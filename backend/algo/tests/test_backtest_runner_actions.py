"""Targeted tests for runner action handling — set_target_weight
sizing + missing-feature graceful skip.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.runner import _action_to_intent
from backend.algo.backtest.types import Fill


def _fill(ticker: str, qty: int, price: float) -> Fill:
    from uuid import uuid4
    return Fill(
        intent_id=uuid4(), ticker=ticker, side="BUY", qty=qty,
        fill_price=Decimal(str(price)), fill_date=date(2026, 1, 1),
        fees_inr=Decimal("0"), fee_rates_version="2026-04-01",
    )


def test_set_target_weight_sizes_buy_when_no_position():
    """target=20% × 100k equity = 20k notional / 100 last_price = 200 qty."""
    pt = PositionTracker()
    intent = _action_to_intent(
        {"type": "set_target_weight", "weight": 0.20},
        ticker="X", bar_date=date(2026, 1, 1), pt=pt,
        last_price=Decimal("100"),
        current_equity=Decimal("100000"),
    )
    assert intent is not None
    assert intent.side == "BUY"
    assert intent.qty == 200


def test_set_target_weight_sizes_sell_when_over_weight():
    """Existing 300 shares at 100 = 30k notional. Target 20% = 20k → 200 qty.
    Diff = -100 → SELL 100 shares."""
    pt = PositionTracker()
    pt.apply_fill(_fill("X", qty=300, price=100))
    intent = _action_to_intent(
        {"type": "set_target_weight", "weight": 0.20},
        ticker="X", bar_date=date(2026, 1, 2), pt=pt,
        last_price=Decimal("100"),
        current_equity=Decimal("100000"),
    )
    assert intent is not None
    assert intent.side == "SELL"
    assert intent.qty == 100


def test_set_target_weight_no_op_when_at_target():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", qty=200, price=100))
    intent = _action_to_intent(
        {"type": "set_target_weight", "weight": 0.20},
        ticker="X", bar_date=date(2026, 1, 2), pt=pt,
        last_price=Decimal("100"),
        current_equity=Decimal("100000"),
    )
    assert intent is None


def test_set_target_weight_returns_none_on_zero_weight():
    pt = PositionTracker()
    intent = _action_to_intent(
        {"type": "set_target_weight", "weight": 0},
        ticker="X", bar_date=date(2026, 1, 1), pt=pt,
        last_price=Decimal("100"),
        current_equity=Decimal("100000"),
    )
    assert intent is None


def test_set_target_weight_returns_none_on_zero_price():
    pt = PositionTracker()
    intent = _action_to_intent(
        {"type": "set_target_weight", "weight": 0.20},
        ticker="X", bar_date=date(2026, 1, 1), pt=pt,
        last_price=Decimal("0"),
        current_equity=Decimal("100000"),
    )
    assert intent is None


def test_set_target_weight_returns_none_on_zero_equity():
    pt = PositionTracker()
    intent = _action_to_intent(
        {"type": "set_target_weight", "weight": 0.20},
        ticker="X", bar_date=date(2026, 1, 1), pt=pt,
        last_price=Decimal("100"),
        current_equity=Decimal("0"),
    )
    assert intent is None


def test_set_target_weight_floors_to_int_qty():
    """30k notional / 99 last_price = 303.03 → floor to 303."""
    pt = PositionTracker()
    intent = _action_to_intent(
        {"type": "set_target_weight", "weight": 0.30},
        ticker="X", bar_date=date(2026, 1, 1), pt=pt,
        last_price=Decimal("99"),
        current_equity=Decimal("100000"),
    )
    assert intent is not None
    assert intent.qty == 303


def test_buy_action_unchanged_by_new_kwargs():
    """Existing buy/sell/exit handlers ignore the new
    last_price/current_equity kwargs."""
    pt = PositionTracker()
    intent = _action_to_intent(
        {"type": "buy", "qty": {"shares": 5}},
        ticker="X", bar_date=date(2026, 1, 1), pt=pt,
        last_price=Decimal("100"),
        current_equity=Decimal("100000"),
    )
    assert intent is not None
    assert intent.side == "BUY"
    assert intent.qty == 5


def test_hold_action_returns_none():
    pt = PositionTracker()
    assert _action_to_intent(
        {"type": "hold"}, ticker="X",
        bar_date=date(2026, 1, 1), pt=pt,
    ) is None
