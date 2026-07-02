"""Shared quantity-aware FIFO matcher for BUY/SELL fill pairing.

Used by both ``trade_pairing.py`` (the Strategy Performance
closed-trades rollup job) and ``routes/attribution.py`` (the
single-day attribution view). Both previously paired fills by list
*index* (``buys[i]`` with ``sells[i]``), which silently produced
wrong quantity/price and dropped trades whenever a ticker's fills
were not a clean 1:1 sequence -- found 2026-07-02 via KTKBANK: one
BUY of 16 shares exited via three separate SELLs (1, 1, 14) at
different prices on different days; the index-based pairing paired
the full 16-share buy against only the first 1-share sell and
silently dropped the other two sell events (including the 14-share,
₹270.15 exit).
"""
from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


def match_fifo(
    buys: list[dict[str, Any]],
    sells: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Quantity-aware FIFO match of BUY fills against SELL fills.

    Each input dict must carry: ``event_id`` (str), ``qty`` (int,
    positive), ``price`` (float), ``ts_ns`` (int). Inputs need not
    be pre-sorted -- this function sorts internally by ``ts_ns``.

    Returns a list of closed-lot records, oldest-sell-first, each:
    ``buy_event_id``, ``sell_event_id``, ``qty`` (the quantity this
    lot closes -- may be less than either fill's own qty when a buy
    or sell spans multiple lots), ``buy_price``, ``sell_price``,
    ``buy_ts_ns``, ``sell_ts_ns``.

    A single buy fill larger than the sell that (partially) closes
    it produces multiple output records sharing the same
    ``buy_event_id``, one per consuming sell. Symmetrically, a sell
    larger than the next available buy produces multiple records
    sharing the same ``sell_event_id`` against different buys.

    Unconsumed buy quantity (an open position with no closing sell
    yet) is excluded from the output -- matches the pre-existing
    "unmatched fills are skipped" semantics of both callers. Sell
    quantity left over after the buy queue is exhausted (more sold
    than ever bought -- a genuine data problem, not expected in
    practice) is dropped with a logged warning rather than silently
    fabricated into a phantom trade.
    """
    sorted_buys = sorted(buys, key=lambda b: int(b["ts_ns"]))
    sorted_sells = sorted(sells, key=lambda s: int(s["ts_ns"]))

    buy_queue: list[dict[str, Any]] = [
        {**b, "_remaining": int(b["qty"])} for b in sorted_buys
    ]
    buy_i = 0
    out: list[dict[str, Any]] = []

    for sell in sorted_sells:
        remaining_sell = int(sell["qty"])
        while remaining_sell > 0:
            if buy_i >= len(buy_queue):
                _logger.warning(
                    "match_fifo: sell event_id=%s has %d unit(s) "
                    "with no remaining buy quantity to match -- "
                    "dropped, not fabricated as a trade.",
                    sell["event_id"], remaining_sell,
                )
                break
            buy_lot = buy_queue[buy_i]
            if buy_lot["_remaining"] <= 0:
                buy_i += 1
                continue
            slice_qty = min(remaining_sell, buy_lot["_remaining"])
            out.append({
                "buy_event_id": buy_lot["event_id"],
                "sell_event_id": sell["event_id"],
                "qty": slice_qty,
                "buy_price": float(buy_lot["price"]),
                "sell_price": float(sell["price"]),
                "buy_ts_ns": int(buy_lot["ts_ns"]),
                "sell_ts_ns": int(sell["ts_ns"]),
            })
            buy_lot["_remaining"] -= slice_qty
            remaining_sell -= slice_qty
            if buy_lot["_remaining"] == 0:
                buy_i += 1
    return out
