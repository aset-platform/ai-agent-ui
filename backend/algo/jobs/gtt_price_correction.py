"""One-off GTT fill-price correction: patch already-written
order_filled_live events whose price came from the GTT's configured
order (an estimate) rather than Kite's true post-trigger execution
price, for triggers that happened today (order history is
today-scoped -- see docs/superpowers/specs/2026-07-02-closed-trade-
pairing-and-gtt-price-fix-design.md).

Usage::

    docker compose exec backend python \
        scripts/backfill_gtt_price_corrections.py
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pyiceberg.expressions import In

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.db.duckdb_engine import (
    invalidate_metadata,
    query_iceberg_table,
)
from backend.maintenance.backup import verify_or_backup

_logger = logging.getLogger(__name__)

ALGO_EVENTS_TABLE = "algo.events"


def find_true_price(
    orders: list[dict[str, Any]], symbol: str,
) -> float | None:
    """Find the real executed average price for a COMPLETE SELL
    order matching ``symbol`` in Kite's order history. Returns the
    most recent match's ``average_price``, or ``None`` if no
    COMPLETE SELL order matches."""
    matches = [
        o for o in orders
        if o.get("tradingsymbol") == symbol
        and o.get("transaction_type") == "SELL"
        and str(o.get("status") or "").upper() == "COMPLETE"
    ]
    if not matches:
        return None
    latest = max(
        matches,
        key=lambda o: str(o.get("order_timestamp") or ""),
    )
    avg_price = latest.get("average_price")
    if not avg_price:
        return None
    try:
        return float(avg_price)
    except (TypeError, ValueError):
        return None


def correct_gtt_event_prices(
    kite: Any, symbols: list[str], *, dry_run: bool = True,
) -> dict[str, Any]:
    """Find today's gtt_triggered order_filled_live events for
    ``symbols`` whose price_source is 'gtt_config_estimate' (or
    missing, for events written before Task 4 shipped), look up
    the true fill price via Kite's order history, and -- unless
    ``dry_run`` -- scoped-delete + re-append those specific event
    rows with the corrected price and price_source='kite_orders'.

    Iceberg is append-only; "patching" a row is delete-by-key then
    re-insert, never an in-place mutation.
    """
    kc = getattr(kite, "_kc", None)
    if kc is None:
        return {"status": "error", "error": "no _kc on kite client"}
    orders = kc.orders() or []

    sql = (
        "SELECT event_id, ts_ns, ts_date, session_id, user_id, "
        "strategy_id, mode, type, payload_json, written_at "
        "FROM events "
        "WHERE mode = 'live' AND type = 'order_filled_live'"
    )
    rows = query_iceberg_table(ALGO_EVENTS_TABLE, sql, [])

    to_correct: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get("reason") != "gtt_triggered":
            continue
        if payload.get("price_source") == "kite_orders":
            continue  # already corrected
        symbol = payload.get("symbol")
        if symbol not in symbols:
            continue
        true_price = find_true_price(orders, symbol)
        if true_price is None:
            _logger.warning(
                "gtt-price-correction: no matching COMPLETE SELL "
                "order found for %s -- leaving estimate as-is",
                symbol,
            )
            continue
        new_payload = {
            **payload,
            "price": str(true_price),
            "price_source": "kite_orders",
        }
        to_correct.append({**row, "_new_payload": new_payload})

    if dry_run or not to_correct:
        return {
            "status": "dry_run" if dry_run else "ok",
            "would_correct": [
                {
                    "event_id": r["event_id"],
                    "symbol": json.loads(
                        r["payload_json"],
                    ).get("symbol"),
                    "old_price": json.loads(
                        r["payload_json"],
                    ).get("price"),
                    "new_price": r["_new_payload"]["price"],
                }
                for r in to_correct
            ],
        }

    event_ids = [r["event_id"] for r in to_correct]
    verify_or_backup([ALGO_EVENTS_TABLE])

    import pyarrow as pa

    corrected_rows = []
    for r in to_correct:
        row = dict(r)
        row.pop("_new_payload")
        row["payload_json"] = json.dumps(r["_new_payload"])
        corrected_rows.append(row)

    def _do_correction() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(ALGO_EVENTS_TABLE)
        tbl.delete(In("event_id", event_ids))
        schema = tbl.schema().as_arrow()
        arrow = pa.Table.from_pylist(corrected_rows, schema=schema)
        tbl.append(arrow)

    retry_iceberg_op(ALGO_EVENTS_TABLE, _do_correction)
    invalidate_metadata(ALGO_EVENTS_TABLE)
    _logger.info(
        "gtt-price-correction: corrected %d event(s): %s",
        len(to_correct), event_ids,
    )
    return {
        "status": "ok",
        "corrected": [
            {
                "event_id": r["event_id"],
                "symbol": r["_new_payload"]["symbol"],
                "new_price": r["_new_payload"]["price"],
            }
            for r in to_correct
        ],
    }
