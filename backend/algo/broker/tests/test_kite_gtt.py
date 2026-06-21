"""Unit tests for KiteClient GTT methods."""
from unittest.mock import MagicMock

import pytest

from backend.algo.broker.kite_client import KiteClient


def _make_client() -> KiteClient:
    """KiteClient with mocked KiteConnect internals."""
    client = KiteClient.__new__(KiteClient)
    client._kc = MagicMock()
    client._kc.GTT_TYPE_SINGLE = "single"
    client._dry_run = False
    client._access_token = "tok"
    return client


class TestPlaceGtt:
    def test_returns_gtt_id(self):
        client = _make_client()
        client._kc.place_gtt.return_value = {"trigger_id": 12345}
        gtt_id = client.place_gtt(
            ticker="RELIANCE.NS",
            trigger_price=4750.0,
            limit_price=4702.5,
            qty=10,
        )
        assert gtt_id == 12345

    def test_uses_single_trigger_type(self):
        client = _make_client()
        client._kc.place_gtt.return_value = {"trigger_id": 1}
        client.place_gtt(
            ticker="RELIANCE.NS",
            trigger_price=4750.0,
            limit_price=4702.5,
            qty=10,
        )
        call_kwargs = client._kc.place_gtt.call_args[1]
        assert call_kwargs["trigger_type"] == "single"

    def test_strips_ns_suffix(self):
        client = _make_client()
        client._kc.place_gtt.return_value = {"trigger_id": 1}
        client.place_gtt(
            ticker="INFY.NS",
            trigger_price=1000.0,
            limit_price=990.0,
            qty=5,
        )
        call_kwargs = client._kc.place_gtt.call_args[1]
        assert call_kwargs["tradingsymbol"] == "INFY"
        assert call_kwargs["exchange"] == "NSE"

    def test_order_is_limit_sell(self):
        client = _make_client()
        client._kc.place_gtt.return_value = {"trigger_id": 1}
        client.place_gtt(
            ticker="TCS.NS",
            trigger_price=3000.0,
            limit_price=2970.0,
            qty=2,
        )
        orders = client._kc.place_gtt.call_args[1]["orders"]
        assert len(orders) == 1
        assert orders[0]["transaction_type"] == "SELL"
        assert orders[0]["order_type"] == "LIMIT"
        assert orders[0]["price"] == 2970.0

    def test_trigger_values_contains_trigger_price(self):
        client = _make_client()
        client._kc.place_gtt.return_value = {"trigger_id": 7}
        client.place_gtt(
            ticker="WIPRO.NS",
            trigger_price=500.0,
            limit_price=495.0,
            qty=20,
        )
        call_kwargs = client._kc.place_gtt.call_args[1]
        assert call_kwargs["trigger_values"] == [500.0]

    def test_dry_run_returns_zero_without_calling_kite(self):
        client = _make_client()
        client._dry_run = True
        gtt_id = client.place_gtt(
            ticker="HDFC.NS",
            trigger_price=1500.0,
            limit_price=1485.0,
            qty=3,
        )
        assert gtt_id == 0
        client._kc.place_gtt.assert_not_called()


class TestDeleteGtt:
    def test_calls_delete(self):
        client = _make_client()
        client._kc.delete_gtt.return_value = {"trigger_id": 99}
        client.delete_gtt(99)
        client._kc.delete_gtt.assert_called_once_with(trigger_id=99)

    def test_noop_on_already_triggered_exception(self):
        from kiteconnect.exceptions import InputException
        client = _make_client()
        client._kc.delete_gtt.side_effect = InputException(
            "GTT already triggered"
        )
        # Must not raise
        client.delete_gtt(999)

    def test_dry_run_does_not_call_kite(self):
        client = _make_client()
        client._dry_run = True
        client.delete_gtt(42)
        client._kc.delete_gtt.assert_not_called()


class TestGetGtts:
    def test_returns_list(self):
        client = _make_client()
        client._kc.get_gtts.return_value = [
            {"id": 1, "status": "active"},
            {"id": 2, "status": "triggered"},
        ]
        result = client.get_gtts()
        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_returns_empty_list_on_network_error(self):
        from kiteconnect.exceptions import NetworkException
        client = _make_client()
        client._kc.get_gtts.side_effect = NetworkException("timeout")
        result = client.get_gtts()
        assert result == []

    def test_returns_empty_list_on_generic_error(self):
        client = _make_client()
        client._kc.get_gtts.side_effect = RuntimeError("unexpected")
        result = client.get_gtts()
        assert result == []
