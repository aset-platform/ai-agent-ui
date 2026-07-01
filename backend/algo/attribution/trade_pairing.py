"""FIFO buy/sell fill-pairing for closed-trade reconstruction.

Fresh implementation modeled on the single-day pairing logic in
``routes/attribution.py``, adapted for the Strategy Performance
closed-trades rollup job (backend/algo/jobs/closed_trades_rollup.py):

* Buckets by ``(strategy_id, ticker)`` instead of ``ticker``-only,
  so two strategies trading the same ticker never cross-pair.
* Captures ``dry_run`` and both fill ``event_id``s (needed for the
  idempotent upsert key into ``algo.closed_trades``).
* Does not enrich with signal/regime context — that's specific to
  the single-day attribution view and out of scope here.

See ``docs/superpowers/specs/2026-07-01-algo-strategy-performance-design.md``
for why this is a fresh module rather than an extraction from
``routes/attribution.py``.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

_logger = logging.getLogger(__name__)

_FILL_TYPES = ("order_filled", "order_filled_live")


def pair_fills_by_strategy_and_ticker(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """FIFO-pair BUY/SELL fills into closed trades.

    ``events`` rows must carry: ``event_id``, ``strategy_id``,
    ``type``, ``payload_json``, ``ts_ns``. Only
    ``order_filled`` / ``order_filled_live`` rows are considered;
    everything else (signals, GTT events, etc.) is ignored.

    Fill payloads carry ``symbol`` (no ``.NS`` suffix, e.g.
    ``"ITC"``); some legacy rows may carry ``ticker`` instead
    (with the suffix) — both are normalised to the same canonical
    symbol.

    Unmatched fills (an open position with no closing SELL yet)
    are skipped — they are not "closed trades".
    """
    fills_by_key: dict[tuple[str, str], list[dict]] = {}
    for ev in events:
        if ev.get("type") not in _FILL_TYPES:
            continue
        try:
            payload = json.loads(ev.get("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            _logger.warning(
                "trade_pairing: unparseable payload_json for "
                "event_id=%s", ev.get("event_id"),
            )
            continue
        raw_sym = payload.get("symbol") or payload.get("ticker") or ""
        sym = str(raw_sym).upper().removesuffix(".NS")
        if not sym:
            continue
        strategy_id = str(ev.get("strategy_id") or "")
        key = (strategy_id, sym)
        fills_by_key.setdefault(key, []).append(
            {**ev, "_payload": payload},
        )

    out: list[dict[str, Any]] = []
    for (strategy_id, sym), fills in fills_by_key.items():
        buys = sorted(
            (f for f in fills if f["_payload"].get("side") == "BUY"),
            key=lambda f: int(f.get("ts_ns") or 0),
        )
        sells = sorted(
            (f for f in fills if f["_payload"].get("side") == "SELL"),
            key=lambda f: int(f.get("ts_ns") or 0),
        )
        for i in range(min(len(buys), len(sells))):
            buy_fill, sell_fill = buys[i], sells[i]
            avg_price = float(
                buy_fill["_payload"].get("fill_price") or 0,
            )
            fill_price = float(
                sell_fill["_payload"].get("fill_price") or 0,
            )
            qty = int(buy_fill["_payload"].get("qty") or 0)
            realised_pnl_inr = (fill_price - avg_price) * qty
            return_pct = (
                (fill_price - avg_price) / avg_price * 100
                if avg_price else 0.0
            )
            out.append({
                "strategy_id": strategy_id or None,
                "ticker": sym,
                "qty": qty,
                "avg_price": avg_price,
                "fill_price": fill_price,
                "opened_at": _ts_ns_to_date(int(buy_fill["ts_ns"])),
                "closed_at": _ts_ns_to_date(int(sell_fill["ts_ns"])),
                "opened_at_ts_ns": int(buy_fill["ts_ns"]),
                "closed_at_ts_ns": int(sell_fill["ts_ns"]),
                "realised_pnl_inr": realised_pnl_inr,
                "return_pct": return_pct,
                "exit_reason": (
                    sell_fill["_payload"].get("exit_reason")
                    or "signal"
                ),
                "dry_run": bool(
                    sell_fill["_payload"].get("dry_run", False),
                ),
                "buy_event_id": buy_fill.get("event_id"),
                "sell_event_id": sell_fill.get("event_id"),
            })
    return out


def _ts_ns_to_date(ts_ns: int) -> date:
    return datetime.fromtimestamp(
        ts_ns / 1_000_000_000, tz=timezone.utc,
    ).date()
