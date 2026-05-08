"""Append-only writer for ``algo.events`` — used by the backtest
runner to flush all events at the end of a run (single Iceberg
commit instead of per-event writes per CLAUDE.md §4.1 #2).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa

from stocks.repository import StockRepository

_logger = logging.getLogger(__name__)


def event_row(
    *,
    session_id: UUID,
    user_id: UUID,
    strategy_id: UUID | None,
    mode: str,
    type_: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a single algo.events row dict ready for bulk append."""
    now = datetime.now(timezone.utc)
    ts_ns = int(now.timestamp() * 1_000_000_000)
    return {
        "event_id": str(uuid4()),
        "ts_ns": ts_ns,
        "ts_date": now.date().isoformat(),
        "session_id": str(session_id),
        "user_id": str(user_id),
        "strategy_id": str(strategy_id) if strategy_id else None,
        "mode": mode,
        "type": type_,
        "payload_json": json.dumps(payload, default=str),
        "written_at": now,
    }


def flush_events(rows: list[dict[str, Any]]) -> None:
    """Single Iceberg commit. No-op on empty list."""
    if not rows:
        return
    repo = StockRepository()
    arrow = pa.Table.from_pylist(rows)
    repo._retry_commit(  # noqa: SLF001 — internal-but-stable
        "algo.events", "append", arrow,
    )
    _logger.info("flushed %d algo.events rows", len(rows))
