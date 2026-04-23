"""End-to-end test for enriched forecast pipeline.

Validates the full enriched forecast flow WITHOUT requiring
Prophet or live data.  Simulates the data flow that happens
in the batch executor and chat forecast path.

Covers:
- Stable ticker: classify → tier1/tier2 features → no bias
- Volatile ticker: classify → logistic growth → oversold bias
- Future feature calendar variation across 180 business days
- Logistic cap/floor positivity
- JSON serialisation round-trip of confidence components
"""

import json

import numpy as np
import pandas as pd
import pytest

from tools._forecast_regime import (
    apply_technical_bias,
    classify_regime,
    compute_logistic_bounds,
    get_regime_config,
)
from tools._forecast_features import (
    build_future_features,
    compute_tier1_features,
    compute_tier2_features,
)
from tools._forecast_accuracy import (
    compute_confidence_score,
    confidence_badge,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 60) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame for testing."""
    rng = np.random.default_rng(seed=42)
    dates = pd.date_range("2025-06-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "close": rng.uniform(95, 105, n),
            "volume": rng.integers(100_000, 500_000, n),
            "high": rng.uniform(100, 110, n),
            "low": rng.uniform(85, 95, n),
        }
    )


def _make_forecast(n: int = 90, base: float = 100.0) -> pd.DataFrame:
    """Build a minimal Prophet-style forecast DataFrame."""
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "ds": dates,
            "yhat": [base * 1.1] * n,
            "yhat_lower": [base * 0.95] * n,
            "yhat_upper": [base * 1.25] * n,
        }
    )


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestEnrichedForecastE2E:
    """Integration tests for the enriched forecast pipeline."""

    def test_stable_ticker_full_flow(self):
        """Stable ticker: neutral RSI → no technical bias; good
        metrics → High or Medium confidence badge."""
        analysis_row = {
            "annualized_volatility": 22.0,
            "rsi_14": 55.0,
            "macd": 0.0,
            "macd_signal_line": 0.0,
            "volume_spike": False,
            "price_direction": "flat",
            "bull_phase_pct": 55.0,
            "bear_phase_pct": 30.0,
            "support_level": 90.0,
            "resistance_level": 110.0,
        }

        # 1. Regime classification
        regime = classify_regime(22.0)
        assert regime == "stable"

        cfg = get_regime_config(22.0)
        assert cfg.transform == "none"
        assert cfg.growth == "linear"

        # 2. Tier 1 features with high Piotroski score
        piotroski_row = {"f_score": 8}
        quarterly_rows = [
            {"total_revenue": 1_200_000, "diluted_eps": 5.0},
            {"total_revenue": 1_000_000, "diluted_eps": 4.0},
        ]
        t1 = compute_tier1_features(
            analysis_row=analysis_row,
            piotroski_row=piotroski_row,
            quarterly_rows=quarterly_rows,
            current_price=100.0,
        )
        # f_score 8 / 9 ≈ 0.889 > 0.8
        assert t1["piotroski"] > 0.8

        # 3. Tier 2 features — volume_anomaly key present
        ohlcv = _make_ohlcv(n=60)
        t2 = compute_tier2_features(
            ohlcv_df=ohlcv,
            sector_index_df=None,
            earnings_dates=None,
        )
        assert "volume_anomaly" in t2

        # 4. Technical bias — neutral RSI ⇒ total_bias == 0.0
        forecast_df = _make_forecast(n=90)
        _, meta = apply_technical_bias(
            forecast_df=forecast_df,
            analysis_row=analysis_row,
        )
        assert meta["total_bias"] == 0.0

        # 5. Confidence score with good metrics
        metrics = {
            "directional_accuracy_pct": 65.0,
            "MAPE_pct": 12.0,
            "coverage": 0.78,
            "interval_width_ratio": 0.30,
        }
        score, components = compute_confidence_score(
            metrics=metrics,
            data_completeness=0.90,
        )
        badge, _ = confidence_badge(score, components)
        assert badge in ("High", "Medium")

    def test_volatile_ticker_full_flow(self):
        """Volatile ticker: oversold RSI on bearish forecast
        ⇒ positive bias, adjusted yhat > original at day 0."""
        analysis_row = {
            "annualized_volatility": 85.0,
            "rsi_14": 22.0,          # oversold → +0.15 bias
            "macd": -1.0,
            "macd_signal_line": 0.5,  # macd bearish → -0.08 bias
            "volume_spike": False,
            "price_direction": "flat",
        }

        # 1. Regime classification
        regime = classify_regime(85.0)
        assert regime == "volatile"

        cfg = get_regime_config(85.0)
        assert cfg.growth == "logistic"
        assert cfg.transform == "log"

        # 2. Technical bias on bearish-looking forecast
        # Net bias = +0.15 (RSI oversold) - 0.08 (MACD bearish)
        # = +0.07 (positive → dampens bearish / lifts yhat)
        base_price = 100.0
        forecast_df = _make_forecast(n=90, base=base_price)
        original_yhat_day0 = float(forecast_df["yhat"].iloc[0])

        adj_df, meta = apply_technical_bias(
            forecast_df=forecast_df,
            analysis_row=analysis_row,
        )

        assert meta["total_bias"] > 0.0, (
            f"Expected positive bias but got {meta['total_bias']}"
        )
        adjusted_yhat_day0 = float(adj_df["yhat"].iloc[0])
        assert adjusted_yhat_day0 > original_yhat_day0, (
            "Oversold RSI should lift yhat above original at day 0"
        )

    def test_future_features_have_calendar_variation(self):
        """180 business-day future frame has ≥5 weekdays,
        ≥6 months, and constant piotroski across all rows."""
        last_known = {
            "volatility_regime": 0.22,
            "trend_strength": 0.25,
            "sr_position": 0.50,
            "piotroski": 0.889,
            "revenue_growth": 0.20,
            "eps_growth": 0.25,
            "sector_relative_strength": 0.05,
            "earnings_proximity": 0.50,
            "volume_anomaly": 0.10,
            "obv_trend": 0.001,
            "day_of_week": 0.25,
            "month_of_year": 0.50,
            "expiry_proximity": 0.60,
        }
        future_dates = list(
            pd.date_range("2026-07-01", periods=180, freq="B")
        )

        df = build_future_features(
            last_known=last_known,
            future_dates=future_dates,
        )

        assert len(df) == 180

        # day_of_week should cycle across Mon-Fri → 5 unique values
        dow_values = df["day_of_week"].unique()
        assert len(dow_values) == 5, (
            f"Expected 5 unique day_of_week values, got {dow_values}"
        )

        # month_of_year across 180 business days spans ≥6 months
        moy_values = df["month_of_year"].unique()
        assert len(moy_values) >= 6, (
            f"Expected >=6 unique month_of_year values, "
            f"got {moy_values}"
        )

        # Piotroski (slow feature) should be constant
        assert df["piotroski"].nunique() == 1, (
            "piotroski should be constant across all future rows"
        )

    def test_logistic_bounds_positive(self):
        """compute_logistic_bounds returns cap > floor > 0."""
        ohlcv = _make_ohlcv(n=60)
        cap, floor = compute_logistic_bounds(ohlcv)

        assert floor > 0.0, f"Expected floor > 0, got {floor}"
        assert cap > 0.0, f"Expected cap > 0, got {cap}"
        assert cap > floor, (
            f"Expected cap ({cap:.2f}) > floor ({floor:.2f})"
        )

    def test_confidence_json_roundtrip(self):
        """Confidence components survive JSON serialisation."""
        metrics = {
            "directional_accuracy_pct": 60.0,
            "MAPE_pct": 18.0,
            "coverage": 0.82,
            "interval_width_ratio": 0.45,
        }
        score, components = compute_confidence_score(
            metrics=metrics,
            data_completeness=0.85,
        )

        # Serialise → deserialise
        serialised = json.dumps(components)
        recovered = json.loads(serialised)

        # Values must match after round-trip
        for key, original_val in components.items():
            assert key in recovered, (
                f"Key {key!r} missing after JSON round-trip"
            )
            assert recovered[key] == pytest.approx(
                original_val, abs=1e-6
            ), (
                f"Value mismatch for {key!r}: "
                f"{original_val} → {recovered[key]}"
            )

        # Score itself should be a plain float (JSON-safe)
        json.dumps({"score": score})
