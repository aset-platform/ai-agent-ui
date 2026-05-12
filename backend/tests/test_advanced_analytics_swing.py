"""Tests for swing-setups feature."""

from __future__ import annotations

import numpy as np
import pandas as pd

from advanced_analytics_models import AdvancedRow
from advanced_analytics_routes import (
    _death_cross_days_ago,
    _rolling_band_20d_prev,
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
