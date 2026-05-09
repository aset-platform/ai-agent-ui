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
                session, user_id, request.strategy_id,
            )
        if strategy is None:
            async with _session_factory() as session:
                await repo.mark_failed(
                    session, run_id=run_id,
                    error_text="Strategy not found",
                )
                await session.commit()
            return

        # Build a minimal UserContext for the universe helper.
        user = UserContext(
            user_id=str(user_id), email="", role="pro",
        )
        universe = await resolve_universe(
            user=user, strategy=strategy,
        )

        summary = run_backtest(
            strategy=strategy, request=request,
            user_id=user_id, universe=universe,
        )
        # Stamp run_id from the route — the runner generated its
        # own; we overwrite so the persisted summary matches the
        # row id.
        summary = summary.model_copy(update={"run_id": run_id})

        async with _session_factory() as session:
            await repo.mark_completed(
                session, run_id=run_id, summary=summary,
            )
            await session.commit()

    except Exception as exc:  # noqa: BLE001 — last-resort catch
        _logger.exception("backtest job %s failed: %s", run_id, exc)
        try:
            async with _session_factory() as session:
                await repo.mark_failed(
                    session, run_id=run_id, error_text=str(exc),
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            _logger.exception("failed to record job failure")
