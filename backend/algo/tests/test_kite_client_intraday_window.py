"""Tests for KiteClient.fetch_intraday_historical_window
(ASETPLTFRM-400 slice 1a).

Covers:
- Chunking math: 4-yr 15m window splits into ⌈1461 / 200⌉ = 8 calls.
- Concatenation across chunks, ascending by bar_open_ts_ns.
- Duplicate bar (same bar_open_ts_ns from a chunk-boundary overlap)
  is dropped.
- Tail-trim: a partially-built bar at the very end is removed.
- start > end → empty list (no Kite calls).
- Invalid interval → ValueError before any Kite call.
- Missing access_token → RuntimeError before any Kite call.
- Rate-limit throttle is invoked once per chunk.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.kite_client import KiteClient

IST_OFFSET_MIN = 330  # UTC+5:30


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
        # Reset the class-level throttle so prior tests don't bleed
        # in.
        KiteClient._hist_last_call_ts = 0.0
        yield client, kc_instance


def _candle(ts_aware, close=100.0):
    """Kite SDK-shaped intraday candle (tz-aware datetime)."""
    return {
        "date": ts_aware,
        "open": close - 0.5,
        "high": close + 0.5,
        "low": close - 1.0,
        "close": close,
        "volume": 100,
    }


def _ist(year, month, day, hour, minute):
    """IST tz-aware datetime — matches what Kite returns."""
    from datetime import timedelta as _td

    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=timezone(_td(minutes=IST_OFFSET_MIN)),
    )


def test_window_chunks_into_kite_caps(kite_client):
    """4-year 15m window: 1461 calendar days at 200-day cap → 8
    Kite calls."""
    client, kc = kite_client
    kc.historical_data.return_value = []
    client.fetch_intraday_historical_window(
        ticker="ITC",
        instrument_token=12345,
        interval_sec=900,
        start=date(2022, 5, 13),
        end=date(2026, 5, 13),
    )
    # ceil(1461 / 200) = 8
    assert kc.historical_data.call_count == 8


def test_5m_window_uses_100_day_chunks(kite_client):
    client, kc = kite_client
    kc.historical_data.return_value = []
    client.fetch_intraday_historical_window(
        ticker="ITC",
        instrument_token=12345,
        interval_sec=300,
        start=date(2026, 1, 1),
        end=date(2026, 5, 13),
    )
    # 133 days / 100 = 2 calls
    assert kc.historical_data.call_count == 2


def test_1m_window_uses_60_day_chunks(kite_client):
    client, kc = kite_client
    kc.historical_data.return_value = []
    client.fetch_intraday_historical_window(
        ticker="ITC",
        instrument_token=12345,
        interval_sec=60,
        start=date(2026, 3, 1),
        end=date(2026, 5, 13),
    )
    # 74 days / 60 = 2 calls
    assert kc.historical_data.call_count == 2


def test_concatenates_bars_ascending(kite_client):
    """Two chunks return bars from different days; concatenation
    should be ascending by bar_open_ts_ns."""
    client, kc = kite_client
    # Force the 5-min cap (100 days) so the call splits.
    # Chunk 1: 2026-01-01 → 2026-04-10 (covers ITC bar 2026-04-01).
    # Chunk 2: 2026-04-11 → 2026-05-13 (covers bars 2026-05-01).
    chunk1 = [_candle(_ist(2026, 4, 1, 9, 15), close=100)]
    chunk2 = [_candle(_ist(2026, 5, 1, 9, 15), close=110)]
    kc.historical_data.side_effect = [chunk1, chunk2]
    out = client.fetch_intraday_historical_window(
        ticker="ITC",
        instrument_token=12345,
        interval_sec=300,
        start=date(2026, 1, 1),
        end=date(2026, 5, 13),
    )
    assert [b.date for b in out] == [
        date(2026, 4, 1),
        date(2026, 5, 1),
    ]
    assert out[0].close == Decimal("100")
    assert out[1].close == Decimal("110")
    # Ascending by bar_open_ts_ns enforced.
    assert out[0].bar_open_ts_ns < out[1].bar_open_ts_ns


def test_duplicate_bar_at_chunk_boundary_deduped(kite_client):
    """If Kite returns the same bar in two overlapping chunks
    (same ``bar_open_ts_ns``), keep one copy."""
    client, kc = kite_client
    same_bar = _candle(_ist(2026, 4, 1, 9, 15), close=100)
    chunk1 = [same_bar]
    chunk2 = [same_bar]  # same ts → dedup
    kc.historical_data.side_effect = [chunk1, chunk2]
    out = client.fetch_intraday_historical_window(
        ticker="ITC",
        instrument_token=12345,
        interval_sec=300,
        start=date(2026, 1, 1),
        end=date(2026, 5, 13),
    )
    assert len(out) == 1


def test_partial_tail_bar_dropped(kite_client):
    """A bar whose window has not yet closed (open + interval >
    now) must be removed."""
    client, kc = kite_client
    from datetime import timedelta as _td

    now = datetime.now(timezone.utc)
    # bar opened 1 minute ago, 15-minute interval → window still
    # open for ~14 more minutes; must be dropped.
    open_bar_ts = now.astimezone(timezone(_td(minutes=IST_OFFSET_MIN))) - _td(
        minutes=1
    )
    # A clearly-closed bar from yesterday.
    closed_bar_ts = open_bar_ts - _td(hours=24)
    kc.historical_data.return_value = [
        _candle(closed_bar_ts, close=100),
        _candle(open_bar_ts, close=200),
    ]
    out = client.fetch_intraday_historical_window(
        ticker="ITC",
        instrument_token=12345,
        interval_sec=900,
        start=open_bar_ts.date() - _td(days=2),
        end=open_bar_ts.date(),
    )
    assert len(out) == 1
    assert out[0].close == Decimal("100")


def test_start_after_end_returns_empty_without_kite_call(
    kite_client,
):
    client, kc = kite_client
    out = client.fetch_intraday_historical_window(
        ticker="ITC",
        instrument_token=12345,
        interval_sec=900,
        start=date(2026, 5, 13),
        end=date(2026, 5, 12),
    )
    assert out == []
    kc.historical_data.assert_not_called()


def test_invalid_interval_raises_before_kite_call(kite_client):
    client, kc = kite_client
    with pytest.raises(ValueError, match="not supported"):
        client.fetch_intraday_historical_window(
            ticker="ITC",
            instrument_token=12345,
            interval_sec=180,
            start=date(2026, 5, 1),
            end=date(2026, 5, 13),
        )
    kc.historical_data.assert_not_called()


def test_missing_access_token_raises():
    """Defence at the SDK boundary — no token, no call."""
    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        client = KiteClient(
            api_key="test_api_key",
            access_token=None,
            dry_run=False,
        )
        client._kc = kc_instance
        with pytest.raises(RuntimeError, match="access_token"):
            client.fetch_intraday_historical_window(
                ticker="ITC",
                instrument_token=12345,
                interval_sec=900,
                start=date(2026, 5, 1),
                end=date(2026, 5, 13),
            )
        kc_instance.historical_data.assert_not_called()


def test_throttle_invoked_per_chunk(kite_client):
    """Each Kite call must go through ``_hist_throttle`` —
    serial 200-day chunks back-to-back would burst Kite's 3 req/s
    cap otherwise."""
    client, kc = kite_client
    kc.historical_data.return_value = []
    with patch.object(KiteClient, "_hist_throttle") as mock_throttle:
        client.fetch_intraday_historical_window(
            ticker="ITC",
            instrument_token=12345,
            interval_sec=900,
            start=date(2022, 5, 13),
            end=date(2026, 5, 13),
        )
    # 8 chunks → 8 throttle calls.
    assert mock_throttle.call_count == 8


def test_back_to_back_chunks_respect_min_interval(kite_client):
    """Two real chunks must be separated by at least
    ``_HIST_MIN_INTERVAL_S`` of wall-clock time."""
    client, kc = kite_client
    kc.historical_data.return_value = []
    t0 = time.monotonic()
    client.fetch_intraday_historical_window(
        ticker="ITC",
        instrument_token=12345,
        interval_sec=300,
        start=date(2026, 1, 1),
        end=date(2026, 5, 13),
    )
    elapsed = time.monotonic() - t0
    # 2 chunks → at least one throttled wait of ~0.34s. Allow
    # generous slack to keep CI stable (>= 0.30s).
    assert elapsed >= 0.30, (
        f"Expected ≥ 0.30s for throttled 2-chunk window, "
        f"got {elapsed:.3f}s"
    )
