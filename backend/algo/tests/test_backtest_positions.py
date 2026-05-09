"""PositionTracker — long/exit, FIFO realised P&L."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill


def _fill(ticker: str, side: str, qty: int, price: float, day: date) -> Fill:
    return Fill(
        intent_id=uuid4(),
        ticker=ticker,
        side=side,
        qty=qty,
        fill_price=Decimal(str(price)),
        fill_date=day,
        fees_inr=Decimal("0"),
        fee_rates_version="2026-04-01",
    )


def test_buy_opens_long_position():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    pos = pt.open_positions()["X"]
    assert pos.qty == 10
    assert pos.avg_price == Decimal("100")


def test_sell_closes_long_realises_pnl():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    pt.apply_fill(_fill("X", "SELL", 10, 110, date(2024, 1, 3)))
    assert pt.open_positions().get("X") is None
    closed = pt.closed_positions()
    assert len(closed) == 1
    # Realised PnL = (110 - 100) * 10 = 100
    assert closed[0].realised_pnl_inr == Decimal("100")


def test_partial_sell_keeps_remainder_open():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    pt.apply_fill(_fill("X", "SELL", 4, 105, date(2024, 1, 3)))
    pos = pt.open_positions()["X"]
    assert pos.qty == 6
    assert pos.avg_price == Decimal("100")
    closed = pt.closed_positions()
    # Realised PnL = (105 - 100) * 4 = 20
    assert closed[0].realised_pnl_inr == Decimal("20")


def test_average_price_blends_two_buys():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    pt.apply_fill(_fill("X", "BUY", 10, 120, date(2024, 1, 3)))
    pos = pt.open_positions()["X"]
    assert pos.qty == 20
    assert pos.avg_price == Decimal("110")


def test_short_side_not_supported_v1():
    pt = PositionTracker()
    # Selling without a long is a no-op in v1 (long-only).
    pt.apply_fill(_fill("X", "SELL", 5, 100, date(2024, 1, 2)))
    assert pt.open_positions() == {}


def test_total_realised_pnl_aggregates():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    pt.apply_fill(_fill("X", "SELL", 10, 110, date(2024, 1, 3)))
    pt.apply_fill(_fill("Y", "BUY", 5, 200, date(2024, 1, 2)))
    pt.apply_fill(_fill("Y", "SELL", 5, 190, date(2024, 1, 3)))
    # X: +100 ; Y: -50  →  total +50
    assert pt.total_realised_pnl_inr() == Decimal("50")


def test_unrealised_pnl_uses_mark_price():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    # Mark at 110 → unrealised = (110 - 100) * 10 = 100
    pnl = pt.unrealised_pnl_inr({"X": Decimal("110")})
    assert pnl == Decimal("100")
