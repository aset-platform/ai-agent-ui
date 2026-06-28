from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.paper.broker import PaperBroker
from backend.algo.paper.types import Signal


def _sell(qty=10):
    return Signal(
        strategy_id=uuid4(), user_id=uuid4(), ticker="X.NS",
        side="SELL", qty=qty, emitted_at_ns=0, reason="trail_stop",
    )


def test_trigger_fill_uses_trigger_not_last_price(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "0")
    b = PaperBroker(fee_as_of=date(2026, 6, 1), product="DELIVERY")
    fill = b.execute(
        signal=_sell(), last_price=Decimal("90.00"),
        fill_date=date(2026, 6, 1), trigger_price=Decimal("95.00"),
    )
    # fills AT trigger (95), not last_price (90); bps=0 → no slippage
    assert fill.fill_price == Decimal("95.00")
    assert fill.trigger_price == Decimal("95.00")


def test_trigger_fill_applies_sell_slippage(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "100")  # 1%
    b = PaperBroker(fee_as_of=date(2026, 6, 1), product="DELIVERY")
    fill = b.execute(
        signal=_sell(), last_price=Decimal("90.00"),
        fill_date=date(2026, 6, 1), trigger_price=Decimal("100.00"),
    )
    # SELL receives less: 100 * (1 - 0.01) = 99.00
    assert fill.fill_price == Decimal("99.00")


def test_no_trigger_is_unchanged_market_fill(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "0")
    b = PaperBroker(fee_as_of=date(2026, 6, 1))  # default product
    fill = b.execute(
        signal=_sell(), last_price=Decimal("90.00"),
        fill_date=date(2026, 6, 1),
    )
    assert fill.fill_price == Decimal("90.00")
    assert fill.trigger_price is None


def test_mis_books_intraday_fees_below_delivery():
    sig = _sell(qty=100)
    d = PaperBroker(fee_as_of=date(2026, 6, 1), product="DELIVERY")
    i = PaperBroker(fee_as_of=date(2026, 6, 1), product="INTRADAY")
    fd = date(2026, 6, 1)
    df = d.execute(signal=sig, last_price=Decimal("500"), fill_date=fd)
    ifl = i.execute(signal=sig, last_price=Decimal("500"), fill_date=fd)
    # delivery sell STT (~0.1%) > intraday sell STT (~0.025%)
    assert df.fees_inr > ifl.fees_inr
