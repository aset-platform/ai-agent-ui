"""POST /v1/algo/sweep/run + GET endpoints.

Routes follow the lift-to-module-level pattern: each
handler delegates to a pure ``_impl`` function unit-
tested without an HTTP harness.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException,
)
from sqlalchemy import text

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.backtest.runs_repo import (
    BacktestRunsRepo,
)
from backend.algo.backtest.sweep import run_sweep_job
from backend.algo.backtest.sweep_types import (
    SweepConfig, SweepResult,
)
from backend.algo.backtest.sweep_whitelist import (
    SWEEPABLE_FIELDS, validate_swept_values,
)
from backend.algo.backtest.universe import (
    resolve_universe,
)
from backend.algo.strategy.repo import get_strategy
from backend.db.engine import get_session_factory

_logger = logging.getLogger(__name__)


def _sweep_fields_impl() -> dict:
    """Return the whitelist for the form dropdown."""
    return {
        "fields": [
            {
                "key": key,
                "label": f.label,
                "field_type": f.field_type,
                "min_value": str(f.min_value),
                "max_value": str(f.max_value),
            }
            for key, f in SWEEPABLE_FIELDS.items()
        ],
    }


async def _sweep_start_impl(
    *,
    body: SweepConfig,
    user_id: UUID,
    background_tasks: BackgroundTasks,
) -> dict:
    """POST /v1/algo/sweep/run handler body.

    Validates the whitelist field + values, loads the
    base strategy, resolves universe, creates the sweep
    parent row, schedules the runner as a background
    task. Returns sweep_run_id immediately.
    """
    try:
        coerced = validate_swept_values(
            body.swept_field, body.swept_values,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=str(exc),
        ) from exc

    factory = get_session_factory()
    async with factory() as session:
        strategy = await get_strategy(
            session, user_id, body.base_strategy_id,
        )
    if strategy is None:
        raise HTTPException(
            status_code=404,
            detail="Base strategy not found",
        )

    uc = UserContext(
        user_id=str(user_id), email="", role="pro",
    )
    universe = await resolve_universe(
        user=uc, strategy=strategy,
    )

    repo = BacktestRunsRepo()
    async with factory() as session:
        row = await repo.create_pending_sweep(
            session,
            user_id=user_id,
            base_strategy_id=body.base_strategy_id,
            period_start=body.period_start,
            period_end=body.period_end,
        )
        await session.commit()

    body_coerced = body.model_copy(
        update={"swept_values": coerced},
    )

    background_tasks.add_task(
        run_sweep_job,
        sweep_run_id=row.run_id,
        user_id=user_id,
        config=body_coerced,
        base_strategy=strategy,
        universe=universe,
    )
    return {
        "sweep_run_id": str(row.run_id),
        "status": "pending",
    }


async def _sweep_get_impl(
    *, run_id: UUID, user_id: UUID,
) -> dict:
    """GET /v1/algo/sweep/runs/{id} handler body."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT id, status, period_start, "
                "period_end, started_at, completed_at, "
                "summary_json, error_text "
                "FROM algo.runs "
                "WHERE id = :id AND user_id = :uid "
                "  AND mode = 'sweep'"
            ),
            {"id": run_id, "uid": user_id},
        )
        row = result.mappings().first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Sweep run not found",
        )

    sj = row["summary_json"]
    if sj is not None:
        result_obj = SweepResult.model_validate(sj)
        return result_obj.model_dump(mode="json")

    return {
        "run_id": str(row["id"]),
        "status": row["status"],
        "period_start": row["period_start"].isoformat(),
        "period_end": row["period_end"].isoformat(),
        "started_at": (
            row["started_at"].isoformat()
            if row["started_at"] else None
        ),
        "completed_at": None,
        "variants": [],
        "cross_variant_pbo": None,
        "returns_matrix_shape": [0, 0],
        "winner_variant_index": None,
        "error_text": row["error_text"],
    }


async def _sweep_list_impl(*, user_id: UUID) -> dict:
    """GET /v1/algo/sweep/runs handler body."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT id, strategy_id, status, "
                "started_at, completed_at "
                "FROM algo.runs "
                "WHERE user_id = :uid "
                "  AND mode = 'sweep' "
                "ORDER BY started_at DESC LIMIT 100"
            ),
            {"uid": user_id},
        )
        rows = result.mappings().all()
    return {
        "sweeps": [
            {
                "run_id": str(r["id"]),
                "base_strategy_id": str(r["strategy_id"]),
                "status": r["status"],
                "started_at": (
                    r["started_at"].isoformat()
                    if r["started_at"] else None
                ),
                "completed_at": (
                    r["completed_at"].isoformat()
                    if r["completed_at"] else None
                ),
            }
            for r in rows
        ],
    }


def create_sweep_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/sweep", tags=["algo-trading"],
    )

    @router.get("/fields")
    async def sweep_fields(
        user: UserContext = Depends(pro_or_superuser),
    ):
        return _sweep_fields_impl()

    @router.post("/run", status_code=202)
    async def sweep_start(
        body: SweepConfig,
        background_tasks: BackgroundTasks,
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _sweep_start_impl(
            body=body,
            user_id=UUID(user.user_id),
            background_tasks=background_tasks,
        )

    @router.get("/runs/{run_id}")
    async def sweep_get(
        run_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _sweep_get_impl(
            run_id=run_id,
            user_id=UUID(user.user_id),
        )

    @router.get("/runs")
    async def sweep_list(
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _sweep_list_impl(
            user_id=UUID(user.user_id),
        )

    return router
