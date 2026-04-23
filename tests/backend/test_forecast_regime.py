"""Tests for volatility regime classification module.

Covers:
- Stable / Moderate / Volatile regime detection
- Missing volatility defaults to Moderate
- Boundary conditions (30.0, 60.0)
- Logistic cap/floor bounds from OHLCV
- Prophet config dict structure
"""

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(
    close_values: list[float],
    start: str = "2023-01-01",
) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame for testing."""
    idx = pd.date_range(start, periods=len(close_values), freq="B")
    arr = np.array(close_values, dtype=float)
    return pd.DataFrame(
        {
            "ds": idx,
            "open": arr * 0.99,
            "high": arr * 1.02,
            "low": arr * 0.97,
            "close": arr,
            "volume": 1_000_000,
        }
    )


# ---------------------------------------------------------------------------
# TestClassifyRegime
# ---------------------------------------------------------------------------

class TestClassifyRegime:
    """classify_regime(annualized_vol) → regime string."""

    def test_stable_regime(self):
        from tools._forecast_regime import classify_regime
        assert classify_regime(0.0) == "stable"
        assert classify_regime(10.0) == "stable"
        assert classify_regime(29.99) == "stable"

    def test_moderate_regime(self):
        from tools._forecast_regime import classify_regime
        assert classify_regime(30.0) == "moderate"
        assert classify_regime(45.0) == "moderate"
        assert classify_regime(59.99) == "moderate"

    def test_volatile_regime(self):
        from tools._forecast_regime import classify_regime
        assert classify_regime(60.0) == "volatile"
        assert classify_regime(80.0) == "volatile"
        assert classify_regime(150.0) == "volatile"

    def test_missing_volatility_defaults_moderate(self):
        from tools._forecast_regime import classify_regime
        assert classify_regime(None) == "moderate"

    def test_boundary_30_is_moderate(self):
        from tools._forecast_regime import classify_regime
        assert classify_regime(30.0) == "moderate"

    def test_boundary_60_is_volatile(self):
        from tools._forecast_regime import classify_regime
        assert classify_regime(60.0) == "volatile"


# ---------------------------------------------------------------------------
# TestComputeLogisticBounds
# ---------------------------------------------------------------------------

class TestComputeLogisticBounds:
    """compute_logistic_bounds(ohlcv_df) → (cap, floor)."""

    def _two_year_ohlcv(self) -> pd.DataFrame:
        """~504 trading days of prices from 100 to 200."""
        rng = np.random.default_rng(7)
        n = 504
        prices = np.linspace(100, 200, n) + rng.standard_normal(n) * 2
        prices = np.clip(prices, 80, 220).tolist()
        return _make_ohlcv(prices, start="2022-01-01")

    def test_bounds_from_ohlcv(self):
        from tools._forecast_regime import compute_logistic_bounds
        df = self._two_year_ohlcv()
        cap, floor = compute_logistic_bounds(df)
        assert cap > 0
        assert floor > 0
        assert cap > floor

    def test_cap_is_ath_times_1_5(self):
        from tools._forecast_regime import compute_logistic_bounds
        df = self._two_year_ohlcv()
        cap, _ = compute_logistic_bounds(df)
        # ATH over last 2 years; cap = ATH * 1.5
        two_yr_high = df["high"].iloc[-504:].max()
        expected_cap = two_yr_high * 1.5
        assert abs(cap - expected_cap) < 0.01

    def test_floor_is_low_times_0_3(self):
        from tools._forecast_regime import compute_logistic_bounds
        df = self._two_year_ohlcv()
        _, floor = compute_logistic_bounds(df)
        # floor = 1yr_low * 0.3
        one_yr_low = df["low"].iloc[-252:].min()
        expected_floor = one_yr_low * 0.3
        assert abs(floor - expected_floor) < 0.01


# ---------------------------------------------------------------------------
# TestBuildProphetConfig
# ---------------------------------------------------------------------------

class TestBuildProphetConfig:
    """build_prophet_config(regime) → dict for Prophet(**config)."""

    def test_stable_config(self):
        from tools._forecast_regime import build_prophet_config
        cfg = build_prophet_config("stable")
        assert cfg["growth"] == "linear"
        assert cfg["changepoint_prior_scale"] == pytest.approx(0.01)
        assert cfg["changepoint_range"] == pytest.approx(0.80)

    def test_moderate_config(self):
        from tools._forecast_regime import build_prophet_config
        cfg = build_prophet_config("moderate")
        assert cfg["growth"] == "linear"
        assert cfg["changepoint_prior_scale"] == pytest.approx(0.10)
        assert cfg["changepoint_range"] == pytest.approx(0.85)

    def test_volatile_config(self):
        from tools._forecast_regime import build_prophet_config
        cfg = build_prophet_config("volatile")
        assert cfg["growth"] == "logistic"
        assert cfg["changepoint_prior_scale"] == pytest.approx(0.25)
        assert cfg["changepoint_range"] == pytest.approx(0.90)

    def test_volatile_config_has_no_cap(self):
        """cap/floor belong on the DataFrame, not the Prophet config."""
        from tools._forecast_regime import build_prophet_config
        cfg = build_prophet_config("volatile")
        assert "cap" not in cfg
        assert "floor" not in cfg

    def test_unknown_regime_raises(self):
        from tools._forecast_regime import build_prophet_config
        with pytest.raises(ValueError, match="Unknown regime"):
            build_prophet_config("unknown_regime")


# ---------------------------------------------------------------------------
# Helpers for TestApplyTechnicalBias
# ---------------------------------------------------------------------------

def _make_forecast_df(
    n: int = 40,
    base_price: float = 100.0,
    start: str = "2025-01-01",
) -> pd.DataFrame:
    """Build a minimal Prophet forecast DataFrame."""
    dates = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame(
        {
            "ds": dates,
            "yhat": [base_price] * n,
            "yhat_lower": [base_price * 0.95] * n,
            "yhat_upper": [base_price * 1.05] * n,
        }
    )


# ---------------------------------------------------------------------------
# TestApplyTechnicalBias
# ---------------------------------------------------------------------------

class TestApplyTechnicalBias:
    """apply_technical_bias(forecast_df, analysis_row) → (df, metadata)."""

    def test_overbought_dampens_bullish(self):
        """RSI=80 → overbought bias of -15%, day 1 yhat should be reduced."""
        from tools._forecast_regime import apply_technical_bias

        df = _make_forecast_df()
        analysis = {
            "rsi_14": 80.0,
            "macd": 0.0,
            "macd_signal_line": 0.0,
            "volume_spike": False,
            "price_direction": "flat",
        }
        adj_df, meta = apply_technical_bias(df, analysis)

        # Day 1 (index 1) should have taper > 0 and bias < 0
        # so yhat should be less than original 100.0
        assert adj_df["yhat"].iloc[1] < df["yhat"].iloc[1]
        assert meta["total_bias"] < 0.0
        assert "rsi_overbought" in meta["signals"]

    def test_oversold_dampens_bearish(self):
        """RSI=20 → oversold bias of +15%, day 1 yhat should be increased."""
        from tools._forecast_regime import apply_technical_bias

        df = _make_forecast_df()
        analysis = {
            "rsi_14": 20.0,
            "macd": 0.0,
            "macd_signal_line": 0.0,
            "volume_spike": False,
            "price_direction": "flat",
        }
        adj_df, meta = apply_technical_bias(df, analysis)

        assert adj_df["yhat"].iloc[1] > df["yhat"].iloc[1]
        assert meta["total_bias"] > 0.0
        assert "rsi_oversold" in meta["signals"]

    def test_taper_at_day_30(self):
        """Day 35 should be unadjusted (taper = 0 at day >= 30)."""
        from tools._forecast_regime import apply_technical_bias

        df = _make_forecast_df(n=40)
        analysis = {
            "rsi_14": 80.0,
            "macd": 0.0,
            "macd_signal_line": 0.0,
            "volume_spike": False,
            "price_direction": "flat",
        }
        adj_df, _ = apply_technical_bias(df, analysis)

        # Day 35 (index 35): taper should be 0, so yhat unchanged
        original = df["yhat"].iloc[35]
        adjusted = adj_df["yhat"].iloc[35]
        assert abs(adjusted - original) < 1e-9

    def test_cap_at_15_pct(self):
        """Stack all bearish signals — total bias must be capped at -15%."""
        from tools._forecast_regime import apply_technical_bias

        df = _make_forecast_df()
        # RSI overbought (-15%) + MACD bearish (-8%) + vol spike down (-5%)
        # uncapped sum = -28%, capped = -15%
        analysis = {
            "rsi_14": 80.0,
            "macd": -1.0,
            "macd_signal_line": 0.0,
            "volume_spike": True,
            "price_direction": "down",
        }
        adj_df, meta = apply_technical_bias(df, analysis)

        # At day 0, taper=1.0, so multiplier = 1 + (-0.15) = 0.85
        ratio = adj_df["yhat"].iloc[0] / df["yhat"].iloc[0]
        assert ratio >= 0.85 - 1e-9
        assert abs(meta["total_bias"]) <= 0.15 + 1e-9

    def test_no_signal_no_change(self):
        """RSI=50, neutral MACD, no volume spike → DataFrame unchanged."""
        from tools._forecast_regime import apply_technical_bias

        df = _make_forecast_df()
        analysis = {
            "rsi_14": 50.0,
            "macd": 1.0,
            "macd_signal_line": 1.0,
            "volume_spike": False,
            "price_direction": "flat",
        }
        adj_df, meta = apply_technical_bias(df, analysis)

        pd.testing.assert_frame_equal(adj_df, df)
        assert meta["total_bias"] == 0.0
        assert meta["signals"] == []

    def test_none_analysis_no_change(self):
        """analysis_row=None → DataFrame unchanged, total_bias=0."""
        from tools._forecast_regime import apply_technical_bias

        df = _make_forecast_df()
        adj_df, meta = apply_technical_bias(df, None)

        pd.testing.assert_frame_equal(adj_df, df)
        assert meta["total_bias"] == 0.0
        assert meta["signals"] == []

    def test_returns_metadata(self):
        """Metadata must contain 'total_bias' (float) and 'signals' (list)."""
        from tools._forecast_regime import apply_technical_bias

        df = _make_forecast_df()
        analysis = {
            "rsi_14": 80.0,
            "macd": -1.0,
            "macd_signal_line": 0.0,
            "volume_spike": False,
            "price_direction": "flat",
        }
        _, meta = apply_technical_bias(df, analysis)

        assert isinstance(meta, dict)
        assert "total_bias" in meta
        assert "signals" in meta
        assert isinstance(meta["total_bias"], float)
        assert isinstance(meta["signals"], list)
