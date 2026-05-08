"""Paper-trading routes.

Slice 8b: GET /events (events timeline).
Slice 8c: POST /runs (start), DELETE /runs/{strategy_id} (stop),
          GET /runs (list active).
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_logger = logging.getLogger(__name__)


class StartRunRequest(BaseModel):
    strategy_id: UUID
    fixture_path: str = Field(min_length=1, max_length=200)
    initial_capital_inr: Decimal = Field(
        default=Decimal("100000.00"), ge=Decimal("1000.00"),
    )


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def create_paper_router() -> APIRouter:
    router = APIRouter(prefix="/algo/paper", tags=["algo-trading"])

    @router.get("/events")
    async def list_events(
        limit: int = Query(100, ge=1, le=500),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """Recent mode='paper' events for the caller (newest first)."""
        from backend.db.duckdb_engine import query_iceberg_table
        sql = (
            "SELECT event_id, ts_ns, ts_date, "
            "       strategy_id, type, payload_json "
            "FROM events "
            "WHERE user_id = ? AND mode = 'paper' "
            "ORDER BY ts_ns DESC "
            "LIMIT ?"
        )
        try:
            rows = query_iceberg_table(
                "algo.events", sql,
                [str(UUID(user.user_id)), limit],
            )
        except FileNotFoundError:
            # No events yet — algo.events table empty.
            return []

        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:  # noqa: BLE001
                payload = {}
            out.append({
                "event_id": r["event_id"],
                "ts_ns": int(r["ts_ns"]),
                "ts_date": r["ts_date"],
                "strategy_id": r.get("strategy_id"),
                "type": r["type"],
                "payload": payload,
            })
        return out

    @router.post("/runs", status_code=201)
    async def start_run(
        body: StartRunRequest,
        user: UserContext = Depends(pro_or_superuser),
    ):
        from backend.algo.paper.kill_switch_repo import (
            KillSwitchRepo,
        )
        from backend.algo.paper.supervisor import (
            build_replay_source, get_supervisor,
        )
        from backend.algo.redis_async import get_async_redis
        from backend.algo.strategy.repo import get_strategy

        user_id = UUID(user.user_id)
        factory = _get_session_factory()
        async with factory() as session:
            strategy = await get_strategy(
                session, user_id, body.strategy_id,
            )
        if strategy is None:
            raise HTTPException(
                status_code=404, detail="Strategy not found",
            )

        # Build the tick source (validates fixture path).
        try:
            source = build_replay_source(body.fixture_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ks_repo = KillSwitchRepo(redis_client=get_async_redis())
        kill_active = await ks_repo.is_active(user_id)

        sv = get_supervisor()
        try:
            row = await sv.start_run(
                user_id=user_id,
                strategy=strategy,
                source=source,
                initial_capital_inr=body.initial_capital_inr,
                kill_switch_active=kill_active,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return row

    @router.delete("/runs/{strategy_id}")
    async def stop_run(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ):
        from backend.algo.paper.supervisor import get_supervisor

        sv = get_supervisor()
        stopped = await sv.stop_run(
            user_id=UUID(user.user_id), strategy_id=strategy_id,
        )
        if not stopped:
            raise HTTPException(
                status_code=404, detail="No active run found",
            )
        return {"stopped": True}

    @router.get("/runs")
    async def list_runs(
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        from backend.algo.paper.supervisor import get_supervisor

        sv = get_supervisor()
        return sv.list_active(user_id=UUID(user.user_id))

    return router
