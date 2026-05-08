"""GET /v1/algo/performance/runs — strategy-vs-strategy aggregate.

Reads algo.runs (PG) for the caller and aggregates completed
runs per strategy_id: total runs, win rate, average PnL%,
total PnL ₹. Powers the Performance tab table per spec § 9.1
slice 9 ("cohort comparison; strategy-vs-strategy diff").
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_logger = logging.getLogger(__name__)


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def create_performance_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/performance", tags=["algo-trading"],
    )

    @router.get("/runs")
    async def list_runs(
        limit: int = Query(50, ge=1, le=200),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """Recent algo.runs rows for the caller (any mode), newest
        first. The frontend can group by strategy_id client-side
        for the strategy-vs-strategy diff."""
        factory = _get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT r.id, r.strategy_id, "
                    "       s.name AS strategy_name, "
                    "       r.mode, r.status, "
                    "       r.period_start, r.period_end, "
                    "       r.started_at, r.completed_at, "
                    "       r.summary_json "
                    "FROM algo.runs r "
                    "LEFT JOIN algo.strategies s "
                    "  ON s.id = r.strategy_id "
                    "WHERE r.user_id = :uid "
                    "ORDER BY r.started_at DESC "
                    "LIMIT :lim"
                ),
                {"uid": UUID(user.user_id), "lim": limit},
            )
            rows = result.mappings().all()

        out: list[dict[str, Any]] = []
        for r in rows:
            sj: dict | None = r["summary_json"]
            out.append({
                "run_id": str(r["id"]),
                "strategy_id": str(r["strategy_id"]),
                "strategy_name": r["strategy_name"] or "Unknown",
                "mode": r["mode"],
                "status": r["status"],
                "period_start": (
                    r["period_start"].isoformat()
                    if r["period_start"] else None
                ),
                "period_end": (
                    r["period_end"].isoformat()
                    if r["period_end"] else None
                ),
                "started_at": r["started_at"].isoformat(),
                "completed_at": (
                    r["completed_at"].isoformat()
                    if r["completed_at"] else None
                ),
                "total_pnl_inr": (
                    str(Decimal(str(sj["total_pnl_inr"])))
                    if sj else None
                ),
                "total_pnl_pct": (
                    str(Decimal(str(sj["total_pnl_pct"])))
                    if sj else None
                ),
                "total_trades": (
                    int(sj["total_trades"])
                    if sj and "total_trades" in sj else None
                ),
                "win_rate_pct": (
                    str(Decimal(str(sj["win_rate_pct"])))
                    if sj and "win_rate_pct" in sj else None
                ),
                "max_drawdown_pct": (
                    str(Decimal(str(sj["max_drawdown_pct"])))
                    if sj and "max_drawdown_pct" in sj else None
                ),
            })
        return out

    return router
