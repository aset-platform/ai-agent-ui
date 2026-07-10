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

from backend.algo.attribution.fifo_matcher import match_fifo

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

    Strategy-less SELL fills (``strategy_id`` empty/None) are
    attributed to the strategy that holds the open BUY for the same
    ticker. Panic-close SELLs — and any order placed outside the
    LiveRuntime in-flight ledger — are emitted by the postback
    reconciler with ``strategy_id=None`` (no in-flight entry to match
    against). Without this attribution the strategy-less SELL lands in
    a different ``(strategy_id, ticker)`` bucket from its opening BUY
    and never FIFO-pairs, so the exit is silently dropped from
    ``algo.closed_trades`` (found 2026-07-10 — a panic-close left
    three real exits invisible on the Live Performance page).
    """
    # First pass: parse fills once, and record which strategies
    # opened BUYs per ticker (earliest-buy-first, deduped) so a
    # strategy-less SELL can inherit the owning strategy.
    parsed: list[tuple[dict, dict, str]] = []
    _buy_strat_ts: dict[str, list[tuple[int, str]]] = {}
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
        parsed.append((ev, payload, sym))
        if payload.get("side") == "BUY":
            sid = str(ev.get("strategy_id") or "")
            if sid:
                _buy_strat_ts.setdefault(sym, []).append(
                    (int(ev.get("ts_ns") or 0), sid),
                )
    buy_strategies_by_ticker: dict[str, list[str]] = {}
    for sym, pairs in _buy_strat_ts.items():
        ordered: list[str] = []
        for _ts, sid in sorted(pairs):
            if sid not in ordered:
                ordered.append(sid)
        buy_strategies_by_ticker[sym] = ordered

    fills_by_key: dict[tuple[str, str], list[dict]] = {}
    for ev, payload, sym in parsed:
        strategy_id = str(ev.get("strategy_id") or "")
        if not strategy_id and payload.get("side") == "SELL":
            candidates = buy_strategies_by_ticker.get(sym, [])
            if candidates:
                strategy_id = candidates[0]
                if len(candidates) > 1:
                    _logger.warning(
                        "trade_pairing: strategy-less SELL for %s "
                        "(event_id=%s) — %d strategies hold this "
                        "ticker; attributing to earliest-buying "
                        "strategy %s",
                        sym, ev.get("event_id"),
                        len(candidates), strategy_id,
                    )
            # No BUY for this ticker → leave "" so the unmatched SELL
            # is dropped by match_fifo rather than fabricated.
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
        sell_lookup = {f["event_id"]: f for f in sells}
        match_buys = [
            {
                "event_id": f["event_id"],
                "qty": int(f["_payload"].get("qty") or 0),
                "price": float(
                    f["_payload"].get("fill_price")
                    or f["_payload"].get("price")
                    or 0,
                ),
                "ts_ns": int(f["ts_ns"]),
            }
            for f in buys
        ]
        match_sells = [
            {
                "event_id": f["event_id"],
                "qty": int(f["_payload"].get("qty") or 0),
                "price": float(
                    f["_payload"].get("fill_price")
                    or f["_payload"].get("price")
                    or 0,
                ),
                "ts_ns": int(f["ts_ns"]),
            }
            for f in sells
        ]
        for lot in match_fifo(match_buys, match_sells):
            sell_fill = sell_lookup[lot["sell_event_id"]]
            realised_pnl_inr = (
                (lot["sell_price"] - lot["buy_price"]) * lot["qty"]
            )
            return_pct = (
                (lot["sell_price"] - lot["buy_price"])
                / lot["buy_price"] * 100
                if lot["buy_price"] else 0.0
            )
            out.append({
                "strategy_id": strategy_id or None,
                "ticker": sym,
                "qty": lot["qty"],
                "avg_price": lot["buy_price"],
                "fill_price": lot["sell_price"],
                "opened_at": _ts_ns_to_date(lot["buy_ts_ns"]),
                "closed_at": _ts_ns_to_date(lot["sell_ts_ns"]),
                "opened_at_ts_ns": lot["buy_ts_ns"],
                "closed_at_ts_ns": lot["sell_ts_ns"],
                "realised_pnl_inr": realised_pnl_inr,
                "return_pct": return_pct,
                "exit_reason": (
                    sell_fill["_payload"].get("exit_reason")
                    or "signal"
                ),
                "dry_run": bool(
                    sell_fill["_payload"].get("dry_run", False),
                ),
                "buy_event_id": lot["buy_event_id"],
                "sell_event_id": lot["sell_event_id"],
            })
    return out


def _ts_ns_to_date(ts_ns: int) -> date:
    return datetime.fromtimestamp(
        ts_ns / 1_000_000_000, tz=timezone.utc,
    ).date()
