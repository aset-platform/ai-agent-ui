from datetime import date
from decimal import Decimal

from backend.algo.backtest.sim_broker import SimBroker
from backend.algo.backtest.types import BarData, OrderIntent


def _bar(d, o, h, l, c, ts=None):
    return BarData(
        ticker="X.NS", date=d, open=Decimal(o), high=Decimal(h),
        low=Decimal(l), close=Decimal(c), volume=1000,
        bar_open_ts_ns=ts,
    )


def test_trigger_fill_is_same_bar_at_trigger(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "0")
    bars = {"X.NS": [
        _bar(date(2026, 6, 1), "100", "101", "94", "95", ts=1),
        _bar(date(2026, 6, 1), "95", "96", "90", "92", ts=2),
    ]}
    sim = SimBroker(bars=bars, fee_as_of=date(2026, 6, 1))
    intent = OrderIntent(
        ticker="X.NS", side="SELL", qty=10,
        intent_emitted_at=date(2026, 6, 1), intent_emitted_ts_ns=1,
        exit_reason="trail_stop", trigger_price=Decimal("96.50"),
    )
    fill = sim.execute(intent)
    assert fill is not None
    # same bar (ts=1), filled AT trigger (no slippage), not next open
    assert fill.fill_ts_ns == 1
    assert fill.fill_price == Decimal("96.50")
    assert fill.trigger_price == Decimal("96.50")


def test_trigger_fill_applies_sell_slippage(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "100")  # 1%
    bars = {"X.NS": [_bar(date(2026, 6, 1), "100", "101", "94", "95", ts=1)]}
    sim = SimBroker(bars=bars, fee_as_of=date(2026, 6, 1))
    intent = OrderIntent(
        ticker="X.NS", side="SELL", qty=10,
        intent_emitted_at=date(2026, 6, 1), intent_emitted_ts_ns=1,
        exit_reason="phase1_stop", trigger_price=Decimal("100.00"),
    )
    fill = sim.execute(intent)
    # SELL receives less: 100 * (1 - 0.01) = 99.00
    assert fill.fill_price == Decimal("99.00")


def test_explicit_delivery_product_books_higher_fees(monkeypatch):
    """Two-clock CNC exit: an exec-bar trigger fill (ts_ns set) with
    explicit ``product='DELIVERY'`` must book DELIVERY fees, NOT the
    cheaper INTRADAY fees the bar grain would otherwise infer.

    Delivery sell STT (~0.1%) > intraday sell STT (~0.025%) and
    delivery adds DP charges, so the DELIVERY fill MUST have strictly
    higher total fees than the product=None (INTRADAY-inferred) fill.
    """
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "0")
    bars = {"X.NS": [_bar(date(2026, 6, 1), "100", "101", "94", "95",
                          ts=1)]}
    sim = SimBroker(bars=bars, fee_as_of=date(2026, 6, 1))

    # ts_ns set → bar-grain inference would say INTRADAY.
    intraday = sim.execute(OrderIntent(
        ticker="X.NS", side="SELL", qty=10,
        intent_emitted_at=date(2026, 6, 1), intent_emitted_ts_ns=1,
        exit_reason="trail_stop", trigger_price=Decimal("100.00"),
        product=None,
    ))
    # Same exec bar, but explicit DELIVERY product (true CNC product).
    delivery = sim.execute(OrderIntent(
        ticker="X.NS", side="SELL", qty=10,
        intent_emitted_at=date(2026, 6, 1), intent_emitted_ts_ns=1,
        exit_reason="trail_stop", trigger_price=Decimal("100.00"),
        product="DELIVERY",
    ))
    assert intraday is not None and delivery is not None
    # Identical fill price (only the fee schedule differs).
    assert intraday.fill_price == delivery.fill_price
    # Delivery exit must underbill nothing: strictly higher fees.
    assert delivery.fees_inr > intraday.fees_inr
