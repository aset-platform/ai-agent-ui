"""POST /v1/algo/backtest/run — synchronous v1.

v1 runs the backtest inline (small data, ~1-2s for 30 bars on
~10 tickers) and returns the summary directly. Slice 7b adds an
async-job wrapper that returns ``run_id`` immediately and lets
the UI poll ``GET /runs/{id}``.

GET /v1/algo/backtest/runs/{run_id} returns the persisted
summary from algo.runs. v1 stores summaries in-memory keyed
on run_id (a tiny module-level dict) — Slice 7b promotes to
algo.runs persistence + MinIO artifact upload.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import (
    BacktestRequest,
    BacktestSummary,
)
from backend.algo.strategy.repo import get_strategy

_logger = logging.getLogger(__name__)

# v1 in-memory store keyed on run_id. Slice 7b moves this to
# algo.runs PG row + MinIO artifact bundle.
_RUNS: dict[UUID, BacktestSummary] = {}


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def create_backtest_router() -> APIRouter:
    router = APIRouter(prefix="/algo/backtest", tags=["algo-trading"])

    @router.post("/run", response_model=BacktestSummary)
    async def run_endpoint(
        body: BacktestRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> BacktestSummary:
        factory = _get_session_factory()
        async with factory() as session:
            strategy = await get_strategy(
                session, UUID(user.user_id), body.strategy_id,
            )
        if strategy is None:
            raise HTTPException(
                status_code=404, detail="Strategy not found",
            )

        # v1 universe = the strategy's stored universe.scope as
        # an opaque list. Slice 7b resolves to the user's actual
        # watchlist ∪ holdings via the existing _scoped_tickers
        # helper from insights_routes.
        universe: list[str] = []  # Resolved by caller in v2

        try:
            summary = run_backtest(
                strategy=strategy,
                request=body,
                user_id=UUID(user.user_id),
                universe=universe,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            _logger.exception("backtest run failed: %s", exc)
            raise HTTPException(
                status_code=500, detail="Backtest run failed",
            )

        _RUNS[summary.run_id] = summary
        return summary

    @router.get(
        "/runs/{run_id}", response_model=BacktestSummary,
    )
    async def get_run(
        run_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> BacktestSummary:
        summary = _RUNS.get(run_id)
        if summary is None:
            raise HTTPException(
                status_code=404, detail="Run not found",
            )
        return summary

    return router
