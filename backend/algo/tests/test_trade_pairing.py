"""Tests for the FIFO buy/sell fill-pairing helper used by the
Strategy Performance closed-trades rollup job."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.algo.attribution.trade_pairing import (
    pair_fills_by_strategy_and_ticker,
)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def _fill(
    *, strategy_id, event_id, symbol, side, qty, fill_price, ts,
    event_type="order_filled", exit_reason=None, dry_run=False,
):
    payload = {
        "symbol": symbol, "side": side, "qty": qty,
        "fill_price": fill_price, "dry_run": dry_run,
    }
    if exit_reason is not None:
        payload["exit_reason"] = exit_reason
    return {
        "event_id": event_id,
        "strategy_id": strategy_id,
        "type": event_type,
        "payload_json": json.dumps(payload),
        "ts_ns": _ts(ts),
    }


def test_pairs_single_buy_sell_fifo():
    events = [
        _fill(
            strategy_id="s1", event_id="e1", symbol="ITC",
            side="BUY", qty=10, fill_price=300.0,
            ts=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="e2", symbol="ITC",
            side="SELL", qty=10, fill_price=310.0,
            ts=datetime(2026, 6, 5, tzinfo=timezone.utc),
            exit_reason="signal",
        ),
    ]
    trades = pair_fills_by_strategy_and_ticker(events)
    assert len(trades) == 1
    t = trades[0]
    assert t["ticker"] == "ITC"
    assert t["strategy_id"] == "s1"
    assert t["qty"] == 10
    assert t["avg_price"] == 300.0
    assert t["fill_price"] == 310.0
    assert t["realised_pnl_inr"] == 100.0
    assert round(t["return_pct"], 4) == round(10 / 300 * 100, 4)
    assert t["exit_reason"] == "signal"
    assert t["dry_run"] is False
    assert t["buy_event_id"] == "e1"
    assert t["sell_event_id"] == "e2"
    assert t["opened_at"].isoformat() == "2026-06-01"
    assert t["closed_at"].isoformat() == "2026-06-05"


def test_one_buy_split_across_multiple_sells():
    """The KTKBANK shape found 2026-07-02: one BUY of 16 exited via
    three separate SELLs (1, 1, 14) at different prices/dates. Must
    produce three closed-trade rows, not one row with the full buy
    qty against only the first sell (which also silently dropped
    the other two sells entirely)."""
    events = [
        _fill(
            strategy_id="s1", event_id="b1", symbol="KTKBANK",
            side="BUY", qty=16, fill_price=266.3,
            ts=datetime(2026, 6, 24, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="s1", symbol="KTKBANK",
            side="SELL", qty=1, fill_price=267.0,
            ts=datetime(2026, 6, 25, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="s2", symbol="KTKBANK",
            side="SELL", qty=1, fill_price=267.0,
            ts=datetime(2026, 6, 25, 0, 0, 1, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="s3", symbol="KTKBANK",
            side="SELL", qty=14, fill_price=270.15,
            ts=datetime(2026, 7, 2, tzinfo=timezone.utc),
        ),
    ]
    trades = pair_fills_by_strategy_and_ticker(events)
    assert len(trades) == 3
    total_qty = sum(t["qty"] for t in trades)
    assert total_qty == 16
    last = [t for t in trades if t["sell_event_id"] == "s3"][0]
    assert last["qty"] == 14
    assert last["fill_price"] == 270.15
    assert last["closed_at"].isoformat() == "2026-07-02"
    total_pnl = sum(t["realised_pnl_inr"] for t in trades)
    assert round(total_pnl, 2) == 55.3


def test_does_not_mix_two_strategies_on_same_ticker():
    """Two different strategies both trading ITC on the same day
    must NOT be cross-paired (strategy A's buy with strategy B's
    sell)."""
    events = [
        _fill(
            strategy_id="s1", event_id="e1", symbol="ITC",
            side="BUY", qty=10, fill_price=300.0,
            ts=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s2", event_id="e2", symbol="ITC",
            side="BUY", qty=5, fill_price=305.0,
            ts=datetime(2026, 6, 1, 1, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="e3", symbol="ITC",
            side="SELL", qty=10, fill_price=310.0,
            ts=datetime(2026, 6, 5, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s2", event_id="e4", symbol="ITC",
            side="SELL", qty=5, fill_price=308.0,
            ts=datetime(2026, 6, 6, tzinfo=timezone.utc),
        ),
    ]
    trades = pair_fills_by_strategy_and_ticker(events)
    assert len(trades) == 2
    by_strategy = {t["strategy_id"]: t for t in trades}
    assert by_strategy["s1"]["buy_event_id"] == "e1"
    assert by_strategy["s1"]["sell_event_id"] == "e3"
    assert by_strategy["s2"]["buy_event_id"] == "e2"
    assert by_strategy["s2"]["sell_event_id"] == "e4"


def test_unmatched_open_position_is_skipped():
    events = [
        _fill(
            strategy_id="s1", event_id="e1", symbol="TCS",
            side="BUY", qty=1, fill_price=4000.0,
            ts=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ),
    ]
    assert pair_fills_by_strategy_and_ticker(events) == []


def test_live_fills_use_price_key_not_fill_price():
    """Real order_filled_live payloads (both the direct live/runtime.py
    fill path and the Kite postback webhook path) carry the price
    under "price", never "fill_price" -- paper's order_filled events
    use "fill_price". Confirmed against real algo.events rows
    2026-07-02: e.g. {"kite_order_id": ..., "symbol": "EQUITASBNK",
    "side": "SELL", "qty": 1, "source": "kite_postback", "price":
    "77.04", "fees_inr": "0"} -- no "fill_price" key at all. Without
    a fallback, every live-mode closed trade materializes with
    avg_price=fill_price=pnl=return_pct=0."""
    events = [
        {
            "event_id": "e1", "strategy_id": "s1",
            "type": "order_filled_live",
            "payload_json": json.dumps({
                "symbol": "EQUITASBNK", "side": "BUY", "qty": 1,
                "source": "kite_postback", "price": "74.77",
                "fees_inr": "0",
            }),
            "ts_ns": _ts(datetime(2026, 6, 29, tzinfo=timezone.utc)),
        },
        {
            "event_id": "e2", "strategy_id": "s1",
            "type": "order_filled_live",
            "payload_json": json.dumps({
                "symbol": "EQUITASBNK", "side": "SELL", "qty": 1,
                "source": "kite_postback", "price": "77.04",
                "fees_inr": "0",
            }),
            "ts_ns": _ts(datetime(2026, 7, 1, tzinfo=timezone.utc)),
        },
    ]
    trades = pair_fills_by_strategy_and_ticker(events)
    assert len(trades) == 1
    t = trades[0]
    assert t["avg_price"] == 74.77
    assert t["fill_price"] == 77.04
    assert round(t["realised_pnl_inr"], 2) == 2.27
    assert t["return_pct"] != 0.0


def test_strips_ns_suffix_and_ignores_non_fill_events():
    events = [
        {
            "event_id": "sig1", "strategy_id": "s1",
            "type": "signal_generated",
            "payload_json": '{"ticker": "ITC.NS", "side": "BUY"}',
            "ts_ns": _ts(datetime(2026, 6, 1, tzinfo=timezone.utc)),
        },
        _fill(
            strategy_id="s1", event_id="e1", symbol="ITC",
            side="BUY", qty=2, fill_price=300.0,
            ts=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="e2", symbol="ITC",
            side="SELL", qty=2, fill_price=290.0,
            event_type="order_filled_live",
            ts=datetime(2026, 6, 2, tzinfo=timezone.utc),
        ),
    ]
    trades = pair_fills_by_strategy_and_ticker(events)
    assert len(trades) == 1
    assert trades[0]["ticker"] == "ITC"
    assert trades[0]["realised_pnl_inr"] == -20.0
    # No explicit exit_reason on the SELL payload -> defaults.
    assert trades[0]["exit_reason"] == "signal"
