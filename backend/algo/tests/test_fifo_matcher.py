"""Tests for the shared quantity-aware FIFO fill matcher used by
both trade_pairing.py (closed-trades rollup) and
routes/attribution.py (single-day attribution view)."""
from __future__ import annotations

from backend.algo.attribution.fifo_matcher import match_fifo


def _buy(event_id, qty, price, ts_ns):
    return {
        "event_id": event_id, "qty": qty,
        "price": price, "ts_ns": ts_ns,
    }


def _sell(event_id, qty, price, ts_ns):
    return {
        "event_id": event_id, "qty": qty,
        "price": price, "ts_ns": ts_ns,
    }


def test_one_to_one_match():
    buys = [_buy("b1", 10, 300.0, 100)]
    sells = [_sell("s1", 10, 310.0, 200)]
    lots = match_fifo(buys, sells)
    assert len(lots) == 1
    lot = lots[0]
    assert lot["buy_event_id"] == "b1"
    assert lot["sell_event_id"] == "s1"
    assert lot["qty"] == 10
    assert lot["buy_price"] == 300.0
    assert lot["sell_price"] == 310.0
    assert lot["buy_ts_ns"] == 100
    assert lot["sell_ts_ns"] == 200


def test_one_buy_split_across_multiple_sells():
    """The KTKBANK shape: one BUY of 16, exited via three separate
    SELLs (1, 1, 14) at different prices and dates. Must produce
    three lots, each carrying its own sell's price/date, not one
    row with the full buy qty against only the first sell."""
    buys = [_buy("b1", 16, 266.3, 100)]
    sells = [
        _sell("s1", 1, 267.0, 200),
        _sell("s2", 1, 267.0, 201),
        _sell("s3", 14, 270.15, 300),
    ]
    lots = match_fifo(buys, sells)
    assert len(lots) == 3
    assert [lot["sell_event_id"] for lot in lots] == [
        "s1", "s2", "s3",
    ]
    assert [lot["qty"] for lot in lots] == [1, 1, 14]
    for lot in lots:
        assert lot["buy_event_id"] == "b1"
        assert lot["buy_price"] == 266.3
    assert lots[0]["sell_price"] == 267.0
    assert lots[2]["sell_price"] == 270.15
    assert lots[2]["sell_ts_ns"] == 300
    total_qty = sum(lot["qty"] for lot in lots)
    assert total_qty == 16


def test_one_sell_spans_multiple_buys():
    """Reverse shape: two separate BUY lots, closed by a single
    SELL that covers both."""
    buys = [
        _buy("b1", 5, 100.0, 100),
        _buy("b2", 5, 110.0, 150),
    ]
    sells = [_sell("s1", 10, 120.0, 200)]
    lots = match_fifo(buys, sells)
    assert len(lots) == 2
    assert lots[0]["buy_event_id"] == "b1"
    assert lots[0]["qty"] == 5
    assert lots[0]["buy_price"] == 100.0
    assert lots[1]["buy_event_id"] == "b2"
    assert lots[1]["qty"] == 5
    assert lots[1]["buy_price"] == 110.0
    for lot in lots:
        assert lot["sell_event_id"] == "s1"
        assert lot["sell_price"] == 120.0


def test_unconsumed_buy_quantity_excluded():
    """A buy larger than the available sell quantity leaves an
    open position -- excluded from the output, same as today's
    "unmatched fills are skipped" semantics."""
    buys = [_buy("b1", 10, 300.0, 100)]
    sells = [_sell("s1", 4, 310.0, 200)]
    lots = match_fifo(buys, sells)
    assert len(lots) == 1
    assert lots[0]["qty"] == 4


def test_sell_exceeding_available_buys_is_dropped_with_warning(
    caplog,
):
    """More sold than ever bought is a genuine data problem, not
    expected in practice -- must be dropped and logged, never
    fabricated into a phantom trade."""
    buys = [_buy("b1", 5, 300.0, 100)]
    sells = [_sell("s1", 8, 310.0, 200)]
    with caplog.at_level("WARNING"):
        lots = match_fifo(buys, sells)
    assert len(lots) == 1
    assert lots[0]["qty"] == 5
    assert any(
        "match_fifo" in rec.message for rec in caplog.records
    )


def test_no_buys_or_no_sells_returns_empty():
    assert match_fifo([], [_sell("s1", 5, 100.0, 100)]) == []
    assert match_fifo([_buy("b1", 5, 100.0, 100)], []) == []
    assert match_fifo([], []) == []


def test_inputs_need_not_be_pre_sorted():
    """FIFO order must be enforced internally by ts_ns, regardless
    of the order callers pass fills in."""
    buys = [
        _buy("b2", 5, 110.0, 150),
        _buy("b1", 5, 100.0, 100),
    ]
    sells = [_sell("s1", 10, 120.0, 200)]
    lots = match_fifo(buys, sells)
    assert lots[0]["buy_event_id"] == "b1"
    assert lots[1]["buy_event_id"] == "b2"
