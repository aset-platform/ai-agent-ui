"""Unit tests for PositionTracker."""
from __future__ import annotations

from decimal import Decimal
from datetime import date
from uuid import uuid4

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill


def _buy_fill(ticker: str, qty: int = 10) -> Fill:
    return Fill(
        intent_id=uuid4(),
        ticker=ticker,
        side="BUY",
        qty=qty,
        fill_price=Decimal("100.00"),
        fill_date=date(2025, 1, 1),
        fees_inr=Decimal("0"),
        fee_rates_version="v1",
    )


class TestHasPosition:
    def test_true_for_open_position(self):
        tracker = PositionTracker()
        tracker.apply_fill(_buy_fill("ITC.NS"))
        assert tracker.has_position("ITC.NS") is True

    def test_false_for_never_traded_ticker(self):
        tracker = PositionTracker()
        assert tracker.has_position("NEVER.NS") is False

    def test_matches_open_positions_membership(self):
        tracker = PositionTracker()
        tracker.apply_fill(_buy_fill("ITC.NS"))
        assert tracker.has_position("ITC.NS") == (
            "ITC.NS" in tracker.open_positions()
        )
