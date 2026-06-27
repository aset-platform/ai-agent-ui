"""Tests for freeze_cache._NSE_DEFAULTS immutability (Item B)
and KiteClient.quote() BSE prefix (Item C).
"""
from __future__ import annotations

from types import MappingProxyType
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.freeze_cache import _NSE_DEFAULTS
from backend.algo.broker.kite_client import KiteClient


# ---------------------------------------------------------------------------
# Item B — _NSE_DEFAULTS is a read-only MappingProxyType
# ---------------------------------------------------------------------------

class TestNseDefaultsImmutable:
    def test_reads_work(self):
        assert _NSE_DEFAULTS["largecap"] == 500_000
        assert _NSE_DEFAULTS["midcap"] == 100_000
        assert _NSE_DEFAULTS["smallcap"] == 50_000
        assert _NSE_DEFAULTS["unknown"] == 50_000

    def test_is_mapping_proxy(self):
        assert isinstance(_NSE_DEFAULTS, MappingProxyType)

    def test_mutation_raises_type_error(self):
        with pytest.raises(TypeError):
            _NSE_DEFAULTS["largecap"] = 999  # type: ignore[index]

    def test_new_key_raises_type_error(self):
        with pytest.raises(TypeError):
            _NSE_DEFAULTS["nanocap"] = 1_000  # type: ignore[index]

    def test_key_membership(self):
        assert "largecap" in _NSE_DEFAULTS
        assert "noncap" not in _NSE_DEFAULTS


# ---------------------------------------------------------------------------
# Item C — quote() uses BSE: prefix for .BO tickers
# ---------------------------------------------------------------------------

def _make_client(access_token: str = "tok") -> KiteClient:
    client = KiteClient(api_key="k", access_token=access_token)
    client._kc = MagicMock()
    return client


class TestKiteClientQuoteBsePrefix:
    def test_bse_ticker_uses_bse_prefix(self):
        """A .BO ticker must produce a BSE:-prefixed Kite key."""
        client = _make_client()
        client._kc.quote.return_value = {}
        with patch.object(client, "_hist_throttle"):
            client.quote([("500325.BO", 1)])
        client._kc.quote.assert_called_once_with(["BSE:500325"])

    def test_nse_ticker_uses_nse_prefix(self):
        """A .NS ticker must produce an NSE:-prefixed Kite key."""
        client = _make_client()
        client._kc.quote.return_value = {}
        with patch.object(client, "_hist_throttle"):
            client.quote([("RELIANCE.NS", 738561)])
        client._kc.quote.assert_called_once_with(["NSE:RELIANCE"])

    def test_mixed_batch_bse_and_nse(self):
        """Batch with one .BO and one .NS gets correct prefixes."""
        client = _make_client()
        client._kc.quote.return_value = {}
        with patch.object(client, "_hist_throttle"):
            client.quote([
                ("RELIANCE.NS", 738561),
                ("500325.BO", 1),
            ])
        client._kc.quote.assert_called_once_with(
            ["NSE:RELIANCE", "BSE:500325"],
        )

    def test_bse_result_mapped_back_to_bo_ticker(self):
        """Output dict is keyed by the original .BO ticker."""
        client = _make_client()
        client._kc.quote.return_value = {
            "BSE:500325": {
                "ohlc": {
                    "open": 2870.0, "high": 2895.0,
                    "low": 2860.0, "close": 2880.0,
                },
                "last_price": 2882.0,
                "volume": 100_000,
                "last_trade_time": None,
            }
        }
        with patch.object(client, "_hist_throttle"):
            out = client.quote([("500325.BO", 1)])
        assert "500325.BO" in out
        assert out["500325.BO"]["last_price"] == 2882.0
