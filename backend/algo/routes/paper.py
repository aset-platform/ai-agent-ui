"""GET /v1/algo/paper/events — recent paper-mode events.

Reads algo.events via DuckDB filtered by mode='paper' + the
caller's user_id. Powers the Paper tab's events timeline.

Slice 8b ships this; multi-strategy supervisor + paper run
start/stop endpoints land in 8c.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_logger = logging.getLogger(__name__)


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

    return router
