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

# Authoritative live-fill ``reason`` values → the exit-reason label the
# Performance page displays. A live SELL fill carries the true exit
# cause under ``payload["reason"]`` (stamped from the in-flight entry
# by the postback reconciler, or set directly on the runtime fill
# path); the pairing reads it in preference to any source/event
# heuristic. ``mis_auto_square_off`` is normalised to the badge's
# ``mis_square_off`` key. Entry-side/rebalance reasons
# (``set_target_weight``) and the generic ``exit`` are intentionally
# absent so they fall through to the signal/panic/gtt fallbacks.
_REASON_TO_EXIT: dict[str, str] = {
    "stop_loss": "stop_loss",
    "trail_stop": "trail_stop",
    "time_stop": "time_stop",
    "regime_exit": "regime_exit",
    "user_exit": "user_exit",
    "gtt_triggered": "gtt_triggered",
    "mis_auto_square_off": "mis_square_off",
    "mis_square_off": "mis_square_off",
}


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
    # First pass: parse fills once, record which strategies opened
    # BUYs per ticker (earliest-buy-first, deduped) so a strategy-less
    # SELL can inherit the owning strategy, and collect the
    # kite_order_ids of panic-close orders so the resulting closed
    # trade can be labelled exit_reason='panic_close'. A panic SELL's
    # order_filled_live (from the Kite postback) carries no exit_reason
    # or source marker — the panic intent survives only on the
    # order_submitted_live it shares a kite_order_id with.
    parsed: list[tuple[dict, dict, str]] = []
    _buy_strat_ts: dict[str, list[tuple[int, str]]] = {}
    panic_order_ids: set[str] = set()
    # (ticker, closed-date) keys where a GTT / trailing-stop fired.
    # Both exit pieces emit a gtt_triggered event (Piece A source
    # gtt_poll, Piece B source postback), so a SELL fill whose
    # (ticker, date) matches one is a stop-out, not a strategy signal.
    gtt_exit_keys: set[tuple[str, date]] = set()
    for ev in events:
        etype = ev.get("type")
        if etype == "order_submitted_live":
            try:
                sub_payload = json.loads(ev.get("payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                sub_payload = {}
            if sub_payload.get("source") == "panic_close":
                koid = str(sub_payload.get("kite_order_id") or "")
                if koid:
                    panic_order_ids.add(koid)
            continue
        if etype == "gtt_triggered":
            try:
                gtt_payload = json.loads(ev.get("payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                gtt_payload = {}
            graw = gtt_payload.get("ticker") or gtt_payload.get("symbol")
            gsym = str(graw or "").upper().removesuffix(".NS")
            if gsym:
                gtt_exit_keys.add(
                    (gsym, _ts_ns_to_date(int(ev.get("ts_ns") or 0))),
                )
            continue
        if etype not in _FILL_TYPES:
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
                "exit_reason": _resolve_exit_reason(
                    sell_fill["_payload"],
                    panic_order_ids,
                    gtt_exit_keys,
                    sym,
                    _ts_ns_to_date(lot["sell_ts_ns"]),
                ),
                "dry_run": bool(
                    sell_fill["_payload"].get("dry_run", False),
                ),
                "buy_event_id": lot["buy_event_id"],
                "sell_event_id": lot["sell_event_id"],
            })
    return out


def _resolve_exit_reason(
    sell_payload: dict[str, Any],
    panic_order_ids: set[str],
    gtt_exit_keys: set[tuple[str, date]],
    sym: str,
    closed_at: date,
) -> str:
    """Best-effort exit-reason label for a SELL fill.

    Priority (most specific first):

    1. An explicit ``exit_reason`` on the fill payload wins (backtest
       trade_list rows carry real reasons like ``stop_loss``).
    2. The authoritative live-fill ``reason`` field, when it maps to a
       known exit reason (``user_exit``, ``stop_loss``, ``trail_stop``,
       ``time_stop``, ``regime_exit``, ``gtt_triggered``, MIS
       square-off). This is where a user-initiated close and a
       stop-loss get their true label — panic fills carry no ``reason``
       (they bypass the in-flight ledger) so they fall through.
    3. A fill whose ``kite_order_id`` matches a panic-close submit
       event → ``panic_close``. Checked BEFORE the GTT fallback
       because panic-close deletes GTTs on Kite but not the runtime's
       in-memory state, so a panic fill's postback also trips a
       spurious ``gtt_triggered`` event for the same ticker+date.
    4. GTT fallback for a fill with no usable ``reason``: source
       ``gtt_poll`` (Piece A) or a ``gtt_triggered`` event for the
       same (ticker, date) (Piece B) → ``gtt_triggered``.
    5. Otherwise ``signal`` (genuine strategy-rule exits, rebalance
       sells, and anything unrecognised).
    """
    explicit = sell_payload.get("exit_reason")
    if explicit:
        return str(explicit)
    mapped = _REASON_TO_EXIT.get(str(sell_payload.get("reason") or ""))
    if mapped:
        return mapped
    koid = str(sell_payload.get("kite_order_id") or "")
    if koid and koid in panic_order_ids:
        return "panic_close"
    if (
        sell_payload.get("source") == "gtt_poll"
        or (sym, closed_at) in gtt_exit_keys
    ):
        return "gtt_triggered"
    return "signal"


def _ts_ns_to_date(ts_ns: int) -> date:
    return datetime.fromtimestamp(
        ts_ns / 1_000_000_000, tz=timezone.utc,
    ).date()
