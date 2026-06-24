"""Tests for _round_to_tick: buy/sell limit + stop-trigger rounding."""
from decimal import Decimal

import pytest

from backend.algo.broker.kite_client import _round_to_tick


TICK = Decimal("0.05")


def test_buy_limit_rounds_down_to_tick():
    """BUY LIMIT — choose the tick that does NOT overpay."""
    assert _round_to_tick(
        1234.5678, TICK, side="BUY", is_stop=False
    ) == 1234.55


def test_sell_limit_rounds_up_to_tick():
    """SELL LIMIT — never settle for less than the valid tick."""
    assert _round_to_tick(
        1234.5678, TICK, side="SELL", is_stop=False
    ) == 1234.60


def test_sell_stop_trigger_rounds_down():
    """SELL stop trigger rounds DOWN (fires below market)."""
    assert _round_to_tick(
        99.123, TICK, side="SELL", is_stop=True
    ) == 99.10


def test_buy_stop_trigger_rounds_up():
    """BUY stop trigger rounds UP (fires above market)."""
    assert _round_to_tick(
        99.123, TICK, side="BUY", is_stop=True
    ) == 99.15


def test_already_on_tick_unchanged():
    """Values that are already multiples of the tick are unchanged."""
    assert _round_to_tick(
        1234.50, TICK, side="BUY", is_stop=False
    ) == 1234.50
    assert _round_to_tick(
        1234.50, TICK, side="SELL", is_stop=False
    ) == 1234.50


def test_fallback_tick_used_when_zero():
    """Tick of 0 falls back to Decimal('0.05'); result is tick-aligned."""
    result = _round_to_tick(
        100.123, Decimal("0"), side="BUY", is_stop=False
    )
    # Must be a multiple of 0.05
    assert Decimal(str(result)) % Decimal("0.05") == 0


def test_fallback_tick_used_when_none():
    """tick=None falls back to Decimal('0.05')."""
    result = _round_to_tick(
        100.123, None, side="SELL", is_stop=False
    )
    assert Decimal(str(result)) % Decimal("0.05") == 0
