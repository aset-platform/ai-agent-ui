"""Tests for _round_to_tick: buy/sell limit + stop-trigger rounding."""
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.kite_client import (
    KiteClient,
    _round_to_tick,
)


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


def test_fallback_tick_used_when_zero_exact_value():
    """Tick of 0 uses 0.05 fallback: BUY LIMIT 100.123 → 100.10."""
    result = _round_to_tick(
        100.123, Decimal("0"), side="BUY", is_stop=False
    )
    # Exact value: BUY LIMIT rounds DOWN to nearest 0.05
    assert result == pytest.approx(100.10)
    assert Decimal(str(result)) % Decimal("0.05") == 0


def test_fallback_tick_used_when_none_exact_value():
    """tick=None uses 0.05 fallback: SELL LIMIT 100.123 → 100.15."""
    result = _round_to_tick(
        100.123, None, side="SELL", is_stop=False
    )
    # Exact value: SELL LIMIT rounds UP to nearest 0.05
    assert result == pytest.approx(100.15)
    assert Decimal(str(result)) % Decimal("0.05") == 0


def _make_modify_client() -> KiteClient:
    """KiteClient with mocked internals for modify_order tests."""
    client = KiteClient.__new__(KiteClient)
    client._kc = MagicMock()
    client._dry_run = False
    client._access_token = "tok"
    client._redis = None
    return client


def test_modify_order_rounds_buy_limit_price():
    """modify_order tick-rounds its price before calling the SDK.

    BUY LIMIT 100.123 with 0.05 tick → SDK receives 100.10.
    """
    client = _make_modify_client()
    client._kc.modify_order.return_value = {}

    with patch(
        "backend.algo.broker.kite_client.get_tick_size",
        return_value=Decimal("0.05"),
    ):
        client.modify_order(
            "ord123",
            price=100.123,
            tradingsymbol="RELIANCE",
            transaction_type="BUY",
        )

    call_kwargs = client._kc.modify_order.call_args[1]
    assert call_kwargs["price"] == pytest.approx(100.10)


def test_modify_order_rounds_sell_limit_price():
    """modify_order tick-rounds its price before calling the SDK.

    SELL LIMIT 100.123 with 0.05 tick → SDK receives 100.15.
    """
    client = _make_modify_client()
    client._kc.modify_order.return_value = {}

    with patch(
        "backend.algo.broker.kite_client.get_tick_size",
        return_value=Decimal("0.05"),
    ):
        client.modify_order(
            "ord456",
            price=100.123,
            tradingsymbol="INFY",
            transaction_type="SELL",
        )

    call_kwargs = client._kc.modify_order.call_args[1]
    assert call_kwargs["price"] == pytest.approx(100.15)
