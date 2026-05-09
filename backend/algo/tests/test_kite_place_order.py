"""Tests for KiteClient.place_order / cancel_order / modify_order.

All tests mock kiteconnect.KiteConnect so NO network calls are made.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.kite_client import KiteClient


@pytest.fixture()
def kite_client():
    """KiteClient with a mocked KiteConnect SDK."""
    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        client = KiteClient(
            api_key="test_api_key",
            access_token="test_access_token",
        )
        client._kc = kc_instance
        yield client, kc_instance


# -----------------------------------------------------------------
# place_order — happy path
# -----------------------------------------------------------------

class TestPlaceOrderHappyPath:
    def test_market_order_returns_order_id(self, kite_client):
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {
            "order_id": "KITE123456",
        }
        order_id = client.place_order(
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=10,
            order_type="MARKET",
        )
        assert order_id == "KITE123456"
        mock_kc.place_order.assert_called_once_with(
            variety="regular",
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=10,
            order_type="MARKET",
            product="CNC",
        )

    def test_limit_order_includes_price(self, kite_client):
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "KITE789"}
        order_id = client.place_order(
            tradingsymbol="INFY",
            exchange="NSE",
            transaction_type="BUY",
            quantity=5,
            order_type="LIMIT",
            price=1500.0,
        )
        assert order_id == "KITE789"
        call_kwargs = mock_kc.place_order.call_args[1]
        assert call_kwargs["price"] == 1500.0
        assert call_kwargs["order_type"] == "LIMIT"

    def test_tag_passed_when_provided(self, kite_client):
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "KITE_TAG"}
        client.place_order(
            tradingsymbol="TCS",
            exchange="NSE",
            transaction_type="SELL",
            quantity=2,
            order_type="MARKET",
            tag="algo-abc12345",
        )
        call_kwargs = mock_kc.place_order.call_args[1]
        assert call_kwargs["tag"] == "algo-abc12345"

    def test_no_access_token_raises_runtime_error(self):
        with patch("backend.algo.broker.kite_client.KiteConnect"):
            client = KiteClient(api_key="k", access_token=None)
            with pytest.raises(RuntimeError, match="access_token"):
                client.place_order(
                    tradingsymbol="RELIANCE",
                    exchange="NSE",
                    transaction_type="BUY",
                    quantity=1,
                    order_type="MARKET",
                )


# -----------------------------------------------------------------
# place_order — order type rejection
# -----------------------------------------------------------------

class TestPlaceOrderTypeRejection:
    @pytest.mark.parametrize("bad_type", ["SL", "SLM", "BO", "CO"])
    def test_rejected_order_types(self, kite_client, bad_type):
        client, _ = kite_client
        with pytest.raises(ValueError, match=bad_type):
            client.place_order(
                tradingsymbol="RELIANCE",
                exchange="NSE",
                transaction_type="BUY",
                quantity=1,
                order_type=bad_type,
            )

    def test_rejected_product_mis(self, kite_client):
        client, _ = kite_client
        with pytest.raises(ValueError, match="MIS"):
            client.place_order(
                tradingsymbol="RELIANCE",
                exchange="NSE",
                transaction_type="BUY",
                quantity=1,
                order_type="MARKET",
                product="MIS",
            )

    def test_rejected_variety_bo(self, kite_client):
        client, _ = kite_client
        with pytest.raises(ValueError, match="bo"):
            client.place_order(
                tradingsymbol="RELIANCE",
                exchange="NSE",
                transaction_type="BUY",
                quantity=1,
                order_type="MARKET",
                variety="bo",
            )


# -----------------------------------------------------------------
# cancel_order
# -----------------------------------------------------------------

class TestCancelOrder:
    def test_cancel_happy_path(self, kite_client):
        client, mock_kc = kite_client
        mock_kc.cancel_order.return_value = None
        returned = client.cancel_order("KITE123")
        assert returned == "KITE123"
        mock_kc.cancel_order.assert_called_once_with(
            variety="regular", order_id="KITE123",
        )

    def test_cancel_no_token_raises(self):
        with patch("backend.algo.broker.kite_client.KiteConnect"):
            client = KiteClient(api_key="k", access_token=None)
            with pytest.raises(RuntimeError, match="access_token"):
                client.cancel_order("ORD001")

    def test_cancel_kite_exception_propagates(self, kite_client):
        client, mock_kc = kite_client
        mock_kc.cancel_order.side_effect = Exception("Order not found")
        with pytest.raises(Exception, match="Order not found"):
            client.cancel_order("NONEXISTENT")


# -----------------------------------------------------------------
# modify_order
# -----------------------------------------------------------------

class TestModifyOrder:
    def test_modify_price_happy_path(self, kite_client):
        client, mock_kc = kite_client
        mock_kc.modify_order.return_value = None
        returned = client.modify_order(
            "ORD001", order_type="LIMIT", price=1600.0,
        )
        assert returned == "ORD001"
        call_kwargs = mock_kc.modify_order.call_args[1]
        assert call_kwargs["price"] == 1600.0

    def test_modify_non_limit_raises(self, kite_client):
        client, _ = kite_client
        with pytest.raises(ValueError, match="LIMIT"):
            client.modify_order("ORD001", order_type="MARKET")

    def test_modify_no_token_raises(self):
        with patch("backend.algo.broker.kite_client.KiteConnect"):
            client = KiteClient(api_key="k", access_token=None)
            with pytest.raises(RuntimeError, match="access_token"):
                client.modify_order("ORD001", price=100.0)
