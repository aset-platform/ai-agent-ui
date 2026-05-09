"""PaperBroker — at-tick fills + fee version stamp."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.paper.broker import PaperBroker
from backend.algo.paper.types import Signal


def _signal(qty=10, side="BUY") -> Signal:
    return Signal(
        strategy_id=uuid4(), user_id=uuid4(),
        ticker="X", side=side, qty=qty,
        emitted_at_ns=0,
    )


def test_fill_at_tick_price():
    broker = PaperBroker(fee_as_of=date(2026, 4, 1))
    fill = broker.execute(
        signal=_signal(qty=100),
        last_price=Decimal("250.50"),
        fill_date=date(2026, 4, 1),
    )
    assert fill.fill_price == Decimal("250.50")
    assert fill.qty == 100
    assert fill.fill_date == date(2026, 4, 1)


def test_fill_stamps_fee_rates_version():
    broker = PaperBroker(fee_as_of=date(2026, 4, 1))
    fill = broker.execute(
        signal=_signal(qty=10),
        last_price=Decimal("100"),
        fill_date=date(2026, 4, 1),
    )
    assert fill.fee_rates_version == "2026-04-01"
    assert fill.fees_inr > Decimal("0")


def test_buy_and_sell_both_fill():
    broker = PaperBroker(fee_as_of=date(2026, 4, 1))
    buy = broker.execute(
        signal=_signal(side="BUY"),
        last_price=Decimal("100"),
        fill_date=date(2026, 4, 1),
    )
    sell = broker.execute(
        signal=_signal(side="SELL"),
        last_price=Decimal("105"),
        fill_date=date(2026, 4, 1),
    )
    assert buy.side == "BUY"
    assert sell.side == "SELL"
    assert sell.fill_price == Decimal("105")
