"""Async background coroutine that runs a backtest end-to-end
and reflects status transitions in algo.runs.

Lifecycle:
    pending  ─create_pending─►  pending  (sync, before this call)
    pending  ─mark_running────►  running
    running  ─mark_completed──►  completed (summary_json filled)
    running  ─mark_failed─────►  failed    (error_text filled)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from uuid import UUID

from auth.models import UserContext
from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.runs_repo import BacktestRunsRepo
from backend.algo.backtest.types import BacktestRequest
from backend.algo.backtest.universe import resolve_universe
from backend.algo.strategy.repo import get_strategy

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def _session_factory():
    """Wraps the lazy import so tests can patch it cleanly."""
    from backend.db.engine import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        yield session


async def run_backtest_job(
    *,
    run_id: UUID,
    user_id: UUID,
    request: BacktestRequest,
) -> None:
    """Execute the backtest in the background. NEVER raises —
    every error path writes via mark_failed."""
    repo = BacktestRunsRepo()
    try:
        async with _session_factory() as session:
            await repo.mark_running(session, run_id=run_id)
            await session.commit()

        async with _session_factory() as session:
            strategy = await get_strategy(
                session,
                user_id,
                request.strategy_id,
            )
        if strategy is None:
            async with _session_factory() as session:
                await repo.mark_failed(
                    session,
                    run_id=run_id,
                    error_text="Strategy not found",
                )
                await session.commit()
            return

        # Build a minimal UserContext for the universe helper.
        user = UserContext(
            user_id=str(user_id),
            email="",
            role="pro",
        )
        universe = await resolve_universe(
            user=user,
            strategy=strategy,
        )

        # ASETPLTFRM-433 — drop tickers whose OHLCV history is
        # shorter than the longest indicator warmup window the
        # strategy references. Without this filter the runner
        # would silently skip those (ticker, bar) combos via the
        # KeyError catch at runner.py:647-651 (Feature not in
        # context). Pre-filtering closes that hole at the
        # universe layer.
        from backend.algo.backtest.universe import (
            filter_warmup_eligible,
        )
        from backend.algo.strategy.feature_warmup import (
            compute_strategy_warmup_days,
        )

        warmup_days = compute_strategy_warmup_days(
            strategy.root.model_dump(by_alias=True),
        )
        pre_warmup_count = len(universe)
        universe = filter_warmup_eligible(
            universe,
            period_start=request.period_start,
            warmup_days=warmup_days,
        )
        _logger.info(
            "backtest %s: warmup_filter dropped %d tickers "
            "(warmup=%d bars, period_start=%s) → %d remain",
            run_id,
            pre_warmup_count - len(universe),
            warmup_days,
            request.period_start.isoformat(),
            len(universe),
        )

        # ASETPLTFRM-400 slice 3 — auto-derive ``interval_sec`` from
        # the strategy's ``schedule.interval``. The UI/API client
        # doesn't have to pass ``interval_sec`` explicitly: an MIS
        # strategy with ``schedule.interval = "15m"`` auto-runs at
        # the intraday cadence. Explicit ``interval_sec`` in the
        # request still wins (operator override).
        # Defensive ``getattr`` chain — older test fixtures + the
        # legacy ``Strategy`` shape sometimes lack ``schedule``;
        # falling through to daily is the safe default.
        if request.interval_sec == 86400:
            schedule = getattr(strategy, "schedule", None)
            interval_str = getattr(schedule, "interval", "1d")
            interval_map = {
                "1d": 86400,
                "15m": 900,
                "5m": 300,
                "1m": 60,
            }
            derived = interval_map.get(interval_str, 86400)
            if derived != 86400:
                _logger.info(
                    "backtest %s — auto-derived interval_sec=%d "
                    "from strategy.schedule.interval=%s",
                    run_id,
                    derived,
                    interval_str,
                )
                request = request.model_copy(
                    update={"interval_sec": derived},
                )

        summary = run_backtest(
            strategy=strategy,
            request=request,
            user_id=user_id,
            universe=universe,
        )
        # Stamp run_id from the route — the runner generated its
        # own; we overwrite so the persisted summary matches the
        # row id.
        summary = summary.model_copy(update={"run_id": run_id})

        async with _session_factory() as session:
            await repo.mark_completed(
                session,
                run_id=run_id,
                summary=summary,
            )
            await session.commit()

    except Exception as exc:  # noqa: BLE001 — last-resort catch
        _logger.exception("backtest job %s failed: %s", run_id, exc)
        try:
            async with _session_factory() as session:
                await repo.mark_failed(
                    session,
                    run_id=run_id,
                    error_text=str(exc),
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            _logger.exception("failed to record job failure")
