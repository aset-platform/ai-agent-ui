"""Redis-backed store for WS-lifecycle (``live-ws``) events.

ws_connected / ws_disconnected / ws_auth_failed / ws_gap_filled /
ws_backpressure_drop are 7-day observability noise. Writing them to the
``algo.events`` Iceberg log produced GBs of snapshot metadata for ~50 MB
of data (incident 2026-06-18). They now live in a per-user Redis sorted
set (score = ts_ns), capped + TTL'd; the events panel reads them here.
Best-effort: absent ``REDIS_URL`` or any Redis error is a silent no-op —
these are not compliance records.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

_logger = logging.getLogger(__name__)

_TTL_S = 7 * 24 * 3600          # 7-day retention (matches Iceberg policy)
_MAX_EVENTS = 1_000             # ring-buffer cap per user
_KEY = "algo:ws-events:{user_id}"


def _client():
    """Return the shared Redis client, or None when unavailable."""
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    try:
        from auth.token_store import get_redis_client

        return get_redis_client(url)
    except Exception:  # noqa: BLE001
        return None


def _ts_date(ts_ns: int) -> str:
    return (
        datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        .date()
        .isoformat()
    )


def record_ws_event(
    *,
    user_id: UUID,
    event_id: str,
    ts_ns: int,
    type_: str,
    strategy_id: str | None,
    payload: dict[str, Any],
) -> bool:
    """Append a WS-lifecycle event to the user's sorted set. Returns
    True if stored, False on no-op. Never raises."""
    client = _client()
    if client is None:
        return False
    key = _KEY.format(user_id=user_id)
    member = json.dumps(
        {
            "event_id": event_id,
            "ts_ns": int(ts_ns),
            "type": type_,
            "strategy_id": strategy_id,
            "payload": payload,
        },
        default=str,
    )
    try:
        pipe = client.pipeline()
        pipe.zadd(key, {member: int(ts_ns)})
        # Keep only the newest _MAX_EVENTS (drop the lowest-scored).
        pipe.zremrangebyrank(key, 0, -(_MAX_EVENTS + 1))
        pipe.expire(key, _TTL_S)
        pipe.execute()
        return True
    except Exception:  # noqa: BLE001
        _logger.warning("ws_event_store: record failed", exc_info=True)
        return False


def read_ws_events(
    *,
    user_id: UUID,
    limit: int = 100,
    offset: int = 0,
    type_: str | None = None,
    since_ts_ns: int | None = None,
) -> list[dict[str, Any]]:
    """Newest-first WS-lifecycle events, shaped like the Iceberg events
    endpoint. No-op → []."""
    client = _client()
    if client is None:
        return []
    key = _KEY.format(user_id=user_id)
    try:
        raw = client.zrevrange(key, 0, _MAX_EVENTS - 1)
    except Exception:  # noqa: BLE001
        _logger.warning("ws_event_store: read failed", exc_info=True)
        return []
    out: list[dict[str, Any]] = []
    for m in raw:
        if isinstance(m, bytes):  # client without decode_responses
            m = m.decode()
        try:
            ev = json.loads(m)
        except Exception:  # noqa: BLE001
            continue
        if type_ is not None and ev.get("type") != type_:
            continue
        ts = int(ev.get("ts_ns", 0))
        if since_ts_ns is not None and ts < since_ts_ns:
            continue
        out.append(
            {
                "event_id": ev.get("event_id"),
                "ts_ns": ts,
                "ts_date": _ts_date(ts),
                "strategy_id": ev.get("strategy_id"),
                "type": ev.get("type"),
                "payload": ev.get("payload", {}),
            }
        )
    return out[offset:offset + limit]
