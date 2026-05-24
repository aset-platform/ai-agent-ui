"""Sweep orchestrator + AST mutation helper.

The orchestrator (``run_sweep_job``) is added in a
follow-up task; this module bootstraps with just the
mutation primitive that drives every variant in a sweep.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

_logger = logging.getLogger(__name__)


def _mutate_ast(
    strategy: Any, path: str, value: Any,
) -> Any:
    """Return a deep copy of ``strategy`` with the nested
    field at ``path`` set to ``value``.

    Path is dotted (e.g.
    ``"risk.per_trade.stop_loss_pct"``). Each segment
    must resolve via ``getattr`` on the corresponding
    Pydantic model. If any segment doesn't exist, raises
    ``ValueError`` referencing the failing segment.
    """
    parts = path.split(".")
    if not parts:
        raise ValueError(
            f"empty path: {path!r}",
        )
    new = copy.deepcopy(strategy)
    cur = new
    for seg in parts[:-1]:
        if not hasattr(cur, seg):
            raise ValueError(
                f"cannot resolve path {path!r}: "
                f"segment {seg!r} not found on "
                f"{type(cur).__name__}",
            )
        cur = getattr(cur, seg)
        if cur is None:
            raise ValueError(
                f"cannot resolve path {path!r}: "
                f"segment {seg!r} is None",
            )
    last = parts[-1]
    if not hasattr(cur, last):
        raise ValueError(
            f"cannot resolve path {path!r}: "
            f"final segment {last!r} not found on "
            f"{type(cur).__name__}",
        )
    setattr(cur, last, value)
    return new


# ============================================================
# Sweep orchestrator
# ============================================================

from datetime import datetime, timezone  # noqa: E402
from uuid import UUID  # noqa: E402

from backend.algo.backtest.runs_repo import (  # noqa: E402
    BacktestRunsRepo,
)
from backend.algo.backtest.sweep_pbo import (  # noqa: E402
    build_returns_matrix,
    compute_sweep_pbo,
    variant_equity_curve,
)
from backend.algo.backtest.sweep_types import (  # noqa: E402
    SweepConfig,
    SweepResult,
    SweepVariantSummary,
)
from backend.algo.backtest.sweep_whitelist import (  # noqa: E402
    SWEEPABLE_FIELDS,
)
from backend.algo.backtest.walkforward import (  # noqa: E402
    run_walkforward_job,
)


def _session_factory():
    """Lazy import to avoid circular dependency."""
    from backend.algo.backtest.job import (
        _session_factory as f,
    )
    return f


def _windowsummary_to_backtestsummary(
    w: dict[str, Any], strategy_id: UUID,
) -> Any:
    """Adapt a persisted WindowSummary dict (jsonb) to a
    ``BacktestSummary``-shaped object so
    ``variant_equity_curve`` can chain its equity curve.

    ``window_summaries`` is persisted as
    ``list[WindowSummary]`` (not ``list[BacktestSummary]``)
    — different shape (no fees, no fee_rates_version,
    equity_curve items are dicts not EquityPoint objects).
    Reconstruct only the fields ``variant_equity_curve``
    reads: ``period_start`` + ``equity_curve`` (with
    ``bar_date`` + ``equity_inr``). Other
    ``BacktestSummary`` fields are stubbed because the
    sweep aggregator doesn't read them.
    """
    from datetime import date as _date, datetime as _dt
    from decimal import Decimal
    from uuid import uuid4

    from backend.algo.backtest.types import (
        BacktestSummary,
        EquityPoint,
    )

    def _to_date(v: Any) -> _date:
        if isinstance(v, _date):
            return v
        return _date.fromisoformat(str(v))

    test_start = _to_date(w.get("test_start"))
    test_end = _to_date(w.get("test_end"))
    raw_curve = w.get("equity_curve") or []
    pts = [
        EquityPoint(
            bar_date=_to_date(p.get("bar_date")),
            equity_inr=Decimal(str(p.get("equity_inr"))),
        )
        for p in raw_curve
    ]
    initial = (
        pts[0].equity_inr if pts else Decimal("100000")
    )
    final = pts[-1].equity_inr if pts else initial

    return BacktestSummary(
        run_id=uuid4(),
        strategy_id=strategy_id,
        status="completed",
        period_start=test_start,
        period_end=test_end,
        initial_capital_inr=initial,
        final_equity_inr=final,
        total_pnl_inr=final - initial,
        total_pnl_pct=Decimal(
            str(w.get("total_pnl_pct", "0")),
        ),
        total_fees_inr=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate_pct=Decimal(
            str(w.get("win_rate_pct", "0")),
        ),
        max_drawdown_pct=Decimal(
            str(w.get("max_drawdown_pct", "0")),
        ),
        started_at=_dt.now(),
        completed_at=_dt.now(),
        fee_rates_version="sweep-adapter",
        equity_curve=pts,
    )


async def run_sweep_job(
    *,
    sweep_run_id: UUID,
    user_id: UUID,
    config: SweepConfig,
    base_strategy: Any,
    universe: list[str],
) -> None:
    """Serial sweep orchestrator. NEVER raises.

    For each value V in config.swept_values:
      1. Deep-copy base_strategy AST + mutate field.
      2. Create child walkforward row with
         parent_sweep_id=sweep_run_id.
      3. Call run_walkforward_job(...) and await.
      4. Record variant outcome.

    After all variants:
      5. Pull each variant's summary_json from PG.
      6. Chain per-window equity curves into one variant
         curve via variant_equity_curve.
      7. Align on common dates → (T, N) returns matrix.
      8. Compute cross_variant_pbo.
      9. Rank by per-variant Sharpe.
     10. Write SweepResult to sweep parent's summary_json.
     11. Mark sweep parent 'completed' (or 'failed' if
         < 2 variants survived).
    """
    field_meta = SWEEPABLE_FIELDS.get(config.swept_field)
    if field_meta is None:
        # Routes validate first — defensive only.
        _logger.error(
            "run_sweep_job: unknown swept_field %r",
            config.swept_field,
        )
        return

    factory_fn = _session_factory()
    repo = BacktestRunsRepo()

    # Mark sweep parent running
    async with factory_fn() as session:
        await repo.mark_running(
            session, run_id=sweep_run_id,
        )
        await session.commit()

    variant_outcomes: list[
        tuple[int, Any, UUID, str, str | None]
    ] = []

    from backend.algo.backtest.walkforward import (
        WalkForwardConfig,
    )

    for idx, value in enumerate(config.swept_values):
        mutated = _mutate_ast(
            base_strategy, field_meta.path, value,
        )

        # Create child walkforward row
        async with factory_fn() as session:
            child = await repo.create_pending(
                session,
                user_id=user_id,
                strategy_id=config.base_strategy_id,
                period_start=config.period_start,
                period_end=config.period_end,
                mode="walkforward",
                parent_sweep_id=sweep_run_id,
            )
            await session.commit()

        wf_config = WalkForwardConfig(
            strategy_id=config.base_strategy_id,
            period_start=config.period_start,
            period_end=config.period_end,
            train_days=config.train_days,
            test_days=config.test_days,
            step_days=config.step_days,
            initial_capital_inr=config.initial_capital_inr,
            regime_stratified=config.regime_stratified,
            interval_sec=config.interval_sec,
        )

        try:
            await run_walkforward_job(
                walkforward_run_id=child.run_id,
                user_id=user_id,
                config=wf_config,
                strategy=mutated,
                universe=universe,
            )
            variant_outcomes.append(
                (idx, value, child.run_id,
                 "completed", None),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "sweep variant %d (value=%s) crashed: %s",
                idx, value, exc, exc_info=True,
            )
            async with factory_fn() as session:
                await repo.mark_failed(
                    session,
                    run_id=child.run_id,
                    error_text=str(exc)[:500],
                )
                await session.commit()
            variant_outcomes.append(
                (idx, value, child.run_id, "failed",
                 str(exc)[:500]),
            )

    # Aggregate variants — wrap in try/except so any
    # aggregation failure (schema drift, NaN, divide-by-zero,
    # PG hiccup) marks the sweep parent failed instead of
    # leaving it stranded in 'running'. Honors the
    # "NEVER raises" contract.
    try:
        survived = [
            o for o in variant_outcomes
            if o[3] == "completed"
        ]
        if len(survived) < 2:
            async with factory_fn() as session:
                await repo.mark_failed(
                    session,
                    run_id=sweep_run_id,
                    error_text=(
                        "Need >= 2 completed variants for "
                        "PBO; only "
                        f"{len(survived)} survived"
                    ),
                )
                await session.commit()
            return

        # Pull each variant's summary_json
        async with factory_fn() as session:
            children_rows = (
                await repo.list_children_of_sweep(
                    session, sweep_run_id=sweep_run_id,
                )
            )

        wf_by_id = {r["id"]: r for r in children_rows}

        from decimal import Decimal
        import numpy as np

        variant_summaries: list[SweepVariantSummary] = []
        variant_curves: list[list[tuple[Any, Any]]] = []

        for idx, value, wf_id, status, err in (
            variant_outcomes
        ):
            row = wf_by_id.get(wf_id, {})
            sj = row.get("summary_json") or {}
            if status != "completed" or not sj:
                variant_summaries.append(
                    SweepVariantSummary(
                        variant_index=idx,
                        swept_value=value,
                        walkforward_run_id=wf_id,
                        avg_pnl_pct=Decimal("0"),
                        avg_win_rate_pct=Decimal("0"),
                        avg_max_drawdown_pct=Decimal("0"),
                        sharpe=Decimal("0"),
                        dsr=Decimal("0"),
                        n_trades=0,
                        status=status,
                        error_text=err,
                    ),
                )
                continue

            ws_raw = sj.get("window_summaries", [])
            ws = [
                _windowsummary_to_backtestsummary(
                    w,
                    strategy_id=config.base_strategy_id,
                )
                for w in ws_raw
            ]

            curve = variant_equity_curve(
                ws, config.initial_capital_inr,
            )
            variant_curves.append(curve)

            # Per-variant annualised Sharpe
            eq = np.array(
                [float(v) for _, v in curve],
                dtype=float,
            )
            if eq.size >= 2:
                rets = np.diff(eq) / eq[:-1]
                rets = np.where(
                    np.isfinite(rets), rets, 0.0,
                )
                mu = rets.mean()
                sigma = rets.std(ddof=0)
                sharpe = (
                    float((mu / sigma) * (252 ** 0.5))
                    if sigma > 1e-12 else 0.0
                )
            else:
                sharpe = 0.0

            # Aggregate metrics live under the nested
            # `aggregate` key (WalkForwardAggregate), NOT
            # at the top level of summary_json.
            agg = sj.get("aggregate") or {}

            variant_summaries.append(
                SweepVariantSummary(
                    variant_index=idx,
                    swept_value=value,
                    walkforward_run_id=wf_id,
                    avg_pnl_pct=Decimal(
                        str(agg.get("avg_pnl_pct", "0")),
                    ),
                    avg_win_rate_pct=Decimal(
                        str(agg.get(
                            "avg_win_rate_pct", "0",
                        )),
                    ),
                    avg_max_drawdown_pct=Decimal(
                        str(agg.get(
                            "avg_max_drawdown_pct", "0",
                        )),
                    ),
                    sharpe=Decimal(
                        str(round(sharpe, 3)),
                    ),
                    dsr=Decimal(
                        str(agg.get("dsr", "0")),
                    ),
                    # WindowSummary doesn't carry
                    # total_trades; sum defensively from
                    # the raw dicts (0 when absent).
                    n_trades=int(sum(
                        int(d.get("total_trades", 0) or 0)
                        for d in ws_raw
                    )),
                    status="completed",
                    error_text=None,
                ),
            )

        # Compute cross-variant PBO
        R, _ = build_returns_matrix(variant_curves)
        pbo = compute_sweep_pbo(R)

        # Winner by Sharpe
        completed_summaries = [
            s for s in variant_summaries
            if s.status == "completed"
        ]
        if completed_summaries:
            winner = max(
                completed_summaries,
                key=lambda s: s.sharpe,
            )
            winner_idx = winner.variant_index
        else:
            winner_idx = None

        sweep_result = SweepResult(
            run_id=sweep_run_id,
            base_strategy_id=config.base_strategy_id,
            swept_field=config.swept_field,
            swept_values=list(config.swept_values),
            variants=variant_summaries,
            cross_variant_pbo=pbo,
            returns_matrix_shape=(
                int(R.shape[0]),
                int(R.shape[1]),
            ),
            winner_variant_index=winner_idx,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            status="completed",
        )

        # Persist
        from sqlalchemy import text as _sa_text
        async with factory_fn() as session:
            await session.execute(
                _sa_text(
                    "UPDATE algo.runs SET "
                    "status='completed', "
                    "completed_at=:ca, "
                    "summary_json=CAST(:sj AS jsonb) "
                    "WHERE id = :id",
                ),
                {
                    "id": sweep_run_id,
                    "ca": sweep_result.completed_at,
                    "sj": sweep_result.model_dump_json(),
                },
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "run_sweep_job aggregation failed for "
            "sweep_run_id=%s: %s",
            sweep_run_id, exc, exc_info=True,
        )
        try:
            async with factory_fn() as session:
                await repo.mark_failed(
                    session,
                    run_id=sweep_run_id,
                    error_text=(
                        "Sweep aggregation failed: "
                        f"{str(exc)[:400]}"
                    ),
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            _logger.error(
                "run_sweep_job: failed to mark sweep %s "
                "failed after aggregation crash",
                sweep_run_id, exc_info=True,
            )
        return
