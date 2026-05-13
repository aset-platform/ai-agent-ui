"""Tests for ``load_intraday_bars_window`` (ASETPLTFRM-400 slice 2).

Mix of:
- Edge-case unit tests (interval validation, look-ahead guard,
  empty-tickers, warmup-days arithmetic) — pure validation, no
  catalog read.
- Real-catalog roundtrip against the slice-1c-backfilled
  ``stocks.intraday_bars`` table. Verifies partition pruning
  works (single-ticker / single-month reads stay fast even on
  the 11M-row table) and that the loader's ``BarData`` output
  matches the slice-1e quality invariants.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.algo.backtest.data_source import (
    _SUPPORTED_INTRADAY_SECONDS,
    BackedFutureBarError,
    load_intraday_bars_window,
)

# ────────────────────────────────────────────────────────────────
# Argument validation (no catalog read)
# ────────────────────────────────────────────────────────────────


def test_supported_intraday_seconds_matches_kite_intervals():
    """Mirrors ``KiteClient._INTRADAY_INTERVAL_MAP``. Drift here
    means the loader silently rejects a cadence the keeper +
    writer accept (or vice versa)."""
    assert _SUPPORTED_INTRADAY_SECONDS == {60, 300, 900}


@pytest.mark.parametrize("bad", [0, 1, 30, 120, 180, 600, 1800])
def test_invalid_interval_sec_raises_before_catalog_read(bad):
    with pytest.raises(ValueError, match="interval_sec="):
        load_intraday_bars_window(
            tickers=["ITC.NS"],
            interval_sec=bad,
            period_start=date(2026, 5, 12),
            period_end=date(2026, 5, 13),
        )


def test_future_period_end_raises_backed_future_bar_error():
    """No look-ahead: ``period_end`` past today UTC is rejected
    by the data-source layer."""
    today = datetime.now(timezone.utc).date()
    with pytest.raises(BackedFutureBarError, match="past today"):
        load_intraday_bars_window(
            tickers=["ITC.NS"],
            interval_sec=900,
            period_start=today,
            period_end=today + timedelta(days=7),
        )


def test_inverted_window_raises_value_error():
    with pytest.raises(ValueError, match="period_start"):
        load_intraday_bars_window(
            tickers=["ITC.NS"],
            interval_sec=900,
            period_start=date(2026, 5, 13),
            period_end=date(2026, 5, 12),
        )


def test_negative_warmup_days_raises():
    with pytest.raises(ValueError, match="warmup_days"):
        load_intraday_bars_window(
            tickers=["ITC.NS"],
            interval_sec=900,
            period_start=date(2026, 5, 12),
            period_end=date(2026, 5, 13),
            warmup_days=-1,
        )


def test_empty_tickers_returns_empty_dict_no_catalog_read():
    """Caller passing ``tickers=[]`` should short-circuit to
    ``{}`` rather than execute a query that scans the whole
    table."""
    result = load_intraday_bars_window(
        tickers=[],
        interval_sec=900,
        period_start=date(2026, 5, 12),
        period_end=date(2026, 5, 13),
    )
    assert result == {}


# ────────────────────────────────────────────────────────────────
# Real-catalog roundtrip (uses the live backfilled table)
# ────────────────────────────────────────────────────────────────


def _live_table_has_oil() -> bool:
    """Skip live-catalog tests when OIL.NS isn't present —
    e.g. fresh dev box without the backfill having run."""
    try:
        from backend.db.duckdb_engine import query_iceberg_table

        rows = query_iceberg_table(
            "stocks.intraday_bars",
            "SELECT COUNT(*) AS c FROM intraday_bars "
            "WHERE ticker = 'OIL.NS'",
            [],
        )
        return bool(rows and rows[0]["c"] > 0)
    except Exception:
        return False


pytestmark_live = pytest.mark.skipif(
    not _live_table_has_oil(),
    reason="stocks.intraday_bars has no OIL.NS — run the backfill first",
)


@pytestmark_live
def test_load_single_day_oil_returns_25_bars():
    """One full NSE session at 15m = 25 closed bars. Our live
    catalog has 2026-05-13 fully populated for OIL.NS."""
    out = load_intraday_bars_window(
        tickers=["OIL.NS"],
        interval_sec=900,
        period_start=date(2026, 5, 13),
        period_end=date(2026, 5, 13),
    )
    assert set(out.keys()) == {"OIL.NS"}
    bars = out["OIL.NS"]
    assert len(bars) == 25, f"expected 25 × 15m bars, got {len(bars)}"


@pytestmark_live
def test_load_returns_bardata_with_bar_open_ts_ns_populated():
    """The intraday loader's contract is to populate
    ``bar_open_ts_ns`` (key for the slice-3 inner runner loop)
    in addition to ``date``."""
    out = load_intraday_bars_window(
        tickers=["OIL.NS"],
        interval_sec=900,
        period_start=date(2026, 5, 13),
        period_end=date(2026, 5, 13),
    )
    for bar in out["OIL.NS"]:
        assert bar.bar_open_ts_ns is not None
        assert bar.bar_open_ts_ns > 0
        assert bar.date == date(2026, 5, 13)
        assert bar.ticker == "OIL.NS"
        # Slice-1e OHLC self-consistency holds on live data
        assert bar.high >= bar.low
        assert bar.high >= max(bar.open, bar.close)
        assert bar.low <= min(bar.open, bar.close)


@pytestmark_live
def test_load_returns_bars_sorted_by_bar_open_ts_ns_ascending():
    """The runner's inner loop assumes bars are
    chronologically ordered — verify the loader hands them in
    that order."""
    out = load_intraday_bars_window(
        tickers=["OIL.NS"],
        interval_sec=900,
        period_start=date(2026, 5, 12),
        period_end=date(2026, 5, 13),
    )
    bars = out["OIL.NS"]
    ns_list = [b.bar_open_ts_ns for b in bars]
    assert ns_list == sorted(
        ns_list
    ), "loader must return bars in ascending bar_open_ts_ns order"


@pytestmark_live
def test_load_window_filters_to_requested_dates():
    """Two-day window should return exactly the bars within
    [start, end] — no leakage from adjacent days or older
    months."""
    out = load_intraday_bars_window(
        tickers=["OIL.NS"],
        interval_sec=900,
        period_start=date(2026, 5, 12),
        period_end=date(2026, 5, 13),
    )
    bars = out["OIL.NS"]
    dates = {b.date for b in bars}
    assert dates == {date(2026, 5, 12), date(2026, 5, 13)}
    # 25 bars × 2 days
    assert len(bars) == 50


@pytestmark_live
def test_load_with_warmup_extends_backward():
    """``warmup_days=5`` should fetch bars from up to 5 days
    before ``period_start``. For a window 2026-05-13 alone +
    warmup=5, we should see bars from at least one prior
    trading day."""
    out = load_intraday_bars_window(
        tickers=["OIL.NS"],
        interval_sec=900,
        period_start=date(2026, 5, 13),
        period_end=date(2026, 5, 13),
        warmup_days=5,
    )
    dates = {b.date for b in out["OIL.NS"]}
    # The warmup pulls calendar days 2026-05-08 → 2026-05-13.
    # NSE has trading days within that range; at least one of
    # the prior weekdays should be present.
    pre_window = {d for d in dates if d < date(2026, 5, 13)}
    assert pre_window, (
        "warmup_days=5 should have pulled prior trading days; "
        f"got only {dates}"
    )


@pytestmark_live
def test_unknown_ticker_absent_from_result_dict():
    """A ticker with no rows in the period is silently absent
    (mirrors load_ohlcv_window's contract)."""
    out = load_intraday_bars_window(
        tickers=["OIL.NS", "ZZZ_NOT_A_TICKER.NS"],
        interval_sec=900,
        period_start=date(2026, 5, 13),
        period_end=date(2026, 5, 13),
    )
    assert "OIL.NS" in out
    assert "ZZZ_NOT_A_TICKER.NS" not in out


@pytestmark_live
def test_close_prices_are_decimal_not_float():
    """Backtest engine assumes exact ``Decimal`` arithmetic —
    floats sneaking in would corrupt PnL by ~1e-15 per op,
    compounding into visible drift over 11M rows."""
    out = load_intraday_bars_window(
        tickers=["OIL.NS"],
        interval_sec=900,
        period_start=date(2026, 5, 13),
        period_end=date(2026, 5, 13),
    )
    for bar in out["OIL.NS"]:
        assert isinstance(bar.open, Decimal)
        assert isinstance(bar.high, Decimal)
        assert isinstance(bar.low, Decimal)
        assert isinstance(bar.close, Decimal)
