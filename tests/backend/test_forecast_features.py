"""Tests for tools._forecast_features — Tier 1/2 feature computation."""

import math
from datetime import date, timedelta

import pandas as pd
import pytest

from tools._forecast_features import (
    _days_to_expiry,
    _days_to_nearest_earnings,
    _safe_growth,
    build_future_features,
    compute_tier1_features,
    compute_tier2_features,
    get_sector_index_mapping,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _last_thursday(dt: date) -> date:
    """Return the last Thursday of *dt*'s month."""
    # Walk backwards from end of month
    if dt.month == 12:
        last_day = date(dt.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(dt.year, dt.month + 1, 1) - timedelta(days=1)
    offset = (last_day.weekday() - 3) % 7  # Thursday = 3
    return last_day - timedelta(days=offset)


def _make_ohlcv(n: int = 30) -> pd.DataFrame:
    """Create a minimal OHLCV DataFrame with *n* rows."""
    base = date(2025, 1, 2)
    dates = [base + timedelta(days=i) for i in range(n)]
    prices = [100.0 + i * 0.5 for i in range(n)]
    volumes = [1_000_000 + i * 1000 for i in range(n)]
    return pd.DataFrame(
        {"date": dates, "close": prices, "volume": volumes}
    )


# ---------------------------------------------------------------------------
# TestSafeGrowth
# ---------------------------------------------------------------------------

class TestSafeGrowth:
    """_safe_growth handles edge cases gracefully."""

    def test_normal_positive(self):
        assert abs(_safe_growth(110.0, 100.0) - 0.1) < 1e-9

    def test_normal_negative(self):
        assert abs(_safe_growth(90.0, 100.0) - (-0.1)) < 1e-9

    def test_capped_at_plus_one(self):
        assert _safe_growth(1000.0, 1.0) == 1.0

    def test_capped_at_minus_one(self):
        assert _safe_growth(0.0, 1000.0) == -1.0

    def test_zero_prev_returns_zero(self):
        assert _safe_growth(100.0, 0.0) == 0.0

    def test_none_prev_returns_zero(self):
        assert _safe_growth(100.0, None) == 0.0

    def test_none_curr_returns_zero(self):
        assert _safe_growth(None, 100.0) == 0.0

    def test_nan_returns_zero(self):
        assert _safe_growth(float("nan"), 100.0) == 0.0
        assert _safe_growth(100.0, float("nan")) == 0.0


# ---------------------------------------------------------------------------
# TestComputeTier1
# ---------------------------------------------------------------------------

class TestComputeTier1:
    """compute_tier1_features extracts features from Iceberg rows."""

    def _analysis(self, **kwargs):
        base = {
            "annualized_volatility": 20.0,
            "bull_phase_pct": 60.0,
            "bear_phase_pct": 30.0,
            "support_level": 90.0,
            "resistance_level": 110.0,
        }
        base.update(kwargs)
        return base

    def test_analysis_summary_features(self):
        row = self._analysis()
        feats = compute_tier1_features(
            analysis_row=row,
            piotroski_row=None,
            quarterly_rows=[],
            current_price=100.0,
        )
        assert abs(feats["volatility_regime"] - 0.2) < 1e-9
        assert abs(feats["trend_strength"] - 0.3) < 1e-9
        # sr_position: (100 - 90) / (110 - 90) = 0.5
        assert abs(feats["sr_position"] - 0.5) < 1e-9

    def test_piotroski_feature(self):
        feats = compute_tier1_features(
            analysis_row=self._analysis(),
            piotroski_row={"f_score": 7},
            quarterly_rows=[],
            current_price=100.0,
        )
        expected = 7 / 9
        assert abs(feats["piotroski"] - expected) < 1e-9

    def test_quarterly_growth(self):
        rows = [
            {"total_revenue": 200.0, "diluted_eps": 2.0},
            {"total_revenue": 100.0, "diluted_eps": 1.0},
        ]
        feats = compute_tier1_features(
            analysis_row=self._analysis(),
            piotroski_row=None,
            quarterly_rows=rows,
            current_price=100.0,
        )
        assert abs(feats["revenue_growth"] - 1.0) < 1e-9
        assert abs(feats["eps_growth"] - 1.0) < 1e-9

    def test_growth_capped_at_1(self):
        rows = [
            {"total_revenue": 10000.0, "diluted_eps": 9.0},
            {"total_revenue": 1.0, "diluted_eps": 0.1},
        ]
        feats = compute_tier1_features(
            analysis_row=self._analysis(),
            piotroski_row=None,
            quarterly_rows=rows,
            current_price=100.0,
        )
        assert feats["revenue_growth"] == 1.0
        assert feats["eps_growth"] == 1.0

    def test_missing_everything_returns_defaults(self):
        feats = compute_tier1_features(
            analysis_row=None,
            piotroski_row=None,
            quarterly_rows=[],
            current_price=100.0,
        )
        for key in (
            "volatility_regime",
            "trend_strength",
            "sr_position",
            "piotroski",
            "revenue_growth",
            "eps_growth",
        ):
            assert feats[key] == 0.0, f"{key} should default to 0.0"

    def test_sr_position_clipped_to_zero_one(self):
        """Price below support → 0.0, above resistance → 1.0."""
        row = self._analysis(
            support_level=100.0, resistance_level=110.0
        )
        feats_low = compute_tier1_features(
            analysis_row=row,
            piotroski_row=None,
            quarterly_rows=[],
            current_price=80.0,
        )
        assert feats_low["sr_position"] == 0.0

        feats_high = compute_tier1_features(
            analysis_row=row,
            piotroski_row=None,
            quarterly_rows=[],
            current_price=120.0,
        )
        assert feats_high["sr_position"] == 1.0

    def test_only_one_quarterly_row_zero_growth(self):
        rows = [{"total_revenue": 200.0, "diluted_eps": 2.0}]
        feats = compute_tier1_features(
            analysis_row=self._analysis(),
            piotroski_row=None,
            quarterly_rows=rows,
            current_price=100.0,
        )
        assert feats["revenue_growth"] == 0.0
        assert feats["eps_growth"] == 0.0


# ---------------------------------------------------------------------------
# TestComputeTier2
# ---------------------------------------------------------------------------

class TestComputeTier2:
    """compute_tier2_features computes market microstructure features."""

    def test_volume_anomaly(self):
        df = _make_ohlcv(25)
        # Last volume is 1_024_000 (24 * 1000 + 1_000_000)
        feats = compute_tier2_features(
            ohlcv_df=df,
            sector_index_df=None,
            earnings_dates=None,
        )
        assert "volume_anomaly" in feats
        # Value is a finite float
        assert math.isfinite(feats["volume_anomaly"])

    def test_obv_trend_present(self):
        df = _make_ohlcv(25)
        feats = compute_tier2_features(
            ohlcv_df=df,
            sector_index_df=None,
            earnings_dates=None,
        )
        assert "obv_trend" in feats
        assert math.isfinite(feats["obv_trend"])

    def test_calendar_features(self):
        # Build df ending on a known date: 2025-01-31 (Friday)
        base = date(2025, 1, 1)
        dates = [base + timedelta(days=i) for i in range(31)]
        df = pd.DataFrame(
            {
                "date": dates,
                "close": [100.0] * 31,
                "volume": [1_000_000] * 31,
            }
        )
        feats = compute_tier2_features(
            ohlcv_df=df,
            sector_index_df=None,
            earnings_dates=None,
        )
        last = dates[-1]  # 2025-01-31
        expected_dow = last.weekday() / 4.0
        expected_moy = last.month / 12.0
        assert abs(feats["day_of_week"] - expected_dow) < 1e-9
        assert abs(feats["month_of_year"] - expected_moy) < 1e-9

    def test_sector_relative_strength_no_data(self):
        df = _make_ohlcv(25)
        feats = compute_tier2_features(
            ohlcv_df=df,
            sector_index_df=None,
            earnings_dates=None,
        )
        assert feats["sector_relative_strength"] == 0.0

    def test_sector_relative_strength_with_data(self):
        df = _make_ohlcv(25)
        # Sector flat while ticker rose → positive relative strength
        base = date(2025, 1, 2)
        sdates = [base + timedelta(days=i) for i in range(25)]
        sdf = pd.DataFrame(
            {"date": sdates, "close": [100.0] * 25}
        )
        feats = compute_tier2_features(
            ohlcv_df=df,
            sector_index_df=sdf,
            earnings_dates=None,
        )
        assert feats["sector_relative_strength"] > 0.0

    def test_earnings_proximity_default(self):
        df = _make_ohlcv(25)
        feats = compute_tier2_features(
            ohlcv_df=df,
            sector_index_df=None,
            earnings_dates=None,
        )
        assert feats["earnings_proximity"] == 0.5

    def test_earnings_proximity_near(self):
        df = _make_ohlcv(25)
        last_date = df["date"].iloc[-1]
        near = last_date + timedelta(days=5)
        feats = compute_tier2_features(
            ohlcv_df=df,
            sector_index_df=None,
            earnings_dates=[near],
        )
        expected = min(5 / 90.0, 1.0)
        assert abs(feats["earnings_proximity"] - expected) < 1e-6

    def test_expiry_proximity_finite(self):
        df = _make_ohlcv(25)
        feats = compute_tier2_features(
            ohlcv_df=df,
            sector_index_df=None,
            earnings_dates=None,
        )
        assert math.isfinite(feats["expiry_proximity"])
        assert 0.0 <= feats["expiry_proximity"] <= 1.0


# ---------------------------------------------------------------------------
# TestBuildFutureFeatures
# ---------------------------------------------------------------------------

class TestBuildFutureFeatures:
    """build_future_features propagates features correctly."""

    def _last_known(self) -> dict:
        return {
            "volatility_regime": 0.2,
            "trend_strength": 0.3,
            "sr_position": 0.5,
            "piotroski": 0.7,
            "revenue_growth": 0.1,
            "eps_growth": 0.05,
            "sector_relative_strength": 0.15,
            "volume_anomaly": 0.8,
            "obv_trend": 0.4,
            "day_of_week": 0.25,
            "month_of_year": 0.5,
            "expiry_proximity": 0.3,
            "earnings_proximity": 0.2,
        }

    def test_output_shape(self):
        lk = self._last_known()
        future = [date(2025, 2, 3) + timedelta(days=i) for i in range(10)]
        df = build_future_features(lk, future)
        assert len(df) == 10
        assert set(lk.keys()).issubset(set(df.columns))

    def test_holds_constant_slow_features(self):
        lk = self._last_known()
        future = [date(2025, 2, 3) + timedelta(days=i) for i in range(5)]
        df = build_future_features(lk, future)
        slow = [
            "volatility_regime",
            "trend_strength",
            "sr_position",
            "piotroski",
            "revenue_growth",
            "eps_growth",
            "sector_relative_strength",
            "earnings_proximity",
        ]
        for col in slow:
            assert (
                df[col] == lk[col]
            ).all(), f"{col} should be constant"

    def test_transient_features_zeroed(self):
        lk = self._last_known()
        future = [date(2025, 2, 3) + timedelta(days=i) for i in range(5)]
        df = build_future_features(lk, future)
        assert (df["volume_anomaly"] == 0.0).all()
        assert (df["obv_trend"] == 0.0).all()

    def test_calendar_features_vary(self):
        lk = self._last_known()
        # Pick dates spanning different days/months
        future = [
            date(2025, 1, 31),
            date(2025, 2, 1),
            date(2025, 3, 15),
        ]
        df = build_future_features(lk, future)
        # day_of_week and month_of_year should vary
        assert df["day_of_week"].nunique() > 1
        assert df["month_of_year"].nunique() > 1


# ---------------------------------------------------------------------------
# TestDaysToExpiry
# ---------------------------------------------------------------------------

class TestDaysToExpiry:
    """_days_to_expiry returns correct values."""

    def test_returns_non_negative(self):
        d = date(2025, 4, 15)
        result = _days_to_expiry(d)
        assert result >= 0.0

    def test_last_thursday_itself_is_zero(self):
        # April 2025: last Thursday = April 24
        d = date(2025, 4, 24)
        assert _days_to_expiry(d) == 0.0

    def test_day_before_last_thursday(self):
        # 1 day before expiry → normalised 1/30
        d = date(2025, 4, 23)
        assert abs(_days_to_expiry(d) - (1 / 30.0)) < 1e-9

    def test_normalized_max_one(self):
        # First of any month: expiry proximity ≤ 1
        d = date(2025, 1, 1)
        result = _days_to_expiry(d)
        assert result <= 1.0


# ---------------------------------------------------------------------------
# TestDaysToNearestEarnings
# ---------------------------------------------------------------------------

class TestDaysToNearestEarnings:
    """_days_to_nearest_earnings finds closest date."""

    def test_exact_match(self):
        d = date(2025, 4, 15)
        result = _days_to_nearest_earnings(d, [d])
        assert result == 0.0

    def test_five_days_away(self):
        d = date(2025, 4, 15)
        target = date(2025, 4, 20)
        result = _days_to_nearest_earnings(d, [target])
        assert result == 5.0

    def test_picks_nearest_of_multiple(self):
        d = date(2025, 4, 15)
        dates = [date(2025, 4, 30), date(2025, 4, 18)]
        result = _days_to_nearest_earnings(d, dates)
        assert result == 3.0

    def test_empty_list_returns_none(self):
        d = date(2025, 4, 15)
        assert _days_to_nearest_earnings(d, []) is None


# ---------------------------------------------------------------------------
# TestSectorMapping
# ---------------------------------------------------------------------------

class TestSectorMapping:
    """get_sector_index_mapping returns correct tickers."""

    def test_india_mapping(self):
        m = get_sector_index_mapping("india")
        assert "Financial Services" in m
        assert m["Financial Services"] == "^NSEBANK"
        assert "Information Technology" in m
        assert m["Information Technology"] == "^CNXIT"
        assert len(m) == 5

    def test_us_mapping(self):
        m = get_sector_index_mapping("us")
        assert "Technology" in m
        assert m["Technology"] == "XLK"
        assert "Financial Services" in m
        assert len(m) == 5

    def test_unknown_market_returns_empty(self):
        m = get_sector_index_mapping("unknown")
        assert m == {}
