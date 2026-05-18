"""Append-only writer for ``algo.events`` — used by the backtest
runner to flush all events at the end of a run (single Iceberg
commit instead of per-event writes per CLAUDE.md §4.1 #2).

Schema-adaptive ts_date handling
--------------------------------
The 2026-05-16 universal-design redesign moved ``ts_date`` from
``StringType`` to ``DateType``.  Code changes ship before the
table migration runs in any given environment, so this writer
inspects the live Iceberg schema once per process and emits the
matching PyArrow column type (``pa.date32()`` for new, ``pa.string()``
for legacy).  After every environment is migrated this dual
path becomes a no-op single branch and can be removed.

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
  Order-safety hardening (2026-05-12 PR #1):
    order_ltp_stale_blocked  — placement blocked: stale LTP gate
    (order_submitted_live now carries the spec §3.6 full payload —
     request / context / response.raw nested blocks; top-level keys
     kite_order_id / dry_run / side / qty / symbol preserved for
     PaperEventsTimeline compatibility.)
  Order-safety hardening (2026-05-12 PR #3):
    order_cancelled_timeout  — _OrderTimeoutWatcher cancelled a
       session-tagged LIMIT after ALGO_ORDER_TTL_S (default 90s)
       still in OPEN / TRIGGER PENDING. Payload: kite_order_id,
       tag, status_at_cancel, age_seconds, ttl_seconds, symbol,
       side, qty, filled_qty, reason.
    order_cancel_failed      — cancellation attempt raised; emitted
       per failure with kite_order_id, status_at_cancel_attempt,
       age_seconds, exc_str. The watcher then continues polling.
  Reconciliation (V2-3):
    position_drift_detected / drift_resolved
  Live decoupling (2026-05-12 ASETPLTFRM-376):
    position_hydrated  — emitted once per leg seeded into
       PositionTracker at LiveRuntime spawn. Payload: symbol, qty,
       avg_price, source ("positions" | "holdings"), product
       ("MIS" | "CNC"), entry_ts (ISO 8601 UTC | null), dry_run.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa

from stocks.repository import StockRepository

_logger = logging.getLogger(__name__)

# Cached at first flush — ``True`` once we've confirmed the live
# Iceberg schema uses ``DateType`` for ``ts_date``.  Resets on
# process restart.  Per-flush table inspection is too expensive
# for the LiveRuntime hot path.
_TS_DATE_IS_NATIVE_DATE: bool | None = None


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
        # ts_date is a ``DateType`` Iceberg column (CLAUDE.md
        # §4.3 #22.b — never ``StringType("YYYY-MM-DD")``) so
        # the MonthTransform partition can prune at scan time.
        # Pass a ``datetime.date`` object; PyArrow / PyIceberg
        # round-trip it through Iceberg's date32 representation.
        "ts_date": now.date(),
        "session_id": str(session_id),
        "user_id": str(user_id),
        "strategy_id": str(strategy_id) if strategy_id else None,
        "mode": mode,
        "type": type_,
        "payload_json": json.dumps(payload, default=str),
        "written_at": now,
    }


def _events_arrow_schema(ts_date_native: bool) -> pa.Schema:
    """Build the explicit PyArrow schema for ``algo.events``.

    ``pa.Table.from_pylist`` alone infers ``nullable=True`` for
    every field, which PyIceberg rejects against the
    ``required=True`` flags in the Iceberg schema.  Passing this
    schema explicitly aligns the writer with the table contract.

    Pass ``ts_date_native=True`` after the 2026-05-16 migration
    (DateType), ``False`` before it (StringType legacy).
    """
    return pa.schema([
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("ts_ns", pa.int64(), nullable=False),
        pa.field(
            "ts_date",
            pa.date32() if ts_date_native else pa.string(),
            nullable=False,
        ),
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


def _detect_ts_date_native_date() -> bool:
    """Inspect the live Iceberg schema once per process and
    cache whether ``ts_date`` is the new ``DateType`` (True) or
    the legacy ``StringType`` (False).
    """
    global _TS_DATE_IS_NATIVE_DATE
    if _TS_DATE_IS_NATIVE_DATE is not None:
        return _TS_DATE_IS_NATIVE_DATE
    try:
        from pyiceberg.types import DateType

        from stocks.create_tables import _get_catalog

        tbl = _get_catalog().load_table("algo.events")
        field = tbl.schema().find_field("ts_date")
        _TS_DATE_IS_NATIVE_DATE = isinstance(
            field.field_type, DateType,
        )
    except Exception as exc:  # noqa: BLE001
        # If the table doesn't exist yet (fresh dev environment),
        # assume the new schema — create_algo_tables() always
        # builds DateType.
        _logger.warning(
            "event_writer: ts_date type detection failed (%s) "
            "— defaulting to DateType",
            exc,
        )
        _TS_DATE_IS_NATIVE_DATE = True
    return _TS_DATE_IS_NATIVE_DATE


def _coerce_ts_date_for_legacy(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """``event_row`` emits ``date`` objects.  Legacy schemas
    (pre-migration) expect ISO strings — coerce them in place
    so the PyArrow schema with ``pa.string()`` accepts the rows.
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        td = r.get("ts_date")
        if isinstance(td, date):
            r = {**r, "ts_date": td.isoformat()}
        out.append(r)
    return out


def flush_events(rows: list[dict[str, Any]]) -> None:
    """Single Iceberg commit. No-op on empty list."""
    if not rows:
        return
    repo = StockRepository()
    native_date = _detect_ts_date_native_date()
    if not native_date:
        rows = _coerce_ts_date_for_legacy(rows)
    arrow = pa.Table.from_pylist(
        rows, schema=_events_arrow_schema(native_date),
    )
    repo._retry_commit(  # noqa: SLF001 — internal-but-stable
        "algo.events", "append", arrow,
    )
    _logger.info("flushed %d algo.events rows", len(rows))
