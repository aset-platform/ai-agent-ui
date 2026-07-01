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
