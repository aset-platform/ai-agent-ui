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
from backend.algo.backtest.universe import resolve_universe
from backend.algo.backtest.walkforward import (
    WalkForwardConfig,
    WalkForwardResult,
    run_walkforward_job,
)
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
        factory = _get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, strategy_id, status, period_start, "
                    "period_end, started_at, completed_at, "
                    "summary_json, error_text "
                    "FROM algo.runs "
                    "WHERE id = :id AND user_id = :uid "
                    "  AND mode = 'walkforward'"
                ),
                {"id": run_id, "uid": user_id},
            )
            row = result.mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404, detail="Walk-forward run not found",
            )

        # Completed run: summary_json holds WalkForwardResult shape
        if row["summary_json"] is not None:
            try:
                return WalkForwardResult.model_validate(
                    row["summary_json"],
                )
            except Exception:  # noqa: BLE001
                pass

        # Pending / running: return minimal in-progress shape
        return WalkForwardResult(
            walkforward_run_id=str(run_id),
            strategy_id=str(row["strategy_id"]),
            status=row["status"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            train_days=0,
            test_days=0,
            step_days=0,
            error_text=row["error_text"],
        )

    @router.get("/runs/{run_id}/gates")
    async def get_gates(
        run_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> dict:
        """Return the 5 quality gate booleans + recommendations.

        Empty `gates_passed` (e.g. legacy V2-2 walkforward predating
        REGIME-5) → all gates considered NOT passed and a single
        recommendation surfaces explaining the upgrade path.
        """
        user_id = UUID(user.user_id)
        factory = _get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT summary_json FROM algo.runs "
                    "WHERE id = :id AND user_id = :uid "
                    "  AND mode = 'walkforward'"
                ),
                {"id": run_id, "uid": user_id},
            )
            row = result.mappings().first()

        if row is None:
            raise HTTPException(404, "Walk-forward run not found")

        summary = row["summary_json"] or {}
        agg = (summary.get("aggregate") or {}) if summary else {}
        gates = agg.get("gates_passed") or {}
        overall_pass = bool(gates) and all(gates.values())

        recs: list[str] = []
        gate_recs = {
            "max_dd_ok": (
                "Max DD exceeded 25%. Tighten stop loss "
                "or reduce position sizing."
            ),
            "recovery_ok": (
                "Recovery > 18 months. Strategy may not "
                "survive prolonged drawdowns."
            ),
            "per_regime_non_neg": (
                "Negative return in at least one regime. "
                "Add applicable_regimes filter or regime-specific "
                "entry conditions."
            ),
            "dsr_ok": (
                "DSR < 0.95 — observed Sharpe likely inflated by "
                "multiple-comparison bias. Reduce hyperparameter "
                "search."
            ),
            "pbo_ok": (
                "PBO > 0.30 — high overfit probability. "
                "Lengthen test window or use simpler model."
            ),
        }
        if not gates:
            recs.append(
                "This walk-forward run predates the 5-gate "
                "validation. Re-run to obtain DSR/PBO/per-regime "
                "metrics and gate status."
            )
        else:
            for key, passed in gates.items():
                if not passed and key in gate_recs:
                    recs.append(gate_recs[key])

        return {
            "gates_passed": gates,
            "overall_pass": overall_pass,
            "recommendations": recs,
        }

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
