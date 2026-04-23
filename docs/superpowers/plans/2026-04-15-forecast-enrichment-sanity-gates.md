# Forecast Data Enrichment & Sanity Gates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 19% of broken forecasts via volatility-regime adaptive Prophet, enrich forecasts with 12 new data signals from existing Iceberg tables and computed features, and add composite confidence scoring with UI badges.

**Architecture:** Three new modules (`_forecast_regime.py`, `_forecast_features.py`, plus tests) plug into the existing forecast pipeline. Regime classification drives per-ticker Prophet config. Tier 1/2 features are bulk-loaded and merged as regressors. Post-Prophet technical bias adjustment and confidence scoring wrap the output. Frontend gets a confidence badge with expandable explanation card.

**Tech Stack:** Python 3.12, Prophet, pandas, DuckDB, NumPy, React 19, Next.js 16, Tailwind CSS, lightweight-charts

**Spec:** `docs/superpowers/specs/2026-04-15-forecast-enrichment-sanity-gates-design.md`

---

## Task 1: Volatility Regime Classification Module

**Files:**
- Create: `backend/tools/_forecast_regime.py`
- Create: `tests/backend/test_forecast_regime.py`

- [ ] **Step 1: Write failing tests for regime classification**

```python
# tests/backend/test_forecast_regime.py
"""Tests for volatility regime classification and Prophet config."""
import numpy as np
import pandas as pd
import pytest

from backend.tools._forecast_regime import (
    RegimeConfig,
    classify_regime,
    compute_logistic_bounds,
    build_prophet_config,
)


class TestClassifyRegime:
    def test_stable_regime(self):
        row = {"annualized_volatility": 20.0}
        cfg = classify_regime("TCS.NS", row)
        assert cfg.regime == "stable"
        assert cfg.growth == "linear"
        assert cfg.log_transform is False
        assert cfg.changepoint_prior_scale == 0.01
        assert cfg.changepoint_range == 0.80

    def test_moderate_regime(self):
        row = {"annualized_volatility": 45.0}
        cfg = classify_regime("INFY.NS", row)
        assert cfg.regime == "moderate"
        assert cfg.growth == "linear"
        assert cfg.log_transform is True
        assert cfg.changepoint_prior_scale == 0.10
        assert cfg.changepoint_range == 0.85

    def test_volatile_regime(self):
        row = {"annualized_volatility": 75.0}
        cfg = classify_regime("TANLA.NS", row)
        assert cfg.regime == "volatile"
        assert cfg.growth == "logistic"
        assert cfg.log_transform is True
        assert cfg.changepoint_prior_scale == 0.25
        assert cfg.changepoint_range == 0.90

    def test_missing_volatility_defaults_moderate(self):
        cfg = classify_regime("UNKNOWN.NS", None)
        assert cfg.regime == "moderate"

    def test_boundary_30_is_moderate(self):
        row = {"annualized_volatility": 30.0}
        cfg = classify_regime("X.NS", row)
        assert cfg.regime == "moderate"

    def test_boundary_60_is_volatile(self):
        row = {"annualized_volatility": 60.0}
        cfg = classify_regime("X.NS", row)
        assert cfg.regime == "volatile"


class TestComputeLogisticBounds:
    def test_bounds_from_ohlcv(self):
        dates = pd.date_range("2024-01-01", periods=500, freq="B")
        df = pd.DataFrame({
            "date": dates,
            "high": np.random.uniform(90, 110, len(dates)),
            "low": np.random.uniform(70, 90, len(dates)),
            "close": np.random.uniform(80, 100, len(dates)),
        })
        cap, floor = compute_logistic_bounds(df)
        assert cap > 0
        assert floor > 0
        assert cap > floor

    def test_cap_is_ath_times_1_5(self):
        dates = pd.date_range("2024-01-01", periods=500, freq="B")
        highs = [100.0] * 500
        highs[250] = 200.0  # ATH
        df = pd.DataFrame({
            "date": dates,
            "high": highs,
            "low": [50.0] * 500,
            "close": [90.0] * 500,
        })
        cap, floor = compute_logistic_bounds(df)
        assert cap == pytest.approx(300.0, rel=0.01)  # 200 * 1.5

    def test_floor_is_low_times_0_3(self):
        dates = pd.date_range("2024-01-01", periods=500, freq="B")
        df = pd.DataFrame({
            "date": dates,
            "high": [100.0] * 500,
            "low": [40.0] * 500,
            "close": [80.0] * 500,
        })
        cap, floor = compute_logistic_bounds(df)
        assert floor == pytest.approx(12.0, rel=0.01)  # 40 * 0.3


class TestBuildProphetConfig:
    def test_stable_config(self):
        cfg = RegimeConfig(
            regime="stable",
            growth="linear",
            log_transform=False,
            changepoint_prior_scale=0.01,
            changepoint_range=0.80,
        )
        prophet_kwargs = build_prophet_config(cfg)
        assert prophet_kwargs["growth"] == "linear"
        assert prophet_kwargs["changepoint_prior_scale"] == 0.01
        assert "cap" not in prophet_kwargs

    def test_volatile_config_has_no_cap(self):
        """Cap/floor set on DataFrame, not Prophet kwargs."""
        cfg = RegimeConfig(
            regime="volatile",
            growth="logistic",
            log_transform=True,
            changepoint_prior_scale=0.25,
            changepoint_range=0.90,
        )
        prophet_kwargs = build_prophet_config(cfg)
        assert prophet_kwargs["growth"] == "logistic"
        assert prophet_kwargs["changepoint_prior_scale"] == 0.25
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/test_forecast_regime.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.tools._forecast_regime'`

- [ ] **Step 3: Implement the regime module**

```python
# backend/tools/_forecast_regime.py
"""Volatility-regime adaptive Prophet configuration.

Classifies tickers into stable/moderate/volatile regimes based on
annualized volatility, returning per-regime Prophet parameters.
"""
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)

# ── Regime thresholds ────────────────────────────────────
_VOL_MODERATE = 30.0   # annualized vol % boundary
_VOL_VOLATILE = 60.0

# ── Logistic bounds multipliers ──────────────────────────
_CAP_MULT = 1.5        # ATH * 1.5
_FLOOR_MULT = 0.3      # 1yr-low * 0.3
_LOOKBACK_ATH = 504    # ~2 years trading days
_LOOKBACK_LOW = 252    # ~1 year trading days


@dataclass(frozen=True)
class RegimeConfig:
    """Prophet configuration for a volatility regime."""

    regime: str                     # stable | moderate | volatile
    growth: str                     # linear | logistic
    log_transform: bool
    changepoint_prior_scale: float
    changepoint_range: float


# ── Classification ───────────────────────────────────────

def classify_regime(
    ticker: str,
    analysis_row: dict | None,
) -> RegimeConfig:
    """Classify ticker into a volatility regime.

    Args:
        ticker: Stock ticker symbol.
        analysis_row: Dict with at least 'annualized_volatility'.
            If None, defaults to moderate regime.
    """
    if analysis_row is None:
        _log.debug("%s: no analysis data, defaulting to moderate", ticker)
        return _MODERATE

    vol = analysis_row.get("annualized_volatility")
    if vol is None or np.isnan(vol):
        return _MODERATE

    if vol < _VOL_MODERATE:
        return _STABLE
    if vol < _VOL_VOLATILE:
        return _MODERATE
    return _VOLATILE


# ── Logistic bounds ──────────────────────────────────────

def compute_logistic_bounds(ohlcv_df: pd.DataFrame) -> tuple[float, float]:
    """Compute cap and floor for logistic growth.

    Args:
        ohlcv_df: DataFrame with 'high', 'low' columns, sorted by date.

    Returns:
        (cap, floor) — both positive floats.
    """
    recent = ohlcv_df.tail(_LOOKBACK_ATH)
    ath = float(recent["high"].max())
    low_1yr = float(ohlcv_df.tail(_LOOKBACK_LOW)["low"].min())

    cap = max(ath * _CAP_MULT, 1.0)
    floor = max(low_1yr * _FLOOR_MULT, 0.01)
    return cap, floor


# ── Prophet config builder ───────────────────────────────

def build_prophet_config(cfg: RegimeConfig) -> dict:
    """Build Prophet constructor kwargs from regime config.

    Note: cap/floor are set on the DataFrame, not here.
    """
    return {
        "growth": cfg.growth,
        "changepoint_prior_scale": cfg.changepoint_prior_scale,
        "changepoint_range": cfg.changepoint_range,
        "yearly_seasonality": True,
        "weekly_seasonality": True,
        "daily_seasonality": False,
        "interval_width": 0.80,
    }


# ── Regime singletons ───────────────────────────────────

_STABLE = RegimeConfig(
    regime="stable",
    growth="linear",
    log_transform=False,
    changepoint_prior_scale=0.01,
    changepoint_range=0.80,
)

_MODERATE = RegimeConfig(
    regime="moderate",
    growth="linear",
    log_transform=True,
    changepoint_prior_scale=0.10,
    changepoint_range=0.85,
)

_VOLATILE = RegimeConfig(
    regime="volatile",
    growth="logistic",
    log_transform=True,
    changepoint_prior_scale=0.25,
    changepoint_range=0.90,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/test_forecast_regime.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tools/_forecast_regime.py tests/backend/test_forecast_regime.py
git commit -m "feat(forecast): add volatility regime classification module

Classifies tickers into stable/moderate/volatile regimes based on
annualized volatility. Returns per-regime Prophet config (growth mode,
changepoint_prior_scale, log-transform, logistic bounds).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 2: Technical Bias Adjustment

**Files:**
- Modify: `backend/tools/_forecast_regime.py`
- Modify: `tests/backend/test_forecast_regime.py`

- [ ] **Step 1: Write failing tests for technical bias adjustment**

Add to `tests/backend/test_forecast_regime.py`:

```python
from backend.tools._forecast_regime import apply_technical_bias


