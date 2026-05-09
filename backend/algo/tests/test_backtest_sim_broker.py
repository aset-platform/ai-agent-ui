"""SimBroker fill mechanics + IndianFeeModel integration."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.algo.backtest.sim_broker import (
    NoBarAvailableError,
    SimBroker,
)
from backend.algo.backtest.types import BarData, OrderIntent


def _bar(ticker: str, day: date, openp: float, close: float) -> BarData:
    return BarData(
        ticker=ticker,
        date=day,
        open=Decimal(str(openp)),
        high=Decimal(str(close + 5)),
        low=Decimal(str(openp - 5)),
        close=Decimal(str(close)),
        volume=100_000,
    )


@pytest.fixture
def bars() -> dict[str, list[BarData]]:
    return {
        "RELIANCE.NS": [
            _bar("RELIANCE.NS", date(2026, 4, 1), 2900, 2920),
            _bar("RELIANCE.NS", date(2026, 4, 2), 2925, 2935),
            _bar("RELIANCE.NS", date(2026, 4, 3), 2940, 2950),
        ],
    }


def test_buy_fills_at_next_bar_open(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2026, 4, 1))
    intent = OrderIntent(
        ticker="RELIANCE.NS", side="BUY", qty=10,
        intent_emitted_at=date(2026, 4, 1),
    )
    fill = sb.execute(intent)
    assert fill is not None
    assert fill.fill_date == date(2026, 4, 2)
    assert fill.fill_price == Decimal("2925")
    assert fill.qty == 10


def test_sell_fills_at_next_bar_open(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2026, 4, 1))
    intent = OrderIntent(
        ticker="RELIANCE.NS", side="SELL", qty=5,
        intent_emitted_at=date(2026, 4, 2),
    )
    fill = sb.execute(intent)
    assert fill.fill_date == date(2026, 4, 3)
    assert fill.fill_price == Decimal("2940")


def test_no_next_bar_returns_none(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2026, 4, 1))
    # Last bar in fixture is Jan 3 — no T+1 available.
    intent = OrderIntent(
        ticker="RELIANCE.NS", side="BUY", qty=10,
        intent_emitted_at=date(2026, 4, 3),
    )
    assert sb.execute(intent) is None


def test_unknown_ticker_raises(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2026, 4, 1))
    with pytest.raises(NoBarAvailableError):
        sb.execute(OrderIntent(
            ticker="UNKNOWN.NS", side="BUY", qty=1,
            intent_emitted_at=date(2026, 4, 1),
        ))


def test_fee_is_non_zero_for_delivery_buy(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2026, 4, 1))
    fill = sb.execute(OrderIntent(
        ticker="RELIANCE.NS", side="BUY", qty=100,
        intent_emitted_at=date(2026, 4, 1),
    ))
    assert fill.fees_inr > Decimal("0")
    assert fill.fee_rates_version  # non-empty stamp


def test_intent_emitted_at_after_period_returns_none(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2026, 4, 1))
    intent = OrderIntent(
        ticker="RELIANCE.NS", side="BUY", qty=10,
        intent_emitted_at=date(2026, 4, 10),  # past last bar
    )
    assert sb.execute(intent) is None
