"""Feature dictionary registry — single source of truth for the
leaf vocabulary the strategy AST can reference. Mirrors are
generated for the frontend at
``frontend/components/algo-trading/strategyFeatureCatalog.ts``;
drift caught by ``test_feature_registry_sync.py``.

Each feature has a stable key, a UI label, a numeric type
(int / float), and a source identifier — used by the
backtest engine in Slice 7 to know which Iceberg/PG table
to pull the value from. Adding a feature is a 4-step PR:
1. Add to FEATURES below.
2. Add the matching entry to strategyFeatureCatalog.ts.
3. Implement the resolver in Slice 7's runtime.
4. Add a sample backtest case if the feature is non-obvious.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

FeatureType = Literal["int", "float", "string"]
FeatureSource = Literal[
    "ohlcv",
    # rsi, sma_50, sma_200, golden_cross_days_ago
    "technical",
    "fundamentals",  # pscore, debt_to_eq, roce, sales/profit
    "recommendation",
    "forecast",
    "regime",  # REGIME-1: regime_label, stress_prob, etc.
    "factor",  # REGIME-2a: cached factor library
    # ASETPLTFRM-403 FE-2: centralized intraday feature engine
    # (``stocks.intraday_features`` Iceberg store, populated by
    # FE-3 daily compute / on-demand backfill). Read at backtest
    # time by FE-4; written by live runtime by FE-10.
    "intraday_feature_store",
]


class Feature(BaseModel):
    """A single feature in the strategy vocabulary."""

    key: str
    label: str
    type: FeatureType
    source: FeatureSource


# Initial feature dictionary — equity, daily-bar features only.
# Slice 6 adds intraday-bar features. F&O features = v2.
FEATURES: list[Feature] = [
    # OHLCV
    Feature(key="today_ltp", label="Today LTP", type="float", source="ohlcv"),
    Feature(
        key="prev_day_ltp",
        label="Prev day LTP",
        type="float",
        source="ohlcv",
    ),
    Feature(key="today_vol", label="Today volume", type="int", source="ohlcv"),
    Feature(
        key="today_x_vol",
        label="Today × Vol (vs avg)",
        type="float",
        source="ohlcv",
    ),
    Feature(
        key="away_from_52week_high",
        label="Away from 52w high (%)",
        type="float",
        source="ohlcv",
    ),
    # Technical
    Feature(
        key="golden_cross_days_ago",
        label="Golden cross (days ago)",
        type="int",
        source="technical",
    ),
    Feature(key="sma_50", label="SMA 50", type="float", source="technical"),
    Feature(key="sma_200", label="SMA 200", type="float", source="technical"),
    Feature(key="rsi", label="RSI (14)", type="float", source="technical"),
    Feature(
        key="vwap",
        label="VWAP (intraday)",
        type="float",
        source="technical",
    ),
    Feature(
        key="nifty_above_sma200",
        label="NIFTY > SMA200 regime (1/0)",
        type="int",
        source="technical",
    ),
    Feature(
        key="nifty_30d_return_pct",
        label="NIFTY 30-day return %",
        type="float",
        source="technical",
    ),
    Feature(
        key="today_dpc",
        label="Today delivery %",
        type="float",
        source="technical",
    ),
    # Fundamentals
    Feature(
        key="pscore",
        label="P-Score (Piotroski)",
        type="int",
        source="fundamentals",
    ),
    Feature(
        key="debt_to_eq",
        label="Debt / Equity",
        type="float",
        source="fundamentals",
    ),
    Feature(key="roce", label="ROCE %", type="float", source="fundamentals"),
    Feature(
        key="sales_growth_3yrs",
        label="Sales growth 3y %",
        type="float",
        source="fundamentals",
    ),
    Feature(
        key="prft_growth_3yrs",
        label="Profit growth 3y %",
        type="float",
        source="fundamentals",
    ),
    # Recommendation
    Feature(
        key="recommendation_score",
        label="Recommendation score",
        type="float",
        source="recommendation",
    ),
    # Forecast
    Feature(
        key="forecast_30d_pct_change",
        label="Forecast 30d % change",
        type="float",
        source="forecast",
    ),
    Feature(
        key="forecast_confidence",
        label="Forecast confidence",
        type="float",
        source="forecast",
    ),
    # Regime + breadth + VIX (REGIME-1)
    Feature(
        key="regime_label",
        label="Regime label (BULL/SIDEWAYS/BEAR)",
        type="string",
        source="regime",
    ),
    Feature(
        key="stress_prob",
        label="HMM stress probability",
        type="float",
        source="regime",
    ),
    Feature(
        key="pct_above_50sma",
        label="% above 50d SMA (breadth)",
        type="float",
        source="regime",
    ),
    Feature(
        key="pct_above_200sma",
        label="% above 200d SMA (breadth)",
        type="float",
        source="regime",
    ),
    Feature(
        key="midcap_largecap_ratio",
        label="Midcap / Largecap ratio",
        type="float",
        source="regime",
    ),
    Feature(
        key="vix_close",
        label="India VIX close",
        type="float",
        source="regime",
    ),
    Feature(
        key="vix_sma_20",
        label="India VIX 20-day SMA",
        type="float",
        source="regime",
    ),
    # Factor library (REGIME-2a) — pre-computed nightly to
    # ``stocks.daily_factors`` and overlaid into the per-bar
    # features dict by all 3 runtimes.
    Feature(
        key="mom_12_1",
        label="Momentum 12-1 (skip-month)",
        type="float",
        source="factor",
    ),
    Feature(
        key="mom_6_1",
        label="Momentum 6-1 (skip-month)",
        type="float",
        source="factor",
    ),
    Feature(
        key="mom_3_1",
        label="Momentum 3-1 (skip-month)",
        type="float",
        source="factor",
    ),
    Feature(
        key="prox_52w",
        label="Proximity to 52w high",
        type="float",
        source="factor",
    ),
    Feature(
        key="f_score",
        label="Piotroski F-Score (factor)",
        type="float",
        source="factor",
    ),
    Feature(
        key="realized_vol_60d",
        label="Realized vol 60d (annualised)",
        type="float",
        source="factor",
    ),
    Feature(
        key="beta_to_nifty",
        label="Beta vs NIFTY (252d)",
        type="float",
        source="factor",
    ),
    Feature(
        key="adx_14",
        label="ADX(14)",
        type="float",
        source="factor",
    ),
    Feature(
        key="sma200_slope",
        label="SMA200 slope (21d)",
        type="float",
        source="factor",
    ),
    Feature(
        key="distance_from_sma200",
        label="Distance from SMA200",
        type="float",
        source="factor",
    ),
    Feature(
        key="obv",
        label="On-Balance Volume",
        type="float",
        source="factor",
    ),
    Feature(
        key="volume_x_avg_20",
        label="Volume x 20d avg",
        type="float",
        source="factor",
    ),
    Feature(
        key="up_down_vol_ratio_20",
        label="Up/Down vol ratio (20d)",
        type="float",
        source="factor",
    ),
    Feature(
        key="rs_vs_nifty_3m",
        label="Rel strength vs NIFTY 3m",
        type="float",
        source="factor",
    ),
    Feature(
        key="rs_vs_nifty_6m",
        label="Rel strength vs NIFTY 6m",
        type="float",
        source="factor",
    ),
    Feature(
        key="rs_vs_sector_3m",
        label="Rel strength vs sector 3m",
        type="float",
        source="factor",
    ),
    # ────────────────────────────────────────────────────────────
    # Intraday feature store (ASETPLTFRM-403 FE-2, Phase 1)
    # Populated by the centralized engine at
    # ``backend.algo.features``; persisted to
    # ``stocks.intraday_features`` (Iceberg) by FE-3; read by
    # the backtest runner in FE-4. RS-vs-* features defer to
    # FE-8 because they depend on FE-6 / FE-7 index + sector
    # intraday bar tables.
    # ────────────────────────────────────────────────────────────
    # Intraday — trend
    Feature(
        key="sma_20",
        label="SMA 20 (intraday)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="sma_100",
        label="SMA 100 (intraday)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="ema_20",
        label="EMA 20",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="ema_50",
        label="EMA 50",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="ema_20_slope_5bar",
        label="EMA 20 slope (5-bar)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="dist_from_vwap_pct",
        label="Distance from VWAP %",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="golden_cross_bars_ago",
        label="Golden cross (bars ago, intraday)",
        type="int",
        source="intraday_feature_store",
    ),
    # Intraday — momentum
    Feature(
        key="rsi_14",
        label="RSI(14)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="rsi_5",
        label="RSI(5)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="roc_5",
        label="ROC(5)",
        type="float",
        source="intraday_feature_store",
    ),
    # Intraday — volatility
    Feature(
        key="atr_14",
        label="ATR(14)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="range_expansion",
        label="Range expansion ((H-L)/ATR)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="bb_width",
        label="BB Width (20)",
        type="float",
        source="intraday_feature_store",
    ),
    # Intraday — volume
    Feature(
        key="relative_volume",
        label="Relative volume (TOD avg, 20d)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="volume_spike",
        label="Volume spike (>2x avg)",
        type="int",
        source="intraday_feature_store",
    ),
    # Intraday — structure
    Feature(
        key="gap_pct",
        label="Gap % (today open vs prev close)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="orb_high_15min",
        label="ORB High (15m)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="orb_low_15min",
        label="ORB Low (15m)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="dist_from_prev_day_high_pct",
        label="Distance from prev day high %",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="dist_from_prev_day_low_pct",
        label="Distance from prev day low %",
        type="float",
        source="intraday_feature_store",
    ),
    # Intraday — time
    Feature(
        key="minutes_since_open",
        label="Minutes since 09:15 IST",
        type="int",
        source="intraday_feature_store",
    ),
    Feature(
        key="time_of_day_bucket",
        label="Time-of-day bucket",
        type="string",
        source="intraday_feature_store",
    ),
    # ────────────────────────────────────────────────────────────
    # Intraday — relative-strength + market-breadth (FE-8)
    # Phase-2 cross-sectional features. The two rs_vs_* features
    # were deferred from FE-2 because they depend on the FE-6
    # ``stocks.index_intraday_bars`` table. The two market-breadth
    # features are introduced fresh here as the first Phase-2
    # cohort-pass features.
    # ────────────────────────────────────────────────────────────
    Feature(
        key="rs_vs_nifty_15m",
        label="RS vs NIFTY (15m)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="rs_vs_sector_15m",
        label="RS vs sector (15m)",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="market_breadth_pct_above_sma200",
        label="% Nifty-500 above SMA200",
        type="float",
        source="intraday_feature_store",
    ),
    Feature(
        key="advance_decline_ratio",
        label="Advance/decline ratio (15m)",
        type="float",
        source="intraday_feature_store",
    ),
    # ────────────────────────────────────────────────────────────
    # Intraday — sector rotation + regime link (FE-9, Phase 2)
    # ``sector_rotation_score`` is a NEW Phase-2 cross-sectional
    # feature ranking the 8 NSE sectoral indices by 15m return.
    # Emitted only for tickers whose sector maps into the
    # rotation universe.
    #
    # NOTE: ``regime_label`` + ``stress_prob`` are intentionally
    # NOT duplicated here under ``intraday_feature_store``. The
    # daily-cadence ``source="regime"`` entries above (REGIME-1)
    # remain the canonical AST surface — strategies reference
    # them by the same key regardless of cadence. FE-9 just
    # makes the intraday compute job ALSO emit those two values
    # into ``stocks.intraday_features`` so FE-4 backtest reads
    # see them on intraday bars. Both daily-runner and intraday
    # paths converge on the same key.
    # ────────────────────────────────────────────────────────────
    Feature(
        key="sector_rotation_score",
        label="Sector rotation score (15m)",
        type="float",
        source="intraday_feature_store",
    ),
]


FEATURE_KEYS: frozenset[str] = frozenset(f.key for f in FEATURES)
FEATURE_BY_KEY: dict[str, Feature] = {f.key: f for f in FEATURES}
