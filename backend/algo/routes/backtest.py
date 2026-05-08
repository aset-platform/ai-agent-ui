"""POST /v1/algo/backtest/run — async-job wrapper.

POST /run creates a 'pending' run row, schedules a background
task, and returns 202 with run_id immediately. The UI polls
GET /runs/{id} until status ∈ {completed, failed}.

GET /runs lists the user's recent runs (newest first, paginated).
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, Query,
)
from fastapi.responses import JSONResponse

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.backtest.job import run_backtest_job
from backend.algo.backtest.runs_repo import BacktestRunsRepo
from backend.algo.backtest.types import (
    BacktestRequest, BacktestRun, BacktestSummary,
)

_logger = logging.getLogger(__name__)


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def create_backtest_router() -> APIRouter:
    router = APIRouter(prefix="/algo/backtest", tags=["algo-trading"])

    @router.post("/run", status_code=202)
    async def run_endpoint(
        body: BacktestRequest,
        background: BackgroundTasks,
        user: UserContext = Depends(pro_or_superuser),
    ):
        repo = BacktestRunsRepo()
        factory = _get_session_factory()
        async with factory() as session:
            row = await repo.create_pending(
                session,
                user_id=UUID(user.user_id),
                strategy_id=body.strategy_id,
                period_start=body.period_start,
                period_end=body.period_end,
            )
            await session.commit()

        background.add_task(
            run_backtest_job,
            run_id=row.run_id,
            user_id=UUID(user.user_id),
            request=body,
        )
        return JSONResponse(
            status_code=202,
            content={
                "run_id": str(row.run_id),
                "status": "pending",
            },
        )

    @router.get(
        "/runs/{run_id}", response_model=BacktestSummary,
    )
    async def get_run(
        run_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> BacktestSummary:
        repo = BacktestRunsRepo()
        factory = _get_session_factory()
        async with factory() as session:
            summary = await repo.get_by_id(
                session,
                user_id=UUID(user.user_id),
                run_id=run_id,
            )
        if summary is None:
            raise HTTPException(
                status_code=404, detail="Run not found",
            )
        return summary

    @router.get("/runs", response_model=list[BacktestRun])
    async def list_runs(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[BacktestRun]:
        repo = BacktestRunsRepo()
        factory = _get_session_factory()
        async with factory() as session:
            return await repo.list_by_user(
                session,
                user_id=UUID(user.user_id),
                limit=limit, offset=offset,
            )

    return router
