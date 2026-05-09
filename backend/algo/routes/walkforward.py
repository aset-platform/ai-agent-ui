"""Walk-forward CV API routes.

POST /v1/algo/walkforward/run
    Kicks off an async walk-forward job. Returns 202 with
    walkforward_run_id immediately; UI polls GET until completed.

GET /v1/algo/walkforward/runs/{run_id}
    Returns the WalkForwardResult (aggregate + per-window curves)
    once completed. Returns 200 with status='pending'/'running'
    while the job is in progress.

GET /v1/algo/walkforward/runs
    Lists the user's recent walk-forward runs (newest first,
    paginated).
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, Query,
)
from fastapi.responses import JSONResponse

from sqlalchemy import text

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.backtest.runs_repo import BacktestRunsRepo
from backend.algo.backtest.types import BacktestRun
from backend.algo.backtest.walkforward import (
    WalkForwardConfig,
    WalkForwardResult,
    run_walkforward_job,
)
from backend.algo.backtest.universe import resolve_universe
from backend.algo.strategy.repo import get_strategy

_logger = logging.getLogger(__name__)


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def create_walkforward_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/walkforward", tags=["algo-trading"],
    )

    @router.post("/run", status_code=202)
    async def run_endpoint(
        body: WalkForwardConfig,
        background: BackgroundTasks,
        user: UserContext = Depends(pro_or_superuser),
    ):
        user_id = UUID(user.user_id)
        factory = _get_session_factory()

        # Validate strategy exists before creating the parent row
        async with factory() as session:
            strategy = await get_strategy(
                session, user_id, body.strategy_id,
            )
        if strategy is None:
            raise HTTPException(
                status_code=404, detail="Strategy not found",
            )

        # Resolve universe once — shared across all windows
        from auth.models import UserContext as _UC
        uc = _UC(user_id=str(user_id), email="", role="pro")
        universe = await resolve_universe(
            user=uc, strategy=strategy,
        )

        # Create the parent walk-forward row
        repo = BacktestRunsRepo()
        async with factory() as session:
            row = await repo.create_pending(
                session,
                user_id=user_id,
                strategy_id=body.strategy_id,
                period_start=body.period_start,
                period_end=body.period_end,
                mode="walkforward",
            )
            await session.commit()

        background.add_task(
            run_walkforward_job,
            walkforward_run_id=row.run_id,
            user_id=user_id,
            config=body,
            strategy=strategy,
            universe=universe,
        )
        return JSONResponse(
            status_code=202,
            content={
                "walkforward_run_id": str(row.run_id),
                "status": "pending",
            },
        )

    @router.get(
        "/runs/{run_id}", response_model=WalkForwardResult,
    )
    async def get_run(
        run_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> WalkForwardResult:
        user_id = UUID(user.user_id)
        repo = BacktestRunsRepo()
        factory = _get_session_factory()
        async with factory() as session:
            summary = await repo.get_walkforward_by_id(
                session, user_id=user_id, run_id=run_id,
            )
        if summary is None:
            raise HTTPException(
                status_code=404, detail="Walk-forward run not found",
            )
        # If summary_json was the rich WalkForwardResult shape,
        # decode it; otherwise return a minimal in-progress shape.
        if hasattr(summary, "equity_curve"):
            # Try to decode as WalkForwardResult
            try:
                raw = summary.model_dump(mode="json")
                # Check if it has the walkforward-specific keys
                if "window_summaries" in raw:
                    return WalkForwardResult.model_validate(raw)
            except Exception:  # noqa: BLE001
                pass

        return WalkForwardResult(
            walkforward_run_id=str(run_id),
            strategy_id=str(summary.strategy_id),
            status=summary.status,
            period_start=summary.period_start,
            period_end=summary.period_end,
            train_days=0,
            test_days=0,
            step_days=0,
            error_text=summary.error_text,
        )

    @router.get("/runs", response_model=list[BacktestRun])
    async def list_runs(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[BacktestRun]:
        user_id = UUID(user.user_id)
        repo = BacktestRunsRepo()
        factory = _get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, strategy_id, status, period_start, "
                    "period_end, started_at, completed_at, "
                    "summary_json, error_text "
                    "FROM algo.runs "
                    "WHERE user_id = :uid AND mode = 'walkforward' "
                    "ORDER BY started_at DESC "
                    "LIMIT :lim OFFSET :off"
                ),
                {
                    "uid": user_id,
                    "lim": limit,
                    "off": offset,
                },
            )
            from decimal import Decimal as _D
            rows: list[BacktestRun] = []
            for r in result.mappings().all():
                sj = r["summary_json"]
                rows.append(BacktestRun(
                    run_id=r["id"],
                    strategy_id=r["strategy_id"],
                    status=r["status"],
                    period_start=r["period_start"],
                    period_end=r["period_end"],
                    started_at=r["started_at"],
                    completed_at=r["completed_at"],
                    total_pnl_inr=(
                        _D(str(sj["total_pnl_inr"]))
                        if sj else None
                    ),
                    total_pnl_pct=(
                        _D(str(sj["total_pnl_pct"]))
                        if sj else None
                    ),
                    error_text=r["error_text"],
                ))
        return rows

    return router
