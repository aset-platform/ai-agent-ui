"""Tests for KiteClient.quote() — live OHLC + LTP + volume fetch."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.kite_client import KiteClient


def _make_client(access_token="tok"):
    client = KiteClient(
        api_key="k",
        access_token=access_token,
    )
    client._kc = MagicMock()
    return client


class TestKiteClientQuote:
    def test_happy_path_single_ticker(self):
        client = _make_client()
        ltt = datetime(
            2026, 6, 1, 10, 42, 30, tzinfo=timezone.utc,
        )
        client._kc.quote.return_value = {
            "NSE:RELIANCE": {
                "ohlc": {
                    "open": 2870.0,
                    "high": 2895.3,
                    "low": 2861.5,
                    "close": 2882.1,
                },
                "last_price": 2884.4,
                "volume": 1_842_900,
                "last_trade_time": ltt,
            }
        }
        with patch.object(client, "_hist_throttle") as throttle:
            out = client.quote(
                [("RELIANCE.NS", 738561)],
            )
        throttle.assert_called_once()
        client._kc.quote.assert_called_once_with(
            ["NSE:RELIANCE"],
        )
        assert "RELIANCE.NS" in out
        bar = out["RELIANCE.NS"]
        assert bar["open"] == 2870.0
        assert bar["high"] == 2895.3
        assert bar["low"] == 2861.5
        assert bar["close"] == 2882.1
        assert bar["last_price"] == 2884.4
        assert bar["volume"] == 1_842_900
        assert bar["last_trade_time"] == ltt

    def test_strips_ns_suffix_for_kite_key(self):
        client = _make_client()
        client._kc.quote.return_value = {}
        client.quote([("RELIANCE.NS", 738561)])
        client._kc.quote.assert_called_once_with(
            ["NSE:RELIANCE"],
        )

    def test_no_access_token_raises(self):
        client = _make_client(access_token=None)
        with pytest.raises(RuntimeError, match="access_token"):
            client.quote([("RELIANCE.NS", 738561)])

    def test_empty_kite_response_returns_empty_dict(self):
        client = _make_client()
        client._kc.quote.return_value = {}
        out = client.quote([("RELIANCE.NS", 738561)])
        assert out == {}

    def test_missing_ohlc_block_skips_ticker(self):
        client = _make_client()
        client._kc.quote.return_value = {
            "NSE:RELIANCE": {"last_price": 2884.4},
            # ohlc key missing entirely
        }
        out = client.quote([("RELIANCE.NS", 738561)])
        # Missing ohlc → defaults to zeros, but ticker is still
        # in the output dict. Real callers ignore zero bars.
        assert "RELIANCE.NS" in out
        assert out["RELIANCE.NS"]["open"] == 0.0

    def test_batch_two_tickers(self):
        client = _make_client()
        client._kc.quote.return_value = {
            "NSE:RELIANCE": {
                "ohlc": {
                    "open": 2870, "high": 2895,
                    "low": 2861, "close": 2882,
                },
                "last_price": 2884.4,
                "volume": 1_000_000,
                "last_trade_time": None,
            },
            "NSE:INFY": {
                "ohlc": {
                    "open": 1450, "high": 1465,
                    "low": 1442, "close": 1458,
                },
                "last_price": 1460.0,
                "volume": 500_000,
                "last_trade_time": None,
            },
        }
        out = client.quote([
            ("RELIANCE.NS", 738561),
            ("INFY.NS", 408065),
        ])
        client._kc.quote.assert_called_once_with(
            ["NSE:RELIANCE", "NSE:INFY"],
        )
        assert set(out.keys()) == {"RELIANCE.NS", "INFY.NS"}
