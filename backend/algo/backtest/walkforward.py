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


class WalkForwardAggregate(BaseModel):
    """Aggregate metrics across all test windows."""
    model_config = ConfigDict(extra="forbid")

    avg_win_rate_pct: Decimal
    avg_pnl_pct: Decimal
    avg_max_drawdown_pct: Decimal
    std_pnl_pct: Decimal  # std-dev of per-window PnL%
    window_count: int
    completed_count: int


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
) -> list[Window]:
    """Return the list of (train, test) windows for the period.

    Trailing partial windows (where test_end > end) are dropped,
    not truncated, to keep per-window metrics comparable.

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
    return windows


# ── Aggregate helper ───────────────────────────────────────────


def _aggregate_windows(
    summaries: list[BacktestSummary],
) -> WalkForwardAggregate:
    """Compute aggregate metrics from completed window summaries."""
    completed = [s for s in summaries if s.status == "completed"]
    if not completed:
        return WalkForwardAggregate(
            avg_win_rate_pct=Decimal("0"),
            avg_pnl_pct=Decimal("0"),
            avg_max_drawdown_pct=Decimal("0"),
            std_pnl_pct=Decimal("0"),
            window_count=len(summaries),
            completed_count=0,
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

    return WalkForwardAggregate(
        avg_win_rate_pct=avg_wr.quantize(Decimal("0.01")),
        avg_pnl_pct=avg_pnl.quantize(Decimal("0.01")),
        avg_max_drawdown_pct=avg_dd.quantize(Decimal("0.01")),
        std_pnl_pct=std_pnl,
        window_count=len(summaries),
        completed_count=len(completed),
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

        windows = walk_windows(
            config.period_start,
            config.period_end,
            train_days=config.train_days,
            test_days=config.test_days,
            step_days=config.step_days,
        )

        _logger.info(
            "walk-forward %s: %d windows over %s → %s",
            walkforward_run_id,
            len(windows),
            config.period_start,
            config.period_end,
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

        aggregate = _aggregate_windows(window_summaries)
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
