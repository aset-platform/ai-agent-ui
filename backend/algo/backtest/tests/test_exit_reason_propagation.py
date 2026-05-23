"""exit_reason threads OrderIntent → Fill → closed Position."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill, OrderIntent


def test_order_intent_defaults_exit_reason_to_signal():
    oi = OrderIntent(
        ticker="AAA.NS",
        side="BUY",
        qty=10,
        intent_emitted_at=date(2025, 1, 1),
    )
    assert oi.exit_reason == "signal"


def test_order_intent_accepts_custom_exit_reason():
    oi = OrderIntent(
        ticker="AAA.NS",
        side="SELL",
        qty=10,
        intent_emitted_at=date(2025, 1, 1),
        exit_reason="stop_loss",
    )
    assert oi.exit_reason == "stop_loss"


def test_fill_carries_exit_reason():
    f = Fill(
        intent_id=uuid4(),
        ticker="AAA.NS",
        side="SELL",
        qty=10,
        fill_price=Decimal("100"),
        fill_date=date(2025, 1, 2),
        fees_inr=Decimal("5"),
        fee_rates_version="v1",
        exit_reason="stop_loss",
    )
    assert f.exit_reason == "stop_loss"


def test_apply_sell_stamps_exit_reason_on_closed_position():
    pt = PositionTracker()
    pt.apply_fill(Fill(
        intent_id=uuid4(),
        ticker="AAA.NS",
        side="BUY",
        qty=10,
        fill_price=Decimal("100"),
        fill_date=date(2025, 1, 1),
        fees_inr=Decimal("5"),
        fee_rates_version="v1",
    ))
    pt.apply_fill(Fill(
        intent_id=uuid4(),
        ticker="AAA.NS",
        side="SELL",
        qty=10,
        fill_price=Decimal("95"),
        fill_date=date(2025, 1, 2),
        fees_inr=Decimal("4"),
        fee_rates_version="v1",
        exit_reason="stop_loss",
    ))
    closed = pt.closed_positions()
    assert len(closed) == 1
    assert closed[0].exit_reason == "stop_loss"
