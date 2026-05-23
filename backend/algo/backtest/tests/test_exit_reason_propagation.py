"""exit_reason threads OrderIntent → Fill → closed Position."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.sim_broker import SimBroker
from backend.algo.backtest.types import BarData, Fill, OrderIntent


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


def test_sim_broker_forwards_exit_reason_to_fill():
    """SimBroker.execute() carries OrderIntent.exit_reason into
    the Fill it returns — closes the workaround gap from Task 3
    so paper / live runtimes can reuse this propagation."""
    bars = {
        "RELIANCE.NS": [
            BarData(
                ticker="RELIANCE.NS",
                date=date(2026, 4, 1),
                open=Decimal("2900"),
                high=Decimal("2925"),
                low=Decimal("2895"),
                close=Decimal("2920"),
                volume=100_000,
            ),
            BarData(
                ticker="RELIANCE.NS",
                date=date(2026, 4, 2),
                open=Decimal("2925"),
                high=Decimal("2940"),
                low=Decimal("2920"),
                close=Decimal("2935"),
                volume=100_000,
            ),
        ],
    }
    sb = SimBroker(bars=bars, fee_as_of=date(2026, 4, 1))
    intent = OrderIntent(
        ticker="RELIANCE.NS",
        side="SELL",
        qty=10,
        intent_emitted_at=date(2026, 4, 1),
        exit_reason="stop_loss",
    )
    fill = sb.execute(intent)
    assert fill is not None
    assert fill.exit_reason == "stop_loss"