class TestApplyTechnicalBias:
    def _make_forecast(self, n_days=90, base_price=100.0):
        dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
        return pd.DataFrame({
            "ds": dates,
            "yhat": [base_price * 1.10] * n_days,  # +10% bullish
            "yhat_lower": [base_price * 0.95] * n_days,
            "yhat_upper": [base_price * 1.25] * n_days,
        })

    def test_overbought_dampens_bullish(self):
        fc = self._make_forecast()
        analysis = {"rsi_14": 80.0, "macd": 0.5, "macd_signal_line": 0.5}
        result, meta = apply_technical_bias(fc.copy(), analysis)
        # Day 1 should be dampened (bullish * 0.85)
        assert result["yhat"].iloc[0] < fc["yhat"].iloc[0]

    def test_oversold_dampens_bearish(self):
        fc = self._make_forecast(base_price=100.0)
        fc["yhat"] = 85.0  # bearish forecast
        analysis = {"rsi_14": 20.0, "macd": 0.5, "macd_signal_line": 0.5}
        result, meta = apply_technical_bias(fc.copy(), analysis)
        # Day 1: oversold dampens bearish → price should be higher
        assert result["yhat"].iloc[0] > fc["yhat"].iloc[0]

    def test_taper_at_day_30(self):
        fc = self._make_forecast(n_days=60)
        analysis = {"rsi_14": 80.0, "macd": 0.5, "macd_signal_line": 0.5}
        result, _ = apply_technical_bias(fc.copy(), analysis)
        # Day 30+ should be unadjusted
        assert result["yhat"].iloc[35] == pytest.approx(
            fc["yhat"].iloc[35], rel=1e-6
        )

    def test_cap_at_15_pct(self):
        fc = self._make_forecast()
        # Stack all bearish signals to exceed 15%
        analysis = {
            "rsi_14": 80.0,
            "macd": -0.5, "macd_signal_line": 0.3,
            "volume_spike": True,
            "price_direction": "down",
        }
        result, meta = apply_technical_bias(fc.copy(), analysis)
        ratio = result["yhat"].iloc[0] / fc["yhat"].iloc[0]
        assert ratio >= 0.85  # capped at -15%

    def test_no_signal_no_change(self):
        fc = self._make_forecast()
        analysis = {"rsi_14": 50.0, "macd": 0.5, "macd_signal_line": 0.5}
        result, meta = apply_technical_bias(fc.copy(), analysis)
        pd.testing.assert_frame_equal(result, fc)
        assert meta["total_bias"] == 0.0

    def test_returns_metadata(self):
        fc = self._make_forecast()
        analysis = {"rsi_14": 80.0, "macd": 0.5, "macd_signal_line": 0.5}
        _, meta = apply_technical_bias(fc.copy(), analysis)
        assert "total_bias" in meta
        assert "signals" in meta
        assert isinstance(meta["signals"], list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/test_forecast_regime.py::TestApplyTechnicalBias -v`
Expected: FAIL — `ImportError: cannot import name 'apply_technical_bias'`

- [ ] **Step 3: Implement apply_technical_bias**

Add to `backend/tools/_forecast_regime.py`:

```python
# ── Technical bias adjustment ────────────────────────────

_TAPER_DAYS = 30
_MAX_BIAS = 0.15


def apply_technical_bias(
    forecast_df: pd.DataFrame,
    analysis_row: dict | None,
) -> tuple[pd.DataFrame, dict]:
    """Apply post-Prophet technical bias adjustment.

    Dampens or amplifies forecast based on current RSI, MACD,
    and volume signals. Tapers linearly over 30 days.

    Args:
        forecast_df: Prophet output with yhat, yhat_lower, yhat_upper.
        analysis_row: Dict from analysis_summary with rsi_14, etc.

    Returns:
        (adjusted_df, metadata) — metadata has total_bias and signals.
    """
    if analysis_row is None:
        return forecast_df, {"total_bias": 0.0, "signals": []}

    bias = 0.0
    signals = []

    rsi = analysis_row.get("rsi_14")
    if rsi is not None and not np.isnan(rsi):
        if rsi > 75:
            bias -= 0.15
            signals.append(f"RSI overbought ({rsi:.0f}): -15%")
        elif rsi < 25:
            bias += 0.15
            signals.append(f"RSI oversold ({rsi:.0f}): +15%")

    macd = analysis_row.get("macd")
    macd_signal = analysis_row.get("macd_signal_line")
    if macd is not None and macd_signal is not None:
        if not (np.isnan(macd) or np.isnan(macd_signal)):
            if macd < macd_signal:
                bias -= 0.08
                signals.append("MACD bearish crossover: -8%")
            elif macd > macd_signal:
                bias += 0.08
                signals.append("MACD bullish crossover: +8%")

    vol_spike = analysis_row.get("volume_spike", False)
    if vol_spike:
        direction = analysis_row.get("price_direction", "flat")
        if direction == "down":
            bias -= 0.05
            signals.append("Volume spike + decline: -5%")
        elif direction == "up":
            bias += 0.05
            signals.append("Volume spike + rise: +5%")

    # Cap total bias
    bias = max(-_MAX_BIAS, min(_MAX_BIAS, bias))

    if abs(bias) < 1e-9:
        return forecast_df, {"total_bias": 0.0, "signals": []}

    # Build taper: full effect day 0, zero at day _TAPER_DAYS
    n = len(forecast_df)
    taper = np.array([
        max(0.0, 1.0 - i / _TAPER_DAYS) for i in range(n)
    ])
    multiplier = 1.0 + bias * taper

    for col in ("yhat", "yhat_lower", "yhat_upper"):
        if col in forecast_df.columns:
            forecast_df[col] = forecast_df[col] * multiplier

    return forecast_df, {
        "total_bias": round(bias, 4),
        "signals": signals,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/test_forecast_regime.py -v`
Expected: All 15 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tools/_forecast_regime.py tests/backend/test_forecast_regime.py
git commit -m "feat(forecast): add post-Prophet technical bias adjustment

RSI overbought/oversold, MACD crossover, and volume spike signals
dampen or amplify forecast. Capped at ±15%, tapers linearly over
30 days. Returns metadata for confidence score transparency.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 3: Tier 1 + Tier 2 Feature Computation Module

**Files:**
- Create: `backend/tools/_forecast_features.py`
- Create: `tests/backend/test_forecast_features.py`

- [ ] **Step 1: Write failing tests for feature computation**

```python
# tests/backend/test_forecast_features.py
"""Tests for Tier 1/2 forecast feature computation."""
import numpy as np
import pandas as pd
import pytest

from backend.tools._forecast_features import (
    compute_tier1_features,
    compute_tier2_features,
    build_future_features,
    get_sector_index_mapping,
)


class TestComputeTier1:
    def test_analysis_summary_features(self):
        analysis = {
            "annualized_volatility": 45.0,
            "bull_phase_pct": 60.0,
            "bear_phase_pct": 30.0,
            "support_level": 90.0,
            "resistance_level": 110.0,
        }
        current_price = 100.0
        result = compute_tier1_features(
            analysis_row=analysis,
            piotroski_row=None,
            quarterly_rows=None,
            current_price=current_price,
        )
        assert "volatility_regime" in result
        assert result["volatility_regime"] == pytest.approx(0.45)
        assert "trend_strength" in result
        assert result["trend_strength"] == pytest.approx(0.30)
        assert "sr_position" in result
        assert result["sr_position"] == pytest.approx(0.50)

    def test_piotroski_feature(self):
        result = compute_tier1_features(
            analysis_row=None,
            piotroski_row={"f_score": 7},
            quarterly_rows=None,
            current_price=100.0,
        )
        assert result["piotroski"] == pytest.approx(7 / 9)

    def test_quarterly_growth(self):
        rows = [
            {"quarter_end": "2025-12-31", "total_revenue": 120.0,
             "diluted_eps": 5.0},
            {"quarter_end": "2025-09-30", "total_revenue": 100.0,
             "diluted_eps": 4.0},
        ]
        result = compute_tier1_features(
            analysis_row=None,
            piotroski_row=None,
            quarterly_rows=rows,
            current_price=100.0,
        )
        assert result["revenue_growth"] == pytest.approx(0.20)
        assert result["eps_growth"] == pytest.approx(0.25)

    def test_growth_capped_at_1(self):
        rows = [
            {"quarter_end": "2025-12-31", "total_revenue": 500.0,
             "diluted_eps": 10.0},
            {"quarter_end": "2025-09-30", "total_revenue": 100.0,
             "diluted_eps": 1.0},
        ]
        result = compute_tier1_features(
            analysis_row=None,
            piotroski_row=None,
            quarterly_rows=rows,
            current_price=100.0,
        )
        assert result["revenue_growth"] == 1.0  # capped
        assert result["eps_growth"] == 1.0       # capped

    def test_missing_everything_returns_defaults(self):
        result = compute_tier1_features(
            analysis_row=None,
            piotroski_row=None,
            quarterly_rows=None,
            current_price=100.0,
        )
        assert result["volatility_regime"] == 0.0
        assert result["piotroski"] == 0.0
        assert result["revenue_growth"] == 0.0


class TestComputeTier2:
    def _make_ohlcv(self, n=60):
        dates = pd.date_range("2025-10-01", periods=n, freq="B")
        return pd.DataFrame({
            "date": dates,
            "close": np.random.uniform(95, 105, n),
            "volume": np.random.randint(100000, 500000, n),
        })

    def test_volume_anomaly(self):
        df = self._make_ohlcv()
        result = compute_tier2_features(
            ohlcv_df=df,
            sector_index_df=None,
            earnings_dates=None,
        )
        assert "volume_anomaly" in result
        assert isinstance(result["volume_anomaly"], float)

    def test_calendar_features(self):
        df = self._make_ohlcv()
        result = compute_tier2_features(
            ohlcv_df=df,
            sector_index_df=None,
            earnings_dates=None,
        )
        assert "day_of_week" in result
        assert "month_of_year" in result
        assert 0.0 <= result["day_of_week"] <= 1.0
        assert 0.0 <= result["month_of_year"] <= 1.0

    def test_sector_relative_strength(self):
        n = 60
        dates = pd.date_range("2025-10-01", periods=n, freq="B")
        ohlcv = pd.DataFrame({
            "date": dates,
            "close": np.linspace(100, 120, n),  # +20%
            "volume": [100000] * n,
        })
        sector = pd.DataFrame({
            "date": dates,
            "close": np.linspace(100, 105, n),  # +5%
        })
        result = compute_tier2_features(
            ohlcv_df=ohlcv,
            sector_index_df=sector,
            earnings_dates=None,
        )
        assert "sector_relative_strength" in result
        assert result["sector_relative_strength"] > 0  # outperforming


class TestBuildFutureFeatures:
    def test_holds_constant(self):
        last_known = {
            "volatility_regime": 0.45,
            "trend_strength": 0.30,
            "piotroski": 0.78,
            "volume_anomaly": 0.0,
            "day_of_week": 0.4,
            "month_of_year": 0.25,
        }
        future_dates = pd.date_range("2026-01-01", periods=90, freq="B")
        df = build_future_features(last_known, future_dates)
        assert len(df) == 90
        # Slow-moving features held constant
        assert (df["piotroski"] == 0.78).all()
        # Calendar features vary per date
        assert df["day_of_week"].nunique() == 5
        assert df["month_of_year"].nunique() >= 3


class TestSectorMapping:
    def test_india_mapping(self):
        mapping = get_sector_index_mapping("india")
        assert "Financial Services" in mapping
        assert mapping["Financial Services"] == "^NSEBANK"

    def test_us_mapping(self):
        mapping = get_sector_index_mapping("us")
        assert "Technology" in mapping
        assert mapping["Technology"] == "XLK"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/test_forecast_features.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the features module**

```python
# backend/tools/_forecast_features.py
"""Tier 1 + Tier 2 forecast feature computation.

Tier 1: Features from existing Iceberg tables (analysis_summary,
piotroski_scores, quarterly_results). No new ingestion.

Tier 2: Computed features from OHLCV (sector relative strength,
volume-price, calendar effects).
"""
import logging
from datetime import datetime

import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)

# ── Sector index mapping ────────────────────────────────

_INDIA_SECTOR_MAP = {
    "Financial Services": "^NSEBANK",
    "Information Technology": "^CNXIT",
    "Pharmaceutical": "^CNXPHARMA",
    "Fast Moving Consumer Goods": "^CNXFMCG",
    "Automobile and Auto Components": "^CNXAUTO",
}

_US_SECTOR_MAP = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
}


def get_sector_index_mapping(market: str) -> dict[str, str]:
    """Return sector → index ticker mapping for a market."""
    if market == "india":
        return dict(_INDIA_SECTOR_MAP)
    return dict(_US_SECTOR_MAP)


# ── Tier 1: Existing data features ──────────────────────

def compute_tier1_features(
    analysis_row: dict | None,
    piotroski_row: dict | None,
    quarterly_rows: list[dict] | None,
    current_price: float,
) -> dict[str, float]:
    """Compute Tier 1 features from existing Iceberg data.

    All values normalized to reasonable ranges for Prophet regressors.
    Returns dict of feature_name → float.
    """
    features: dict[str, float] = {}

    # ── analysis_summary features ──
    if analysis_row:
        vol = analysis_row.get("annualized_volatility", 0.0)
        features["volatility_regime"] = (vol or 0.0) / 100.0

        bull = analysis_row.get("bull_phase_pct", 50.0) or 50.0
        bear = analysis_row.get("bear_phase_pct", 50.0) or 50.0
        features["trend_strength"] = (bull - bear) / 100.0

        sup = analysis_row.get("support_level")
        res = analysis_row.get("resistance_level")
        if sup and res and res > sup:
            features["sr_position"] = (
                (current_price - sup) / (res - sup)
            )
        else:
            features["sr_position"] = 0.5
    else:
        features["volatility_regime"] = 0.0
        features["trend_strength"] = 0.0
        features["sr_position"] = 0.5

    # ── piotroski f-score ──
    if piotroski_row:
        f = piotroski_row.get("f_score", 0)
        features["piotroski"] = (f or 0) / 9.0
    else:
        features["piotroski"] = 0.0

    # ── quarterly growth ──
    if quarterly_rows and len(quarterly_rows) >= 2:
        curr, prev = quarterly_rows[0], quarterly_rows[1]
        features["revenue_growth"] = _safe_growth(
            curr.get("total_revenue"),
            prev.get("total_revenue"),
        )
        features["eps_growth"] = _safe_growth(
            curr.get("diluted_eps"),
            prev.get("diluted_eps"),
        )
    else:
        features["revenue_growth"] = 0.0
        features["eps_growth"] = 0.0

    return features


def _safe_growth(curr, prev) -> float:
    """Compute growth ratio, capped at ±1.0."""
    if curr is None or prev is None:
        return 0.0
    if isinstance(curr, float) and np.isnan(curr):
        return 0.0
    if isinstance(prev, float) and np.isnan(prev):
        return 0.0
    if abs(prev) < 1e-9:
        return 0.0
    g = (curr - prev) / abs(prev)
    return max(-1.0, min(1.0, g))


# ── Tier 2: Computed features ────────────────────────────

def compute_tier2_features(
    ohlcv_df: pd.DataFrame,
    sector_index_df: pd.DataFrame | None,
    earnings_dates: list | None,
) -> dict[str, float]:
    """Compute Tier 2 features from OHLCV and sector data.

    Returns dict of feature_name → float (last known value).
    """
    features: dict[str, float] = {}
    last_date = pd.Timestamp(ohlcv_df["date"].iloc[-1])

    # ── Volume anomaly ──
    vol_sma = ohlcv_df["volume"].rolling(20).mean()
    last_vol = float(ohlcv_df["volume"].iloc[-1])
    last_sma = float(vol_sma.iloc[-1])
    if last_sma > 0:
        features["volume_anomaly"] = (last_vol / last_sma) - 1.0
    else:
        features["volume_anomaly"] = 0.0

    # ── OBV trend ──
    close = ohlcv_df["close"].values
    volume = ohlcv_df["volume"].values
    direction = np.sign(np.diff(close, prepend=close[0]))
    obv = np.cumsum(direction * volume)
    if len(obv) >= 20:
        x = np.arange(20, dtype=float)
        y = obv[-20:].astype(float)
        slope = float(np.polyfit(x, y, 1)[0])
        # Normalize by mean volume
        mean_vol = float(np.mean(volume[-20:]))
        features["obv_trend"] = slope / mean_vol if mean_vol > 0 else 0.0
    else:
        features["obv_trend"] = 0.0

    # ── Sector relative strength ──
    if sector_index_df is not None and len(sector_index_df) >= 20:
        ticker_ret = (
            float(ohlcv_df["close"].iloc[-1])
            / float(ohlcv_df["close"].iloc[-20])
            - 1.0
        )
        sector_ret = (
            float(sector_index_df["close"].iloc[-1])
            / float(sector_index_df["close"].iloc[-20])
            - 1.0
        )
        features["sector_relative_strength"] = ticker_ret - sector_ret
    else:
        features["sector_relative_strength"] = 0.0

    # ── Calendar features (last known date) ──
    features["day_of_week"] = last_date.dayofweek / 4.0
    features["month_of_year"] = last_date.month / 12.0

    # ── F&O expiry proximity (India) ──
    features["expiry_proximity"] = _days_to_expiry(last_date) / 30.0

    # ── Earnings proximity ──
    if earnings_dates:
        features["earnings_proximity"] = (
            _days_to_nearest_earnings(last_date, earnings_dates) / 90.0
        )
    else:
        features["earnings_proximity"] = 0.5

    return features


def _days_to_expiry(dt: pd.Timestamp) -> float:
    """Days until last Thursday of current month."""
    import calendar
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    last_date = pd.Timestamp(dt.year, dt.month, last_day)
    # Walk back to Thursday (3)
    while last_date.dayofweek != 3:
        last_date -= pd.Timedelta(days=1)
    diff = (last_date - dt).days
    if diff < 0:
        # Next month
        next_month = dt + pd.offsets.MonthBegin(1)
        last_day = calendar.monthrange(
            next_month.year, next_month.month
        )[1]
        last_date = pd.Timestamp(
            next_month.year, next_month.month, last_day
        )
        while last_date.dayofweek != 3:
            last_date -= pd.Timedelta(days=1)
        diff = (last_date - dt).days
    return float(max(diff, 0))


def _days_to_nearest_earnings(
    dt: pd.Timestamp,
    dates: list,
) -> float:
    """Days to nearest earnings date (past or future)."""
    if not dates:
        return 45.0  # default mid-quarter
    diffs = [abs((pd.Timestamp(d) - dt).days) for d in dates]
    return float(min(diffs))


# ── Future feature builder ───────────────────────────────

def build_future_features(
    last_known: dict[str, float],
    future_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Build future regressor DataFrame for Prophet.

    Slow-moving features held constant at last known value.
    Calendar features computed per date.
    Volume anomaly set to 0 (neutral).
    """
    n = len(future_dates)
    df = pd.DataFrame({"ds": future_dates})

    # Slow-moving: hold constant
    for key in (
        "volatility_regime", "trend_strength", "sr_position",
        "piotroski", "revenue_growth", "eps_growth",
        "sector_relative_strength",
    ):
        df[key] = last_known.get(key, 0.0)

    # Transient: set to neutral
    df["volume_anomaly"] = 0.0
    df["obv_trend"] = 0.0

    # Calendar: compute per date
    df["day_of_week"] = df["ds"].dt.dayofweek / 4.0
    df["month_of_year"] = df["ds"].dt.month / 12.0

    # Expiry proximity per date
    df["expiry_proximity"] = df["ds"].apply(
        lambda d: _days_to_expiry(pd.Timestamp(d)) / 30.0
    )

    # Earnings: hold constant (approximate)
    df["earnings_proximity"] = last_known.get(
        "earnings_proximity", 0.5
    )

    return df
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/test_forecast_features.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tools/_forecast_features.py tests/backend/test_forecast_features.py
git commit -m "feat(forecast): add Tier 1/2 feature computation module

Tier 1: volatility regime, trend strength, support/resistance position,
Piotroski F-Score, revenue/EPS growth from existing Iceberg tables.
Tier 2: sector relative strength, volume anomaly, OBV trend, calendar
effects, F&O expiry and earnings proximity.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 4: Confidence Score Computation

**Files:**
- Modify: `backend/tools/_forecast_accuracy.py`
- Create: `tests/backend/test_forecast_confidence.py`

- [ ] **Step 1: Write failing tests for confidence scoring**

```python
# tests/backend/test_forecast_confidence.py
"""Tests for composite confidence score computation."""
import pytest

from backend.tools._forecast_accuracy import compute_confidence_score


class TestConfidenceScore:
    def test_perfect_score(self):
        metrics = {
            "directional_accuracy_pct": 80.0,
            "MAPE_pct": 5.0,
            "coverage": 0.80,
            "interval_width_ratio": 0.10,
        }
        score, components = compute_confidence_score(
            metrics, data_completeness=1.0
        )
        assert score > 0.8
        assert "direction" in components
        assert "mase" in components

    def test_terrible_score(self):
        metrics = {
            "directional_accuracy_pct": 30.0,
            "MAPE_pct": 100.0,
            "coverage": 0.20,
            "interval_width_ratio": 2.0,
        }
        score, _ = compute_confidence_score(
            metrics, data_completeness=0.2
        )
        assert score < 0.35

    def test_badge_high(self):
        score = 0.70
        from backend.tools._forecast_accuracy import confidence_badge
        badge, reason = confidence_badge(score, {})
        assert badge == "High"

    def test_badge_medium(self):
        score = 0.50
        from backend.tools._forecast_accuracy import confidence_badge
        badge, _ = confidence_badge(score, {})
        assert badge == "Medium"

    def test_badge_low(self):
        score = 0.30
        from backend.tools._forecast_accuracy import confidence_badge
        badge, reason = confidence_badge(score, {})
        assert badge == "Low"
        assert len(reason) > 0

    def test_rejection_threshold(self):
        score = 0.20
        from backend.tools._forecast_accuracy import confidence_badge
        badge, reason = confidence_badge(score, {})
        assert badge == "Rejected"

    def test_missing_metrics_returns_low(self):
        score, _ = compute_confidence_score({}, data_completeness=0.0)
        assert score < 0.40

    def test_mase_from_mape(self):
        """MASE approximated from MAPE when not available."""
        metrics = {
            "directional_accuracy_pct": 60.0,
            "MAPE_pct": 15.0,
            "coverage": 0.75,
            "interval_width_ratio": 0.30,
        }
        score, components = compute_confidence_score(
            metrics, data_completeness=0.8
        )
        assert 0.0 <= components["mase"] <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/test_forecast_confidence.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_confidence_score'`

- [ ] **Step 3: Implement confidence scoring**

Add to end of `backend/tools/_forecast_accuracy.py`:

```python
# ── Composite confidence score ───────────────────────────

def compute_confidence_score(
    metrics: dict,
    data_completeness: float,
) -> tuple[float, dict]:
    """Compute composite confidence score for a forecast.

    Args:
        metrics: Dict with directional_accuracy_pct, MAPE_pct,
            coverage, interval_width_ratio.
        data_completeness: Fraction of regressors available (0-1).

    Returns:
        (score, components) — score is 0-1, components is dict of
        sub-scores for transparency.
    """
    # Direction score: 50% = random, 100% = perfect
    dir_acc = metrics.get("directional_accuracy_pct", 50.0) or 50.0
    direction = max(0.0, min(1.0, (dir_acc - 30.0) / 50.0))

    # MASE approximation from MAPE (naive ~20% MAPE for stocks)
    mape = metrics.get("MAPE_pct", 50.0) or 50.0
    mase_approx = min(mape / 20.0, 2.0)
    mase = max(0.0, 1.0 - mase_approx / 2.0)

    # Coverage calibration: 0.80 is ideal
    cov = metrics.get("coverage", 0.5) or 0.5
    coverage = max(0.0, 1.0 - abs(cov - 0.80) * 5.0)

    # Interval width: narrow = confident
    iw = metrics.get("interval_width_ratio", 0.5) or 0.5
    interval = max(0.0, 1.0 - min(iw, 1.0))

    # Data completeness passed through
    data = max(0.0, min(1.0, data_completeness))

    score = (
        0.25 * direction
        + 0.25 * mase
        + 0.20 * coverage
        + 0.15 * interval
        + 0.15 * data
    )

    components = {
        "direction": round(direction, 3),
        "mase": round(mase, 3),
        "coverage": round(coverage, 3),
        "interval": round(interval, 3),
        "data_completeness": round(data, 3),
    }

    return round(score, 4), components


def confidence_badge(
    score: float,
    components: dict,
) -> tuple[str, str]:
    """Map confidence score to badge label and reason.

    Returns:
        (badge, reason) — badge is High/Medium/Low/Rejected.
    """
    if score < 0.25:
        reason = _build_reason("Rejected", score, components)
        return "Rejected", reason
    if score < 0.40:
        reason = _build_reason("Low", score, components)
        return "Low", reason
    if score < 0.65:
        return "Medium", ""
    return "High", ""


def _build_reason(badge: str, score: float, components: dict) -> str:
    """Build human-readable reason for low/rejected badge."""
    issues = []
    if components.get("direction", 1.0) < 0.4:
        issues.append("low directional accuracy")
    if components.get("mase", 1.0) < 0.3:
        issues.append("high forecast error")
    if components.get("coverage", 1.0) < 0.3:
        issues.append("poor prediction interval coverage")
    if components.get("data_completeness", 1.0) < 0.4:
        issues.append("limited data signals")
    if not issues:
        issues.append("overall low model fit")
    return f"{badge} confidence: {', '.join(issues)}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/test_forecast_confidence.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tools/_forecast_accuracy.py tests/backend/test_forecast_confidence.py
git commit -m "feat(forecast): add composite confidence score + badge mapping

Weighted score from directional accuracy, MASE, coverage calibration,
interval width, and data completeness. Maps to High/Medium/Low/Rejected
badges with human-readable explanations for low scores.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 5: Wire Regime Config into Prophet Model

**Files:**
- Modify: `backend/tools/_forecast_model.py` (lines 55-128)
- Modify: `backend/tools/_forecast_shared.py` (lines 131-188)

- [ ] **Step 1: Modify `_train_prophet_model` to accept regime config**

In `backend/tools/_forecast_model.py`, change the function signature and Prophet instantiation:

```python
# Line 55: Update signature
def _train_prophet_model(
    prophet_df: pd.DataFrame,
    ticker: str = "",
    regressors: pd.DataFrame | None = None,
    regime_config: "RegimeConfig | None" = None,
) -> tuple:
```

Replace the hardcoded Prophet instantiation (lines 96-102) with:

```python
    # ── Build Prophet config from regime ──
    from backend.tools._forecast_regime import (
        RegimeConfig, build_prophet_config, compute_logistic_bounds,
        _MODERATE,
    )
    cfg = regime_config or _MODERATE
    prophet_kwargs = build_prophet_config(cfg)
    prophet_kwargs["holidays"] = hols if not hols.empty else None

    # ── Log transform ──
    if cfg.log_transform:
        prophet_df = prophet_df.copy()
        prophet_df["y"] = np.log(prophet_df["y"].clip(lower=0.01))

    # ── Logistic growth bounds ──
    if cfg.growth == "logistic":
        cap, floor = compute_logistic_bounds(prophet_df)
        if cfg.log_transform:
            cap, floor = np.log(cap), np.log(max(floor, 0.01))
        prophet_df["cap"] = cap
        prophet_df["floor"] = floor

    model = Prophet(**prophet_kwargs)
```

Add `import numpy as np` at top if not present.

- [ ] **Step 2: Update `_generate_forecast` for log-transform and logistic**

In `backend/tools/_forecast_shared.py`, update `_generate_forecast()` (line 131):

```python
def _generate_forecast(
    model: Prophet,
    prophet_df: pd.DataFrame,
    months: int,
    regressors: pd.DataFrame | None = None,
    regime_config: "RegimeConfig | None" = None,
) -> pd.DataFrame:
```

After creating `future` DataFrame (line 154), add logistic bounds:

```python
    from backend.tools._forecast_regime import _MODERATE
    cfg = regime_config or _MODERATE

    if cfg.growth == "logistic":
        # Carry forward cap/floor from training data
        future["cap"] = prophet_df["cap"].iloc[-1]
        future["floor"] = prophet_df["floor"].iloc[-1]
```

After `forecast = model.predict(future)` (around line 170), reverse log-transform:

```python
    if cfg.log_transform:
        for col in ("yhat", "yhat_lower", "yhat_upper"):
            if col in forecast.columns:
                forecast[col] = np.exp(forecast[col])
```

- [ ] **Step 3: Run existing forecast tests to verify no regression**

Run: `python -m pytest tests/backend/ -k "forecast" -v`
Expected: All existing tests PASS (regime_config defaults to moderate, preserving behavior)

- [ ] **Step 4: Commit**

```bash
git add backend/tools/_forecast_model.py backend/tools/_forecast_shared.py
git commit -m "feat(forecast): wire regime config into Prophet model

_train_prophet_model accepts regime_config for per-ticker growth mode,
changepoint params, log-transform, and logistic bounds. _generate_forecast
carries forward bounds and reverses log-transform. Defaults to moderate
regime for backward compatibility.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 6: Wire Tier 1/2 Regressors into Forecast Pipeline

**Files:**
- Modify: `backend/tools/_forecast_shared.py` (lines 118-380)
- Modify: `stocks/repository.py`

- [ ] **Step 1: Add batch readers to repository**

Add to `stocks/repository.py` (after existing `get_piotroski_scores` at line 3084):

```python
    def get_piotroski_scores_batch(
        self, tickers: list[str],
    ) -> dict[str, dict]:
        """Get latest Piotroski scores for multiple tickers.

        Returns dict: ticker → {f_score, ...}
        """
        df = self.get_piotroski_scores()
        if df is None or df.empty:
            return {}
        if "ticker" in df.columns:
            df = df[df["ticker"].isin(tickers)]
            latest = df.sort_values("score_date").groupby(
                "ticker"
            ).last()
            return latest.to_dict("index")
        return {}

    def get_quarterly_results_batch(
        self, tickers: list[str],
    ) -> dict[str, list[dict]]:
        """Get latest 2 quarters per ticker for growth calc.

        Returns dict: ticker → [latest_q, prev_q]
        """
        result = {}
        for t in tickers:
            df = self.get_quarterly_results(t)
            if df is not None and len(df) >= 2:
                result[t] = df.head(2).to_dict("records")
        return result
```

- [ ] **Step 2: Add enriched regressor loading to `_forecast_shared.py`**

Add a new function after `_load_regressors_from_iceberg()`:

```python
def _enrich_regressors(
    regressors: pd.DataFrame,
    ticker: str,
    tier1_features: dict[str, float],
    tier2_features: dict[str, float],
) -> pd.DataFrame:
    """Merge Tier 1/2 features into regressor DataFrame.

    Tier 1/2 features are scalar (last known value). They are
    broadcast across all dates in the regressors DataFrame.
    """
    all_features = {**tier1_features, **tier2_features}
    for name, value in all_features.items():
        if name not in ("day_of_week", "month_of_year",
                        "expiry_proximity"):
            regressors[name] = value
        else:
            # Calendar features: compute per date
            if name == "day_of_week":
                regressors[name] = regressors["ds"].dt.dayofweek / 4.0
            elif name == "month_of_year":
                regressors[name] = regressors["ds"].dt.month / 12.0
            elif name == "expiry_proximity":
                from backend.tools._forecast_features import (
                    _days_to_expiry,
                )
                regressors[name] = regressors["ds"].apply(
                    lambda d: _days_to_expiry(pd.Timestamp(d)) / 30.0
                )
    return regressors
```

- [ ] **Step 3: Run tests to verify no regression**

Run: `python -m pytest tests/backend/ -k "forecast" -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tools/_forecast_shared.py stocks/repository.py
git commit -m "feat(forecast): add batch readers + regressor enrichment

Add get_piotroski_scores_batch and get_quarterly_results_batch to
repository. Add _enrich_regressors to merge Tier 1/2 scalar features
into Prophet regressors DataFrame with per-date calendar computation.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 7: Wire Everything into Batch Executor

**Files:**
- Modify: `backend/jobs/executor.py` (lines 1248-1640)

- [ ] **Step 1: Add Tier 1/2 bulk pre-loads to executor**

In `execute_run_forecasts()`, after the existing OHLCV and forecast_runs pre-loads (around line 1380), add:

```python
    # ── Pre-load Tier 1 data (NEW) ──
    _log.info("Pre-loading analysis_summary for %d tickers", len(tickers))
    _analysis_cache = {}
    try:
        analysis_df = repo.get_analysis_summary_batch(tickers)
        if analysis_df is not None and not analysis_df.empty:
            for _, row in analysis_df.iterrows():
                _analysis_cache[row["ticker"]] = row.to_dict()
    except Exception:
        _log.warning("Failed to pre-load analysis_summary", exc_info=True)

    _piotroski_cache = repo.get_piotroski_scores_batch(tickers)
    _quarterly_cache = repo.get_quarterly_results_batch(tickers)

    # ── Pre-load sector index OHLCV (NEW) ──
    from backend.tools._forecast_features import get_sector_index_mapping
    from backend.market_utils import detect_market
    market = "india" if scope == "india" else "us"
    sector_map = get_sector_index_mapping(market)
    _sector_ohlcv_cache = {}
    sector_tickers = list(set(sector_map.values()))
    for st in sector_tickers:
        try:
            sdf = repo.get_ohlcv(st)
            if sdf is not None and not sdf.empty:
                _sector_ohlcv_cache[st] = sdf
        except Exception:
            pass
```

- [ ] **Step 2: Wire regime + features into per-ticker closure**

In `_forecast_one(ticker)` (around line 1382), after loading OHLCV and before training Prophet:

```python
        # ── Regime classification (NEW) ──
        from backend.tools._forecast_regime import classify_regime
        analysis_row = _analysis_cache.get(ticker)
        regime_cfg = classify_regime(ticker, analysis_row)

        # ── Tier 1 features (NEW) ──
        from backend.tools._forecast_features import (
            compute_tier1_features,
            compute_tier2_features,
        )
        piotroski_row = _piotroski_cache.get(ticker)
        quarterly_rows = _quarterly_cache.get(ticker)
        current_price = float(ohlcv_df["close"].iloc[-1])

        tier1 = compute_tier1_features(
            analysis_row, piotroski_row, quarterly_rows, current_price
        )

        # ── Tier 2 features (NEW) ──
        company_sector = (analysis_row or {}).get("sector", "")
        sector_idx = sector_map.get(company_sector)
        sector_df = _sector_ohlcv_cache.get(sector_idx)

        tier2 = compute_tier2_features(
            ohlcv_df, sector_df, earnings_dates=None
        )

        # ── Enrich regressors (NEW) ──
        from backend.tools._forecast_shared import _enrich_regressors
        if regressors is not None:
            regressors = _enrich_regressors(
                regressors, ticker, tier1, tier2
            )
```

Pass `regime_config=regime_cfg` to `_train_prophet_model()` and `_generate_forecast()`:

```python
        model, train_df = _train_prophet_model(
            prophet_df, ticker=ticker, regressors=regressors,
            regime_config=regime_cfg,
        )
        forecast_df = _generate_forecast(
            model, prophet_df, 9, regressors=regressors,
            regime_config=regime_cfg,
        )
```

- [ ] **Step 3: Wire technical bias + confidence after Prophet**

After forecast generation, before result accumulation:

```python
        # ── Technical bias adjustment (NEW) ──
        from backend.tools._forecast_regime import apply_technical_bias
        forecast_df, bias_meta = apply_technical_bias(
            forecast_df, analysis_row
        )

        # ── Confidence score (NEW) ──
        from backend.tools._forecast_accuracy import (
            compute_confidence_score,
            confidence_badge,
        )
        total_regressors = 14  # market(2) + macro(5) + sentiment(1)
                               # + tier1(6) + tier2(partial)
        available = sum(1 for v in {**tier1, **tier2}.values()
                        if v != 0.0) + 3  # market+macro always present
        data_comp = min(available / total_regressors, 1.0)

        conf_score, conf_components = compute_confidence_score(
            accuracy_metrics, data_comp
        )
        badge, badge_reason = confidence_badge(
            conf_score, conf_components
        )
```

Add confidence to the run dict:

```python
        run_dict["confidence_score"] = conf_score
        run_dict["confidence_components"] = json.dumps({
            **conf_components,
            "regime": regime_cfg.regime,
            "bias": bias_meta,
            "badge": badge,
            "reason": badge_reason,
        })
```

- [ ] **Step 4: Run full forecast tests**

Run: `python -m pytest tests/backend/ -k "forecast" -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/jobs/executor.py
git commit -m "feat(forecast): wire regime, features, bias, confidence into batch executor

Batch pre-loads analysis_summary, piotroski, quarterly, sector indices.
Per-ticker: classify regime, compute Tier 1/2 features, enrich regressors,
train adaptive Prophet, apply technical bias, compute confidence score.
Stores confidence_score + components in forecast_runs.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 8: Evolve forecast_runs Schema

**Files:**
- Modify: `stocks/create_tables.py` (lines 591-732)
- Modify: `stocks/repository.py`

- [ ] **Step 1: Add confidence columns to forecast_runs schema**

In `stocks/create_tables.py`, in `_forecast_runs_schema()`, after the last field (field_id 25 `computed_at`), add:

```python
        NestedField(
            field_id=26,
            name="confidence_score",
            field_type=DoubleType(),
            required=False,
        ),
        NestedField(
            field_id=27,
            name="confidence_components",
            field_type=StringType(),
            required=False,
        ),
```

- [ ] **Step 2: Update repository write method**

In `stocks/repository.py`, find the forecast_runs Arrow schema builder and add the two new columns to the schema mapping. Add `confidence_score` (float64) and `confidence_components` (string) to the `pa.schema` definition.

- [ ] **Step 3: Run schema evolution**

Run: `PYTHONPATH=.:backend python -c "from stocks.repository import StockRepository; r = StockRepository(); r._ensure_tables()"`
Expected: Schema evolved with 2 new columns

- [ ] **Step 4: Commit**

```bash
git add stocks/create_tables.py stocks/repository.py
git commit -m "feat(schema): add confidence_score + confidence_components to forecast_runs

Two new optional columns: confidence_score (float, 0-1) and
confidence_components (JSON string with sub-scores, regime, bias,
badge, and reason).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 9: Wire Regime + Features into Chat Forecast

**Files:**
- Modify: `backend/tools/forecasting_tool.py` (lines 210-249)

- [ ] **Step 1: Add regime + features to single-ticker chat forecast**

In `forecast_stock()`, before the call to `_train_prophet_model()` (around line 210):

```python
    # ── Regime classification ──
    from backend.tools._forecast_regime import classify_regime
    from backend.tools._forecast_features import (
        compute_tier1_features, compute_tier2_features,
    )
    from backend.tools._forecast_shared import _enrich_regressors

    analysis_row = None
    try:
        analysis_df = repo.get_analysis_summary_batch([ticker])
        if analysis_df is not None and not analysis_df.empty:
            analysis_row = analysis_df.iloc[0].to_dict()
    except Exception:
        pass

    regime_cfg = classify_regime(ticker, analysis_row)

    # Tier 1
    piotroski_row = None
    try:
        scores = repo.get_piotroski_scores_batch([ticker])
        piotroski_row = scores.get(ticker)
    except Exception:
        pass

    quarterly_rows = None
    try:
        qr = repo.get_quarterly_results_batch([ticker])
        quarterly_rows = qr.get(ticker)
    except Exception:
        pass

    current_price = float(prophet_df["y"].iloc[-1])
    tier1 = compute_tier1_features(
        analysis_row, piotroski_row, quarterly_rows, current_price
    )
    tier2 = compute_tier2_features(ohlcv_df, None, None)

    if regressors is not None:
        regressors = _enrich_regressors(
            regressors, ticker, tier1, tier2
        )
```

Pass `regime_config=regime_cfg` to both `_train_prophet_model()` and `_generate_forecast()`.

After forecast generation, apply bias:

```python
    from backend.tools._forecast_regime import apply_technical_bias
    forecast_df, bias_meta = apply_technical_bias(
        forecast_df, analysis_row
    )
```

- [ ] **Step 2: Add confidence to chat forecast output**

After accuracy computation, add:

```python
    from backend.tools._forecast_accuracy import (
        compute_confidence_score, confidence_badge,
    )
    total = 14
    avail = sum(1 for v in {**tier1, **tier2}.values() if v != 0.0) + 3
    conf_score, conf_comp = compute_confidence_score(
        accuracy_result, min(avail / total, 1.0)
    )
    badge, reason = confidence_badge(conf_score, conf_comp)
```

Add to the report string:

```python
    report += f"\n**Confidence:** {badge} ({conf_score:.0%})"
    if reason:
        report += f" — {reason}"
    report += f"\n**Regime:** {regime_cfg.regime}"
```

- [ ] **Step 3: Run chat forecast tests**

Run: `python -m pytest tests/backend/ -k "forecast" -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tools/forecasting_tool.py
git commit -m "feat(forecast): wire regime + features + confidence into chat forecast

Single-ticker chat forecast now uses volatility-regime adaptive Prophet,
Tier 1/2 features, technical bias adjustment, and confidence scoring.
Report includes confidence badge and regime classification.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 10: Ingest Sector Index OHLCV

**Files:**
- Modify: `backend/tools/_stock_registry.py`
- Modify: `backend/pipeline/runner.py`

- [ ] **Step 1: Add sector indices to stock registry**

In `backend/tools/_stock_registry.py`, add sector index tickers to the registry data so they get ingested by the pipeline:

```python
# Sector indices for forecast enrichment
SECTOR_INDICES = [
    # India
    "^NSEBANK", "^CNXIT", "^CNXPHARMA", "^CNXFMCG", "^CNXAUTO",
    # US
    "XLK", "XLF", "XLE", "XLV", "XLY",
]
```

- [ ] **Step 2: Wire sector indices into bulk-download pipeline**

In `backend/pipeline/runner.py`, in the `bulk_download` command, ensure sector indices are included in the download batch. Add them to the ticker list if not already present:

```python
    # Include sector indices for forecast enrichment
    from backend.tools._stock_registry import SECTOR_INDICES
    for idx in SECTOR_INDICES:
        if idx not in ticker_list:
            ticker_list.append(idx)
```

- [ ] **Step 3: Test sector index download**

Run: `PYTHONPATH=.:backend python -m backend.pipeline.runner bulk-download --tickers "^NSEBANK,^CNXIT,XLK,XLF" --limit 5`
Expected: Downloads OHLCV for 4 sector indices

- [ ] **Step 4: Commit**

```bash
git add backend/tools/_stock_registry.py backend/pipeline/runner.py
git commit -m "feat(pipeline): add sector index OHLCV ingestion for forecast enrichment

Add 10 sector indices (5 India, 5 US) to stock registry. Wire into
bulk-download pipeline so sector relative strength can be computed
during forecast runs.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 11: Frontend Confidence Badge

**Files:**
- Modify: `frontend/components/charts/ForecastChart.tsx`
- Modify: `frontend/lib/types.ts`

- [ ] **Step 1: Add confidence fields to forecast types**

In `frontend/lib/types.ts`, find the forecast-related type and add:

```typescript
export interface ForecastConfidence {
  score: number;
  badge: "High" | "Medium" | "Low" | "Rejected";
  reason: string;
  components: {
    direction: number;
    mase: number;
    coverage: number;
    interval: number;
    data_completeness: number;
  };
  regime: string;
}
```

- [ ] **Step 2: Add confidence badge component to ForecastChart**

In `frontend/components/charts/ForecastChart.tsx`, add the confidence badge near the existing sentiment display (around line 641):

```tsx
{/* Confidence Badge */}
{confidence && confidence.badge !== "Rejected" && (
  <div className="relative inline-block ml-3">
    <span
      className={`
        inline-flex items-center px-2 py-0.5 rounded-full
        text-xs font-medium cursor-pointer
        ${confidence.badge === "High"
          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
          : confidence.badge === "Medium"
          ? "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400"
          : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
        }
      `}
      onClick={() => setShowConfidence(!showConfidence)}
    >
      {confidence.badge} Confidence
    </span>
    {showConfidence && (
      <div className="absolute z-10 mt-1 w-64 p-3 bg-white dark:bg-gray-800
        border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg text-xs">
        <div className="space-y-1.5">
          <div className="flex justify-between">
            <span className="text-gray-500">Directional accuracy</span>
            <span>{(confidence.components.direction * 100).toFixed(0)}%</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Forecast error (MASE)</span>
            <span>{confidence.components.mase.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Interval coverage</span>
            <span>{(confidence.components.coverage * 100).toFixed(0)}%</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Data signals</span>
            <span>{(confidence.components.data_completeness * 14).toFixed(0)} of 14</span>
          </div>
          {confidence.reason && (
            <p className="text-gray-400 pt-1 border-t border-gray-200 dark:border-gray-700">
              {confidence.reason}
            </p>
          )}
        </div>
      </div>
    )}
  </div>
)}
{/* Rejected forecast message */}
{confidence && confidence.badge === "Rejected" && (
  <div className="mt-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200
    dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-400">
    Forecast unavailable — insufficient model confidence.
    {confidence.reason && <span className="block mt-1 text-xs">{confidence.reason}</span>}
  </div>
)}
```

Add state at the top of the component:

```tsx
const [showConfidence, setShowConfidence] = useState(false);
```

- [ ] **Step 3: Parse confidence from API response**

In the data fetching hook or component, parse `confidence_score` and `confidence_components` from the forecast run API response:

```typescript
const confidence: ForecastConfidence | null = run?.confidence_components
  ? {
      score: run.confidence_score ?? 0,
      ...JSON.parse(run.confidence_components),
    }
  : null;
```

- [ ] **Step 4: Test in browser**

Run: `cd frontend && npm run dev`
Navigate to Analysis → Forecast tab. Verify:
- Confidence badge appears next to sentiment pill
- Click expands explanation card
- Colors match badge level (green/yellow/red)
- Rejected forecasts show message instead of chart

- [ ] **Step 5: Commit**

```bash
git add frontend/components/charts/ForecastChart.tsx frontend/lib/types.ts
git commit -m "feat(ui): add confidence badge with expandable explanation on forecast chart

Shows High (green), Medium (yellow), Low (red) confidence badge.
Click expands card with directional accuracy, MASE, coverage, and
data signal count. Rejected forecasts show unavailable message.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 12: Update API to Return Confidence Data

**Files:**
- Modify: `backend/insights_routes.py` or `backend/routes.py`

- [ ] **Step 1: Include confidence fields in forecast API response**

Find the forecast endpoint that returns forecast run data. Add `confidence_score` and `confidence_components` to the response model:

```python
    # In the forecast run response builder
    result["confidence_score"] = run.get("confidence_score")
    result["confidence_components"] = run.get("confidence_components")
```

Ensure `confidence_components` is parsed from JSON string to dict before returning:

```python
    if isinstance(result.get("confidence_components"), str):
        try:
            result["confidence_components"] = json.loads(
                result["confidence_components"]
            )
        except (json.JSONDecodeError, TypeError):
            result["confidence_components"] = None
```

- [ ] **Step 2: Test API response**

Run: `curl -s http://localhost:8181/v1/insights/forecast/TCS.NS | python3 -m json.tool | grep -A5 confidence`
Expected: `confidence_score` and `confidence_components` present in response

- [ ] **Step 3: Commit**

```bash
git add backend/insights_routes.py
git commit -m "feat(api): return confidence score + components in forecast endpoint

Parses confidence_components JSON string from Iceberg and includes
confidence_score float in forecast run API responses.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 13: Integration Test — End-to-End Forecast with Enrichment

**Files:**
- Create: `tests/backend/test_forecast_enrichment_e2e.py`

- [ ] **Step 1: Write integration test**

```python
# tests/backend/test_forecast_enrichment_e2e.py
"""End-to-end test for enriched forecast pipeline."""
import json
import time
import pytest
import pandas as pd
import numpy as np

from backend.tools._forecast_regime import classify_regime, RegimeConfig
from backend.tools._forecast_features import (
    compute_tier1_features,
    compute_tier2_features,
    build_future_features,
)
from backend.tools._forecast_accuracy import (
    compute_confidence_score,
    confidence_badge,
)
from backend.tools._forecast_regime import apply_technical_bias


class TestEnrichedForecastE2E:
    """Simulate full forecast flow without Prophet (unit-level)."""

    def test_stable_ticker_full_flow(self):
        """TCS-like stable stock gets linear Prophet, no log-transform."""
        analysis = {
            "annualized_volatility": 22.0,
            "bull_phase_pct": 55.0,
            "bear_phase_pct": 35.0,
            "support_level": 3500.0,
            "resistance_level": 4200.0,
            "rsi_14": 55.0,
            "macd": 0.5, "macd_signal_line": 0.5,
        }
        cfg = classify_regime("TCS.NS", analysis)
        assert cfg.regime == "stable"
        assert cfg.log_transform is False

        tier1 = compute_tier1_features(
            analysis, {"f_score": 8}, None, 3800.0
        )
        assert tier1["piotroski"] > 0.8

        tier2 = compute_tier2_features(
            self._make_ohlcv(60), None, None
        )
        assert "volume_anomaly" in tier2

        # No bias applied (RSI neutral)
        fc = self._make_forecast(90, 3800.0)
        adjusted, meta = apply_technical_bias(fc, analysis)
        assert meta["total_bias"] == 0.0

        # Confidence: good metrics
        score, comp = compute_confidence_score(
            {"directional_accuracy_pct": 65, "MAPE_pct": 12,
             "coverage": 0.78, "interval_width_ratio": 0.2},
            data_completeness=0.85,
        )
        badge, _ = confidence_badge(score, comp)
        assert badge in ("High", "Medium")

    def test_volatile_ticker_full_flow(self):
        """TANLA-like volatile stock gets logistic, log-transform."""
        analysis = {
            "annualized_volatility": 85.0,
            "bull_phase_pct": 30.0,
            "bear_phase_pct": 60.0,
            "support_level": 300.0,
            "resistance_level": 600.0,
            "rsi_14": 22.0,  # oversold
            "macd": 0.8, "macd_signal_line": 0.2,
        }
        cfg = classify_regime("TANLA.NS", analysis)
        assert cfg.regime == "volatile"
        assert cfg.growth == "logistic"
        assert cfg.log_transform is True

        # Bias: oversold + bullish MACD = bullish dampening
        fc = self._make_forecast(90, 475.0)
        fc["yhat"] = 300.0  # bearish forecast
        adjusted, meta = apply_technical_bias(fc, analysis)
        assert meta["total_bias"] > 0  # dampens bearish
        assert adjusted["yhat"].iloc[0] > 300.0

    def test_future_features_have_calendar_variation(self):
        last = {
            "volatility_regime": 0.5,
            "piotroski": 0.6,
            "day_of_week": 0.5,
            "month_of_year": 0.25,
        }
        future = pd.date_range("2026-01-01", periods=180, freq="B")
        df = build_future_features(last, future)
        assert df["day_of_week"].nunique() == 5
        assert df["month_of_year"].nunique() >= 6
        assert (df["piotroski"] == 0.6).all()

    def _make_ohlcv(self, n):
        dates = pd.date_range("2025-06-01", periods=n, freq="B")
        return pd.DataFrame({
            "date": dates,
            "close": np.random.uniform(95, 105, n),
            "volume": np.random.randint(100000, 500000, n),
            "high": np.random.uniform(100, 110, n),
            "low": np.random.uniform(85, 95, n),
        })

    def _make_forecast(self, n, base):
        dates = pd.date_range("2026-01-01", periods=n, freq="B")
        return pd.DataFrame({
            "ds": dates,
            "yhat": [base * 1.1] * n,
            "yhat_lower": [base * 0.95] * n,
            "yhat_upper": [base * 1.25] * n,
        })
```

- [ ] **Step 2: Run integration tests**

Run: `python -m pytest tests/backend/test_forecast_enrichment_e2e.py -v`
Expected: All 3 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/backend/test_forecast_enrichment_e2e.py
git commit -m "test(forecast): add end-to-end integration tests for enriched pipeline

Tests stable and volatile ticker flows through regime classification,
feature computation, technical bias, and confidence scoring. Validates
calendar feature variation in future DataFrame.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 14: Update Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `PROGRESS.md`
- Modify: `docs/dev/changelog.md`

- [ ] **Step 1: Update CLAUDE.md with forecast enrichment notes**

Add to the Gotchas section:

```markdown
- **Forecast regime classification**: Tickers classified by annualized
  volatility into stable (<30%), moderate (30-60%), volatile (>60%).
  Each regime gets different Prophet config (growth, cps, log-transform).
- **Log-transform**: Applied for moderate/volatile regimes. Guarantees
  non-negative predictions. `np.log(y)` before fit, `np.exp(yhat)` after.
- **Technical bias**: RSI/MACD/volume signals dampen forecast by up to
  ±15%, tapering over 30 days. Does NOT change model — post-processing.
- **Confidence score**: 5-component weighted score (direction, MASE,
  coverage, interval, data completeness). <0.25 = rejected (hidden).
```

- [ ] **Step 2: Update PROGRESS.md with Sprint 7 entry**

- [ ] **Step 3: Update changelog**

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md PROGRESS.md docs/dev/changelog.md
git commit -m "docs: update CLAUDE.md, PROGRESS, changelog for forecast enrichment

Add regime classification, log-transform, technical bias, and
confidence score documentation.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```
