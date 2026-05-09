"""GET /v1/algo/replay/events — filtered timeline scrubber.

Reads algo.events via DuckDB with filters on mode, type,
strategy_id, ts_date. Powers the Replay tab per spec § 9.1
slice 10 ("event-log timeline scrubber; jump-to-event-type").

Does NOT include /v1/algo/paper/events (Slice 8b) because that
endpoint is paper-only by design and used by the Paper tab.
The Replay tab is cross-mode.
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

_VALID_MODES = {"backtest", "paper"}
_VALID_TYPES = {
    "backtest_run_started",
    "backtest_run_completed",
    "signal_generated",
    "signal_rejected",
    "order_submitted",
    "order_filled",
    "order_cancelled",
    "position_opened",
    "position_closed",
    "risk_breach",
    "broker_connected",
    "broker_disconnected",
    "market_tick",
    "bar_close",
}


def create_replay_router() -> APIRouter:
    router = APIRouter(prefix="/algo/replay", tags=["algo-trading"])

    @router.get("/events")
    async def list_events(
        mode: str | None = Query(default=None),
        type: str | None = Query(default=None, alias="type"),
        strategy_id: UUID | None = Query(default=None),
        ts_date: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """Filtered cross-mode event stream.

        Filter validation is permissive — unknown values map to
        empty result (rather than raising) so the UI doesn't
        bounce on stale chips.
        """
        if mode and mode not in _VALID_MODES:
            return []
        if type and type not in _VALID_TYPES:
            return []

        clauses = ["user_id = ?"]
        params: list[Any] = [str(UUID(user.user_id))]
        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        if type:
            clauses.append("type = ?")
            params.append(type)
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(str(strategy_id))
        if ts_date:
            clauses.append("ts_date = ?")
            params.append(ts_date)
        where = " AND ".join(clauses)
        sql = (
            "SELECT event_id, ts_ns, ts_date, mode, "
            "       strategy_id, type, payload_json "
            "FROM events "
            f"WHERE {where} "
            "ORDER BY ts_ns DESC "
            "LIMIT ?"
        )
        params.append(limit)

        from backend.db.duckdb_engine import query_iceberg_table
        try:
            rows = query_iceberg_table(
                "algo.events", sql, params,
            )
        except FileNotFoundError:
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
                "mode": r["mode"],
                "strategy_id": r.get("strategy_id"),
                "type": r["type"],
                "payload": payload,
            })
        return out

    return router
