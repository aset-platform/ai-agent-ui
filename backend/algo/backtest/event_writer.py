"""Append-only writer for ``algo.events`` — used by the backtest
runner to flush all events at the end of a run (single Iceberg
commit instead of per-event writes per CLAUDE.md §4.1 #2).

Known event type registry (append-only — never remove):
  Backtest:
    backtest_run_started       — runner.run_backtest()
    backtest_run_completed     — runner.run_backtest()
    signal_rejected            — risk engine gate
    order_filled               — sim_broker.execute()
  Walk-forward (V2-2):
    walkforward_window_started   — walkforward.run_walkforward()
    walkforward_window_completed — walkforward.run_walkforward()
  Paper:
    paper_run_started / paper_run_completed / paper_run_stopped
    paper_order_filled / paper_signal_rejected
  Live (V2-5, future):
    order_submitted_live / order_acknowledged_live
    order_filled_live / order_rejected_live / order_cancelled_live
    ws_connected / ws_disconnected / ws_gap_filled
  Reconciliation (V2-3, future):
    position_drift_detected / drift_resolved
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


# Explicit Arrow schema matching algo.events Iceberg schema —
# every column nullable=False except strategy_id (which the Iceberg
# spec defines as optional for system events). pa.Table.from_pylist
# alone infers nullable=True everywhere, which PyIceberg rejects
# at write time with a "Mismatch in fields" error.
_EVENTS_ARROW_SCHEMA = pa.schema([
    pa.field("event_id", pa.string(), nullable=False),
    pa.field("ts_ns", pa.int64(), nullable=False),
    pa.field("ts_date", pa.string(), nullable=False),
    pa.field("session_id", pa.string(), nullable=False),
    pa.field("user_id", pa.string(), nullable=False),
    pa.field("strategy_id", pa.string(), nullable=True),
    pa.field("mode", pa.string(), nullable=False),
    pa.field("type", pa.string(), nullable=False),
    pa.field("payload_json", pa.string(), nullable=False),
    pa.field(
        "written_at", pa.timestamp("us"), nullable=False,
    ),
])


def flush_events(rows: list[dict[str, Any]]) -> None:
    """Single Iceberg commit. No-op on empty list."""
    if not rows:
        return
    repo = StockRepository()
    arrow = pa.Table.from_pylist(rows, schema=_EVENTS_ARROW_SCHEMA)
    repo._retry_commit(  # noqa: SLF001 — internal-but-stable
        "algo.events", "append", arrow,
    )
    _logger.info("flushed %d algo.events rows", len(rows))
