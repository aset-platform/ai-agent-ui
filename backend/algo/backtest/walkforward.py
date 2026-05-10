"""Walk-forward cross-validation harness.

Orchestrates N rolling (train, test) windows over the v1
``run_backtest`` runner. Each window is a standalone
``algo.runs`` row (mode='backtest') linked to a parent
walkforward row (mode='walkforward') via
``parent_walkforward_id``.

Architecture note
-----------------
In a traditional ML walk-forward, the *train* window is used
to fit model parameters; the *test* window is out-of-sample
evaluation. This harness has no trainable model — strategies
are fixed JSON ASTs. We still separate train/test so that:

  1. It is conceptually clear which bars were available to the
     strategy author vs. which bars are OOS evaluation.
  2. Future slices can use the train window for indicator
     warm-up / regime calibration.

For now we run the test window through the full runner and
report its ``BacktestSummary`` as-is. The train window is
noted in the event payload but NOT run through the runner
(no OHLCV data needed for it). This avoids double-running
the same bars and confusing the equity curve.

Walk-forward semantics
----------------------
Given:
    start     — overall period start (inclusive)
    end       — overall period end (inclusive)
    train_days  — number of days in the train sub-window
    test_days   — number of days in the test sub-window
    step_days   — step between consecutive window starts

Window i:
    train_start = start + i * step_days
    train_end   = train_start + train_days - 1
    test_start  = train_end + 1
    test_end    = test_start + test_days - 1

A window is included only if test_end <= end. Trailing
partial windows (test_end > end) are dropped — not
truncated — to keep per-window metrics comparable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import stdev
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.backtest.gates import GateThresholds, evaluate_5_gates
from backend.algo.backtest.metrics import (
    PerRegimeMetrics,
    deflated_sharpe_ratio,
    per_regime_breakdown,
    probability_of_backtest_overfitting,
    recovery_months_from_dd,
)
from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.runs_repo import BacktestRunsRepo
from backend.algo.backtest.types import BacktestRequest, BacktestSummary
from backend.algo.strategy.ast import Strategy

_logger = logging.getLogger(__name__)


# ── Pydantic config model ──────────────────────────────────────


class WalkForwardConfig(BaseModel):
    """Request body for POST /v1/algo/walkforward/run."""
    model_config = ConfigDict(extra="forbid")

    strategy_id: UUID
    period_start: date
    period_end: date
    train_days: int = Field(ge=1)
    test_days: int = Field(ge=1)
    step_days: int = Field(ge=1)
    initial_capital_inr: Decimal = Field(
        default=Decimal("100000.00"), ge=Decimal("1000.00"),
    )
    # REGIME-5: regime-stratified windows + 5-gate thresholds.
    # All optional with safe defaults so existing V2-2 callers
    # keep working unchanged.
    regime_stratified: bool = True
    require_per_regime_non_negative: bool = True
    require_dsr_min: Decimal = Decimal("0.95")
    require_pbo_max: Decimal = Decimal("0.30")
    require_max_dd_pct: Decimal = Decimal("25")
    require_recovery_months_max: int = 18


@dataclass(frozen=True)
class Window:
    """One (train, test) pair produced by ``walk_windows``."""
    index: int           # 0-based
    train_start: date
    train_end: date
    test_start: date
    test_end: date


# ── Walk-forward Pydantic types ────────────────────────────────


class WindowSummary(BaseModel):
    """Per-window aggregate shipped to the frontend."""
    model_config = ConfigDict(extra="forbid")

    window_index: int
    run_id: str
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    status: str
    total_pnl_pct: Decimal | None = None
    win_rate_pct: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    equity_curve: list[dict[str, Any]] = Field(
        default_factory=list,
    )
    error_text: str | None = None


class PerRegimeRow(BaseModel):
    """Per-regime metrics serialised for the API + persistence."""
    model_config = ConfigDict(extra="forbid")

    regime: str
    n_days: int
    cum_return_pct: Decimal
    sharpe: Decimal
    sortino: Decimal
    max_dd_pct: Decimal
    hit_rate: Decimal


class WalkForwardAggregate(BaseModel):
    """Aggregate metrics across all test windows."""
    model_config = ConfigDict(extra="forbid")

    avg_win_rate_pct: Decimal
    avg_pnl_pct: Decimal
    avg_max_drawdown_pct: Decimal
    std_pnl_pct: Decimal  # std-dev of per-window PnL%
    window_count: int
    completed_count: int
    # REGIME-5 additions. All default-empty so any older
    # summary_json blob deserialises cleanly.
    per_regime: list[PerRegimeRow] = Field(default_factory=list)
    dsr: Decimal = Decimal("0")
    pbo: Decimal | None = None
    recovery_months: int = 0
    gates_passed: dict[str, bool] = Field(default_factory=dict)
    regime_stratified: bool = False


class WalkForwardResult(BaseModel):
    """Full result returned by GET /v1/algo/walkforward/runs/{id}."""
    model_config = ConfigDict(extra="forbid")

    walkforward_run_id: str
    strategy_id: str
    status: str
    period_start: date
    period_end: date
    train_days: int
    test_days: int
    step_days: int
    window_summaries: list[WindowSummary] = Field(
        default_factory=list,
    )
    aggregate: WalkForwardAggregate | None = None
    error_text: str | None = None


# ── Core iterator ──────────────────────────────────────────────


def walk_windows(
    start: date,
    end: date,
    *,
    train_days: int,
    test_days: int,
    step_days: int,
    regime_labels: dict[date, str] | None = None,
) -> list[Window]:
    """Return the list of (train, test) windows for the period.

    Trailing partial windows (where test_end > end) are dropped,
    not truncated, to keep per-window metrics comparable.

    REGIME-5: pass ``regime_labels`` (a date → regime mapping
    covering ``[start, end]``) to enforce regime-stratified
    splits. A window is kept only if its TRAIN slice contains at
    least one day of every regime present in the FULL period.
    Pass ``None`` (the default) for the V2-2 behaviour.

    Raises ValueError if:
    - train_days < 1, test_days < 1, or step_days < 1
    - start >= end
    - (train_days + test_days) > total period days  → returns []
    """
    if train_days < 1 or test_days < 1 or step_days < 1:
        raise ValueError(
            "train_days, test_days, step_days must be >= 1"
        )
    if start >= end:
        raise ValueError("start must be before end")

    windows: list[Window] = []
    i = 0
    while True:
        train_start = start + timedelta(days=i * step_days)
        train_end = train_start + timedelta(days=train_days - 1)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=test_days - 1)
        if test_end > end:
            break
        windows.append(Window(
            index=i,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
        ))
        i += 1

    if regime_labels:
        # Universe of regimes seen anywhere in the full period.
        full_period_regimes = {
            lab for d, lab in regime_labels.items()
            if start <= d <= end
        }
        if not full_period_regimes:
            return windows

        def _train_covers_all(w: Window) -> bool:
            seen: set[str] = set()
            cur = w.train_start
            while cur <= w.train_end:
                lab = regime_labels.get(cur)
                if lab is not None:
                    seen.add(lab)
                cur += timedelta(days=1)
            return full_period_regimes.issubset(seen)

        windows = [w for w in windows if _train_covers_all(w)]

    return windows


# ── Aggregate helper ───────────────────────────────────────────


def _q2(x: Decimal | float) -> Decimal:
    """Quantize to 2dp (no NaN propagation)."""
    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return Decimal("0")
        x = Decimal(str(x))
    return x.quantize(Decimal("0.01"))


def _aggregate_windows(
    summaries: list[BacktestSummary],
    *,
    regime_labels: dict[Any, str] | None = None,
    n_trials: int = 1,
    thresholds: GateThresholds | None = None,
    regime_stratified: bool = False,
) -> WalkForwardAggregate:
    """Compute aggregate metrics from completed window summaries.

    REGIME-5 extensions (all kwarg-optional, default no-op):
      regime_labels - day → regime label for the union of all
          test windows. If provided, populates per_regime + the
          per_regime gate.
      n_trials - n strategy variants tried (V2-2 = 1 → DSR
          without multi-comparison deflation, PBO undefined).
      thresholds - GateThresholds override; None → defaults from
          spec §6.1.
      regime_stratified - whether the window iterator was run
          with regime stratification. Stored on the aggregate so
          downstream consumers can show "stratified vs not" in
          the UI.
    """
    completed = [s for s in summaries if s.status == "completed"]
    if not completed:
        return WalkForwardAggregate(
            avg_win_rate_pct=Decimal("0"),
            avg_pnl_pct=Decimal("0"),
            avg_max_drawdown_pct=Decimal("0"),
            std_pnl_pct=Decimal("0"),
            window_count=len(summaries),
            completed_count=0,
            regime_stratified=regime_stratified,
        )

    pnl_pcts = [s.total_pnl_pct for s in completed]
    win_rates = [s.win_rate_pct for s in completed]
    drawdowns = [s.max_drawdown_pct for s in completed]

    n = Decimal(len(completed))
    avg_pnl = sum(pnl_pcts, Decimal("0")) / n
    avg_wr = sum(win_rates, Decimal("0")) / n
    avg_dd = sum(drawdowns, Decimal("0")) / n

    # std-dev requires at least 2 samples
    if len(pnl_pcts) >= 2:
        std_pnl = Decimal(str(
            stdev(float(p) for p in pnl_pcts)
        )).quantize(Decimal("0.01"))
    else:
        std_pnl = Decimal("0")

    # ─── REGIME-5 metrics ─────────────────────────────────────
    # Concat per-window equity curves into a single aggregated
    # curve for per-regime + recovery + DSR/PBO calc.
    full_curve: list[dict[str, Any]] = []
    for s in completed:
        for ep in (s.equity_curve or []):
            full_curve.append({
                "bar_date": ep.bar_date,
                "equity_inr": float(ep.equity_inr),
            })

    # Per-regime breakdown
    per_regime_rows: list[PerRegimeRow] = []
    if regime_labels and full_curve:
        # Normalise label keys to ISO-date strings.
        norm_labels: dict[str, str] = {}
        for k, v in regime_labels.items():
            key = (
                k.isoformat()
                if hasattr(k, "isoformat") else str(k)
            )
            norm_labels[key] = v
        per_regime_metrics: list[PerRegimeMetrics] = (
            per_regime_breakdown(full_curve, norm_labels)
        )
        for m in per_regime_metrics:
            per_regime_rows.append(PerRegimeRow(
                regime=m.regime,
                n_days=m.n_days,
                cum_return_pct=_q2(m.cum_return_pct),
                sharpe=_q2(m.sharpe),
                sortino=_q2(m.sortino),
                max_dd_pct=_q2(m.max_dd_pct),
                hit_rate=_q2(m.hit_rate),
            ))

    # Recovery months (on aggregated equity curve)
    rec_months = recovery_months_from_dd(full_curve)

    # DSR (single-strategy if n_trials=1)
    obs_sharpe = 0.0
    sample_len = max(len(full_curve) - 1, 0)
    if sample_len > 1:
        rets = []
        prev = full_curve[0]["equity_inr"]
        for ep in full_curve[1:]:
            cur = ep["equity_inr"]
            if prev and prev > 0:
                rets.append(cur / prev - 1.0)
            prev = cur
        if rets:
            import math as _math
            mu = sum(rets) / len(rets)
            var = sum((r - mu) ** 2 for r in rets) / len(rets)
            sd = _math.sqrt(var) if var > 0 else 0.0
            if sd > 1e-12:
                obs_sharpe = mu / sd * _math.sqrt(252)
    dsr_val = deflated_sharpe_ratio(
        obs_sharpe=obs_sharpe,
        n_trials=max(n_trials, 1),
        sample_length=sample_len,
    )

    # PBO undefined for single-strategy walkforward. None signals
    # "skip PBO gate" per evaluate_5_gates contract.
    pbo_val: Decimal | None = None

    # 5-gate evaluation
    th = thresholds or GateThresholds()
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=float(avg_dd),
        recovery_months=rec_months,
        per_regime=[
            PerRegimeMetrics(
                regime=r.regime,
                n_days=r.n_days,
                cum_return_pct=float(r.cum_return_pct),
                sharpe=float(r.sharpe),
                sortino=float(r.sortino),
                max_dd_pct=float(r.max_dd_pct),
                hit_rate=float(r.hit_rate),
            )
            for r in per_regime_rows
        ],
        dsr=dsr_val,
        pbo=None,
        thresholds=th,
    )

    return WalkForwardAggregate(
        avg_win_rate_pct=avg_wr.quantize(Decimal("0.01")),
        avg_pnl_pct=avg_pnl.quantize(Decimal("0.01")),
        avg_max_drawdown_pct=avg_dd.quantize(Decimal("0.01")),
        std_pnl_pct=std_pnl,
        window_count=len(summaries),
        completed_count=len(completed),
        per_regime=per_regime_rows,
        dsr=_q2(dsr_val),
        pbo=pbo_val,
        recovery_months=rec_months,
        gates_passed=gates,
        regime_stratified=regime_stratified,
    )


# ── Orchestrator ───────────────────────────────────────────────


async def run_walkforward_job(
    *,
    walkforward_run_id: UUID,
    user_id: UUID,
    config: WalkForwardConfig,
    strategy: Strategy,
    universe: list[str],
) -> None:
    """Execute the walk-forward harness as a background job.

    Lifecycle mirrors ``run_backtest_job``:
      pending  → running  → (per-window backtest loop)
                           → completed / failed

    Each window persists its own ``algo.runs`` row
    (mode='backtest') linked via ``parent_walkforward_id``.
    The parent row (mode='walkforward') stores the aggregate
    ``WalkForwardResult`` in ``summary_json``.

    NEVER raises — all errors are written via mark_failed.
    """
    from backend.algo.backtest.job import _session_factory  # noqa

    repo = BacktestRunsRepo()
    session_id = walkforward_run_id
    events: list[dict[str, Any]] = []
    window_summaries: list[BacktestSummary] = []

    try:
        async with _session_factory() as session:
            await repo.mark_running(
                session, run_id=walkforward_run_id,
            )
            await session.commit()

        # REGIME-5: optionally fetch regime_history once for the
        # full period and use it for both stratified window
        # selection AND per-regime breakdown later. Falls through
        # silently to non-stratified mode when regime_history
        # has no rows for the period (e.g. backtest start 2007
        # but regime_history only goes back 30 days).
        regime_labels: dict[date, str] = {}
        regime_stratified_active = False
        if config.regime_stratified:
            try:
                from backend.algo.regime.repo import (
                    get_regime_history,
                )
                rows = get_regime_history(
                    config.period_start, config.period_end,
                )
                regime_labels = {r.bar_date: r.regime_label for r in rows}
                if regime_labels:
                    regime_stratified_active = True
                else:
                    _logger.warning(
                        "walk-forward %s: regime_history empty for "
                        "%s..%s — falling back to non-stratified",
                        walkforward_run_id,
                        config.period_start, config.period_end,
                    )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "walk-forward %s: regime_history lookup "
                    "failed (%s) — non-stratified fallback",
                    walkforward_run_id, exc,
                )

        windows = walk_windows(
            config.period_start,
            config.period_end,
            train_days=config.train_days,
            test_days=config.test_days,
            step_days=config.step_days,
            regime_labels=(
                regime_labels if regime_stratified_active else None
            ),
        )

        _logger.info(
            "walk-forward %s: %d windows over %s → %s "
            "(stratified=%s)",
            walkforward_run_id,
            len(windows),
            config.period_start,
            config.period_end,
            regime_stratified_active,
        )

        for win in windows:
            # Create a pending child run row
            async with _session_factory() as session:
                child_row = await repo.create_pending(
                    session,
                    user_id=user_id,
                    strategy_id=config.strategy_id,
                    period_start=win.test_start,
                    period_end=win.test_end,
                    mode="backtest",
                    parent_walkforward_id=walkforward_run_id,
                    window_start=win.test_start,
                    window_end=win.test_end,
                )
                await session.commit()

            child_run_id = child_row.run_id

            events.append(event_row(
                session_id=session_id,
                user_id=user_id,
                strategy_id=config.strategy_id,
                mode="walkforward",
                type_="walkforward_window_started",
                payload={
                    "walkforward_run_id": str(walkforward_run_id),
                    "window_index": win.index,
                    "child_run_id": str(child_run_id),
                    "train_start": win.train_start.isoformat(),
                    "train_end": win.train_end.isoformat(),
                    "test_start": win.test_start.isoformat(),
                    "test_end": win.test_end.isoformat(),
                },
            ))

            try:
                async with _session_factory() as session:
                    await repo.mark_running(
                        session, run_id=child_run_id,
                    )
                    await session.commit()

                request = BacktestRequest(
                    strategy_id=config.strategy_id,
                    period_start=win.test_start,
                    period_end=win.test_end,
                    initial_capital_inr=config.initial_capital_inr,
                )
                summary = run_backtest(
                    strategy=strategy,
                    request=request,
                    user_id=user_id,
                    universe=universe,
                )
                # Stamp child_run_id so the summary matches the row
                summary = summary.model_copy(
                    update={"run_id": child_run_id},
                )

                async with _session_factory() as session:
                    await repo.mark_completed(
                        session,
                        run_id=child_run_id,
                        summary=summary,
                    )
                    await session.commit()

                window_summaries.append(summary)

                events.append(event_row(
                    session_id=session_id,
                    user_id=user_id,
                    strategy_id=config.strategy_id,
                    mode="walkforward",
                    type_="walkforward_window_completed",
                    payload={
                        "walkforward_run_id": str(
                            walkforward_run_id
                        ),
                        "window_index": win.index,
                        "child_run_id": str(child_run_id),
                        "total_pnl_pct": str(
                            summary.total_pnl_pct
                        ),
                        "win_rate_pct": str(
                            summary.win_rate_pct
                        ),
                        "max_drawdown_pct": str(
                            summary.max_drawdown_pct
                        ),
                    },
                ))

            except Exception as win_exc:  # noqa: BLE001
                _logger.exception(
                    "walk-forward %s window %d failed: %s",
                    walkforward_run_id, win.index, win_exc,
                )
                async with _session_factory() as session:
                    await repo.mark_failed(
                        session,
                        run_id=child_run_id,
                        error_text=str(win_exc),
                    )
                    await session.commit()

                # Append a failed placeholder so aggregate still
                # counts this window in window_count
                failed_summary = BacktestSummary(
                    run_id=child_run_id,
                    strategy_id=config.strategy_id,
                    status="failed",
                    period_start=win.test_start,
                    period_end=win.test_end,
                    initial_capital_inr=config.initial_capital_inr,
                    final_equity_inr=config.initial_capital_inr,
                    total_pnl_inr=Decimal("0"),
                    total_pnl_pct=Decimal("0"),
                    total_fees_inr=Decimal("0"),
                    total_trades=0,
                    winning_trades=0,
                    losing_trades=0,
                    win_rate_pct=Decimal("0"),
                    max_drawdown_pct=Decimal("0"),
                    started_at=child_row.started_at,
                    completed_at=child_row.started_at,
                    fee_rates_version="n/a",
                    error_text=str(win_exc),
                )
                window_summaries.append(failed_summary)

        # Flush window events in one Iceberg commit
        flush_events(events)
        events = []

        thresholds = GateThresholds(
            max_dd_pct=float(config.require_max_dd_pct),
            recovery_months_max=int(
                config.require_recovery_months_max,
            ),
            require_per_regime_non_negative=(
                config.require_per_regime_non_negative
            ),
            dsr_min=float(config.require_dsr_min),
            pbo_max=float(config.require_pbo_max),
        )
        aggregate = _aggregate_windows(
            window_summaries,
            regime_labels=(
                regime_labels if regime_stratified_active else None
            ),
            n_trials=1,
            thresholds=thresholds,
            regime_stratified=regime_stratified_active,
        )
        window_rows = [
            WindowSummary(
                window_index=win.index,
                run_id=str(s.run_id),
                train_start=windows[win.index].train_start,
                train_end=windows[win.index].train_end,
                test_start=win.test_start,
                test_end=win.test_end,
                status=s.status,
                total_pnl_pct=s.total_pnl_pct,
                win_rate_pct=s.win_rate_pct,
                max_drawdown_pct=s.max_drawdown_pct,
                equity_curve=[
                    {
                        "bar_date": ep.bar_date.isoformat(),
                        "equity_inr": str(ep.equity_inr),
                    }
                    for ep in s.equity_curve
                ],
                error_text=s.error_text,
            )
            for win, s in zip(windows, window_summaries)
        ]

        result = WalkForwardResult(
            walkforward_run_id=str(walkforward_run_id),
            strategy_id=str(config.strategy_id),
            status="completed",
            period_start=config.period_start,
            period_end=config.period_end,
            train_days=config.train_days,
            test_days=config.test_days,
            step_days=config.step_days,
            window_summaries=window_rows,
            aggregate=aggregate,
        )

        # Store on the parent row reusing summary_json
        # WalkForwardResult serialises cleanly as JSONB
        now_utc = datetime.now(timezone.utc)
        parent_summary = BacktestSummary(
            run_id=walkforward_run_id,
            strategy_id=config.strategy_id,
            status="completed",
            period_start=config.period_start,
            period_end=config.period_end,
            initial_capital_inr=config.initial_capital_inr,
            final_equity_inr=config.initial_capital_inr,
            total_pnl_inr=aggregate.avg_pnl_pct,
            total_pnl_pct=aggregate.avg_pnl_pct,
            total_fees_inr=Decimal("0"),
            total_trades=aggregate.completed_count,
            winning_trades=aggregate.completed_count,
            losing_trades=(
                aggregate.window_count - aggregate.completed_count
            ),
            win_rate_pct=aggregate.avg_win_rate_pct,
            max_drawdown_pct=aggregate.avg_max_drawdown_pct,
            started_at=now_utc,
            completed_at=now_utc,
            fee_rates_version="n/a",
            equity_curve=[],
            trade_list=[],
        )

        async with _session_factory() as session:
            await repo.mark_completed(
                session,
                run_id=walkforward_run_id,
                summary=parent_summary,
            )
            await session.commit()

        # Store the rich WalkForwardResult separately in the row
        # via a direct UPDATE on summary_json (mark_completed
        # persists the flat BacktestSummary; we overwrite with the
        # rich walkforward shape so the GET endpoint can decode it).
        async with _session_factory() as session:
            from sqlalchemy import text as _text
            await session.execute(
                _text(
                    "UPDATE algo.runs SET "
                    "summary_json = CAST(:sj AS jsonb) "
                    "WHERE id = :id"
                ),
                {
                    "id": walkforward_run_id,
                    "sj": result.model_dump_json(),
                },
            )
            await session.commit()

        _logger.info(
            "walk-forward %s completed: %d/%d windows ok",
            walkforward_run_id,
            aggregate.completed_count,
            aggregate.window_count,
        )

    except Exception as exc:  # noqa: BLE001
        _logger.exception(
            "walk-forward job %s failed: %s", walkforward_run_id, exc
        )
        if events:
            try:
                flush_events(events)
            except Exception:  # noqa: BLE001
                pass
        try:
            async with _session_factory() as session:
                await repo.mark_failed(
                    session,
                    run_id=walkforward_run_id,
                    error_text=str(exc),
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "failed to record walk-forward job failure"
            )
