"""Tests for swing-setups feature."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from advanced_analytics_models import AdvancedRow
from advanced_analytics_routes import (
    _death_cross_days_ago,
    _rolling_band_20d_prev,
    _rsi_lookback,
)


def test_advanced_row_swing_fields_default_none() -> None:
    """New swing fields default to None for back-compat with the
    seven existing AA reports that don't populate them.
    """
    row = AdvancedRow(ticker="TCS.NS")
    assert row.death_cross_days_ago is None
    assert row.rolling_low_20d_prev is None
    assert row.rolling_high_20d_prev is None
    assert row.rsi_3d_ago is None
    assert row.rsi_max_10d is None
    assert row.rec_category is None
    assert row.rec_severity is None
    assert row.rec_expected_return_pct is None


def _make_sma_df(s50: list[float], s200: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"SMA_50": s50, "SMA_200": s200})


def test_death_cross_none_when_50_above_200() -> None:
    """No death cross active when SMA-50 is above SMA-200 today."""
    df = _make_sma_df([100, 102, 104], [99, 100, 101])
    assert _death_cross_days_ago(df) is None


def test_death_cross_zero_when_today_is_cross() -> None:
    """Cross today → 0."""
    df = _make_sma_df([100, 101, 99], [98, 100, 100])
    # Yesterday: 50=101 > 200=100. Today: 50=99 < 200=100 → cross today.
    assert _death_cross_days_ago(df) == 0


def test_death_cross_n_days_back() -> None:
    """Cross 2 trading days back → 2."""
    df = _make_sma_df([101, 99, 98, 97], [100, 100, 99, 98])
    # Index 0→1 is cross (101>100 then 99<100). Today (i=3): 97<98.
    # n=4, cross at i=1 → (4-1)-1 = 2.
    assert _death_cross_days_ago(df) == 2


def test_death_cross_sentinel_when_below_entire_window() -> None:
    """SMA-50 below SMA-200 entire window with no cross → 999."""
    df = _make_sma_df([90, 91, 92], [100, 101, 102])
    assert _death_cross_days_ago(df) == 999


def test_death_cross_handles_nan_prefix() -> None:
    """NaN prefix (insufficient warmup) returns 999 sentinel."""
    df = _make_sma_df([np.nan, np.nan, 95], [np.nan, np.nan, 100])
    assert _death_cross_days_ago(df) == 999


def test_death_cross_missing_columns_returns_none() -> None:
    """Missing SMA columns return None safely."""
    df = pd.DataFrame({"close": [100, 101]})
    assert _death_cross_days_ago(df) is None


def test_rolling_band_20d_prev_basic() -> None:
    """20-day rolling band excludes today."""
    # 21 rows: index 0..19 used for the band, index 20 is "today".
    lows = list(range(10, 30)) + [5]  # today_low = 5 (below band)
    highs = list(range(20, 40)) + [50]  # today_high = 50 (above)
    df = pd.DataFrame({"low": lows, "high": highs})
    low, high = _rolling_band_20d_prev(df)
    assert low == 10  # min of 10..29 (indices 0..19)
    assert high == 39  # max of 20..39 (indices 0..19)


def test_rolling_band_short_history_returns_none() -> None:
    """Fewer than 21 rows → cannot exclude today, returns (None, None)."""
    df = pd.DataFrame({
        "low": [10, 11, 12],
        "high": [15, 16, 17],
    })
    assert _rolling_band_20d_prev(df) == (None, None)


def test_rolling_band_handles_nan() -> None:
    """NaN low/high values are ignored in min/max."""
    lows = [float("nan")] * 5 + list(range(10, 25)) + [5]
    highs = [float("nan")] * 5 + list(range(20, 35)) + [50]
    df = pd.DataFrame({"low": lows, "high": highs})
    low, high = _rolling_band_20d_prev(df)
    assert low == 10
    assert high == 34


def test_rsi_lookback_basic() -> None:
    """RSI lookback: today, 3-days-ago, max over last 10."""
    rsi_series = pd.Series(
        [40, 45, 50, 55, 60, 65, 70, 68, 60, 50, 45]
    )
    df = pd.DataFrame({"RSI_14": rsi_series})
    today, three_ago, max_10 = _rsi_lookback(df)
    assert today == 45
    assert three_ago == 68  # index -4 (3 trading days before today)
    assert max_10 == 70  # max over last 10 rows


def test_rsi_lookback_short_series_returns_partial_nones() -> None:
    """<4 rows → three_ago None; <10 rows → max_10 still computes
    over available rows."""
    df = pd.DataFrame({"RSI_14": [40, 50, 60]})
    today, three_ago, max_10 = _rsi_lookback(df)
    assert today == 60
    assert three_ago is None
    assert max_10 == 60  # max of the 3 available


def test_rsi_lookback_missing_column_returns_all_none() -> None:
    df = pd.DataFrame({"close": [100, 101]})
    assert _rsi_lookback(df) == (None, None, None)


def test_rsi_lookback_handles_nan() -> None:
    """NaN today returns None for today; lookback unaffected."""
    df = pd.DataFrame({
        "RSI_14": [40, 50, 60, 65, 55, float("nan")],
    })
    today, three_ago, max_10 = _rsi_lookback(df)
    assert today is None
    assert three_ago == 60
    assert max_10 == 65


def test_build_row_populates_swing_computed_cols() -> None:
    """Integration: _build_row stamps the 5 swing computed cols +
    today_low when ohlcv_g + indicators dict carry the right inputs.
    """
    import advanced_analytics_routes as aar

    n = 30
    dates = pd.date_range("2026-04-01", periods=n).date
    ohlcv_g = pd.DataFrame({
        "date": dates,
        "open": [100.0] * n,
        "high": [105.0 + i for i in range(n)],
        "low": [95.0 - i * 0.1 for i in range(n)],
        "close": [100.0 + i * 0.5 for i in range(n)],
        "volume": [1_000_000] * n,
    })

    # Indicators dict — mirror contract _load_indicators_latest emits.
    indicators = {
        "rsi_14": 55.0,
        "sma_50": 110.0,
        "sma_200": 105.0,
        "golden_cross_days_ago": 999,
        # Pre-computed by _load_indicators_latest (Task 5 extension):
        "death_cross_days_ago": None,
        "rsi_3d_ago": 60.0,
        "rsi_max_10d": 65.0,
    }

    row = aar._build_row(
        ticker="TCS.NS",
        ohlcv_g=ohlcv_g,
        delivery_g=None,
        indicators=indicators,
        funds=None,
        prom=None,
        event=None,
        pscore=None,
        company=None,
    )

    # Today snapshot — last row of OHLCV.
    assert row.today_low == pytest.approx(95.0 - 29 * 0.1)
    # Indicator-dict-derived fields.
    assert row.death_cross_days_ago is None
    assert row.rsi_3d_ago == 60.0
    assert row.rsi_max_10d == 65.0
    # OHLCV-derived band (computed inside _build_row).
    # Window: indices 9..28 (last 20 before today).
    # low = min(95 - i*0.1) for i in 9..28 → at i=28 → 95 - 2.8 = 92.2.
    # high = max(105 + i) for i in 9..28 → at i=28 → 133.
    assert row.rolling_low_20d_prev == pytest.approx(92.2)
    assert row.rolling_high_20d_prev == pytest.approx(133.0)
