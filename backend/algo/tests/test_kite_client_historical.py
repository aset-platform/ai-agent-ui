"""Tests for KiteClient.fetch_daily_historical (ASETPLTFRM-383).

Covers:
- Happy path: SDK rows → BarData list, ascending by date.
- NaN-cell rejection consistent with data_source._safe_decimal.
- Missing access_token → RuntimeError (defence at the boundary).
- Rate-limit throttle: two back-to-back calls separated by
  ``_HIST_MIN_INTERVAL_S``.
- ``date`` field accepts datetime, date, or ISO string.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.kite_client import KiteClient


@pytest.fixture()
def kite_client():
    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        client = KiteClient(
            api_key="test_api_key",
            access_token="test_access_token",
            dry_run=False,
        )
        client._kc = kc_instance
        # Reset the class-level throttle clock so previous tests
        # don't bleed into rate-limit assertions here.
        KiteClient._hist_last_call_ts = 0.0
        yield client, kc_instance


def _candle(d, close=100.0):
    """SDK-shaped candle (date may be datetime or date)."""
    return {
        "date": d,
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 1000,
    }


def test_happy_path_returns_bardata_ascending(kite_client):
    client, kc = kite_client
    kc.historical_data.return_value = [
        _candle(datetime(2026, 5, 9, 9, 15), close=101),
        _candle(datetime(2026, 5, 8, 9, 15), close=100),
        _candle(datetime(2026, 5, 11, 9, 15), close=103),
    ]
    out = client.fetch_daily_historical(
        ticker="ITC", instrument_token=12345,
        n_bars=3, end=date(2026, 5, 11),
    )
    assert [b.date for b in out] == [
        date(2026, 5, 8), date(2026, 5, 9), date(2026, 5, 11),
    ]
    assert all(b.ticker == "ITC" for b in out)
    assert out[-1].close == Decimal("103")
    assert out[-1].volume == 1000


def test_nan_cells_skipped(kite_client):
    client, kc = kite_client
    kc.historical_data.return_value = [
        {
            "date": date(2026, 5, 8),
            "open": float("nan"), "high": 1, "low": 1,
            "close": 1, "volume": 0,
        },
        _candle(date(2026, 5, 9), close=100),
    ]
    out = client.fetch_daily_historical(
        ticker="ITC", instrument_token=1,
        n_bars=5, end=date(2026, 5, 9),
    )
    assert len(out) == 1
    assert out[0].date == date(2026, 5, 9)


def test_no_access_token_raises():
    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        MockKC.return_value = MagicMock()
        client = KiteClient(
            api_key="k", access_token=None, dry_run=False,
        )
    with pytest.raises(RuntimeError, match="access_token"):
        client.fetch_daily_historical(
            ticker="ITC", instrument_token=1,
            n_bars=10, end=date(2026, 5, 11),
        )


def test_iso_string_date_accepted(kite_client):
    client, kc = kite_client
    kc.historical_data.return_value = [
        {
            "date": "2026-05-09T09:15:00+05:30",
            "open": 100, "high": 101, "low": 99,
            "close": 100, "volume": 50,
        },
    ]
    out = client.fetch_daily_historical(
        ticker="ITC", instrument_token=1,
        n_bars=1, end=date(2026, 5, 9),
    )
    assert out[0].date == date(2026, 5, 9)


def test_rate_limit_throttle_serialises_calls(kite_client):
    """Two back-to-back calls must be ≥_HIST_MIN_INTERVAL_S apart.

    Verifies the throttle is actually engaging. We don't assert the
    full 0.34s sleep (CI clock jitter) — just that the elapsed time
    between calls is materially > 0 when called rapidly.
    """
    client, kc = kite_client
    kc.historical_data.return_value = []
    t0 = time.monotonic()
    client.fetch_daily_historical(
        ticker="A", instrument_token=1, n_bars=1,
        end=date(2026, 5, 11),
    )
    client.fetch_daily_historical(
        ticker="B", instrument_token=2, n_bars=1,
        end=date(2026, 5, 11),
    )
    elapsed = time.monotonic() - t0
    # First call has no preceding throttle (clock reset in fixture);
    # second is gated by _HIST_MIN_INTERVAL_S. Lower bound 0.25s
    # leaves headroom for jitter; upper bound checks we're not
    # accidentally sleeping orders of magnitude longer.
    assert 0.25 <= elapsed <= 1.5
