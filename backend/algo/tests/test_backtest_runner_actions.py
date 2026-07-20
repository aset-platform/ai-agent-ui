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


def test_set_target_weight_never_trims_when_over_weight():
    """set_target_weight is BUY-only once a position is open — it
    never trims (diff<0), even when the floor-divided target drops
    below the held qty. Existing 300 shares at 100 = 30k notional.
    Target 20% = 20k → 200 qty. Diff = -100, but no SELL is emitted;
    reductions only ever come from an explicit exit/stop_loss/
    time_stop/regime_exit intent."""
    pt = PositionTracker()
    pt.apply_fill(_fill("X", qty=300, price=100))
    intent = _action_to_intent(
        {"type": "set_target_weight", "weight": 0.20},
        ticker="X", bar_date=date(2026, 1, 2), pt=pt,
        last_price=Decimal("100"),
        current_equity=Decimal("100000"),
    )
    assert intent is None


def test_set_target_weight_never_trims_on_price_appreciation():
    """Regression for the 2026-07-17 WABAG live incident: 2 shares
    bought at ₹1996.90 (target 10% of ₹40k = ₹4,000 → floor(4000/
    1996.90)=2). Price rises to ₹2000.40 — a WINNING move — and
    floor(4000/2000.40)=1, so the naive diff<0 path would sell 1
    share purely from crossing a rounding boundary while still
    holding an oversold-entry condition open. Must be a no-op."""
    pt = PositionTracker()
    pt.apply_fill(_fill("WABAG.NS", qty=2, price=1996.90))
    intent = _action_to_intent(
        {"type": "set_target_weight", "weight": 0.1},
        ticker="WABAG.NS", bar_date=date(2026, 7, 17), pt=pt,
        last_price=Decimal("2000.40"),
        current_equity=Decimal("40000"),
    )
    assert intent is None


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
