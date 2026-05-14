/**
 * Feature dictionary mirror — KEEP IN SYNC with
 * ``backend/algo/strategy/features.py``.
 *
 * CI gate: ``backend/algo/tests/test_feature_registry_sync.py``
 * parses this file as text and asserts the key set matches
 * ``FEATURE_KEYS``. Drift fails CI.
 */

export interface StrategyFeature {
  key: string;
  label: string;
  type: "int" | "float" | "string";
  source:
    | "ohlcv"
    | "technical"
    | "fundamentals"
    | "recommendation"
    | "forecast"
    | "regime"
    | "factor"
    | "intraday_feature_store";
}

export const STRATEGY_FEATURES: StrategyFeature[] = [
  // OHLCV
  { key: "today_ltp", label: "Today LTP", type: "float", source: "ohlcv" },
  { key: "prev_day_ltp", label: "Prev day LTP", type: "float", source: "ohlcv" },
  { key: "today_vol", label: "Today volume", type: "int", source: "ohlcv" },
  { key: "today_x_vol", label: "Today × Vol (vs avg)", type: "float", source: "ohlcv" },
  { key: "away_from_52week_high", label: "Away from 52w high (%)", type: "float", source: "ohlcv" },
  // Technical
  { key: "golden_cross_days_ago", label: "Golden cross (days ago)", type: "int", source: "technical" },
  { key: "sma_50", label: "SMA 50", type: "float", source: "technical" },
  { key: "sma_200", label: "SMA 200", type: "float", source: "technical" },
  { key: "rsi", label: "RSI (14)", type: "float", source: "technical" },
  { key: "vwap", label: "VWAP (intraday)", type: "float", source: "technical" },
  { key: "nifty_above_sma200", label: "NIFTY > SMA200 regime (1/0)", type: "int", source: "technical" },
  { key: "nifty_30d_return_pct", label: "NIFTY 30-day return %", type: "float", source: "technical" },
  { key: "today_dpc", label: "Today delivery %", type: "float", source: "technical" },
  // Fundamentals
  { key: "pscore", label: "P-Score (Piotroski)", type: "int", source: "fundamentals" },
  { key: "debt_to_eq", label: "Debt / Equity", type: "float", source: "fundamentals" },
  { key: "roce", label: "ROCE %", type: "float", source: "fundamentals" },
  { key: "sales_growth_3yrs", label: "Sales growth 3y %", type: "float", source: "fundamentals" },
  { key: "prft_growth_3yrs", label: "Profit growth 3y %", type: "float", source: "fundamentals" },
  // Recommendation
  { key: "recommendation_score", label: "Recommendation score", type: "float", source: "recommendation" },
  // Forecast
  { key: "forecast_30d_pct_change", label: "Forecast 30d % change", type: "float", source: "forecast" },
  { key: "forecast_confidence", label: "Forecast confidence", type: "float", source: "forecast" },
  // Regime + breadth + VIX (REGIME-1)
  { key: "regime_label", label: "Regime label (BULL/SIDEWAYS/BEAR)", type: "string", source: "regime" },
  { key: "stress_prob", label: "HMM stress probability", type: "float", source: "regime" },
  { key: "pct_above_50sma", label: "% above 50d SMA (breadth)", type: "float", source: "regime" },
  { key: "pct_above_200sma", label: "% above 200d SMA (breadth)", type: "float", source: "regime" },
  { key: "midcap_largecap_ratio", label: "Midcap / Largecap ratio", type: "float", source: "regime" },
  { key: "vix_close", label: "India VIX close", type: "float", source: "regime" },
  { key: "vix_sma_20", label: "India VIX 20-day SMA", type: "float", source: "regime" },
  // Factor library (REGIME-2a)
  { key: "mom_12_1", label: "Momentum 12-1 (skip-month)", type: "float", source: "factor" },
  { key: "mom_6_1", label: "Momentum 6-1 (skip-month)", type: "float", source: "factor" },
  { key: "mom_3_1", label: "Momentum 3-1 (skip-month)", type: "float", source: "factor" },
  { key: "prox_52w", label: "Proximity to 52w high", type: "float", source: "factor" },
  { key: "f_score", label: "Piotroski F-Score (factor)", type: "float", source: "factor" },
  { key: "realized_vol_60d", label: "Realized vol 60d (annualised)", type: "float", source: "factor" },
  { key: "beta_to_nifty", label: "Beta vs NIFTY (252d)", type: "float", source: "factor" },
  { key: "adx_14", label: "ADX(14)", type: "float", source: "factor" },
  { key: "sma200_slope", label: "SMA200 slope (21d)", type: "float", source: "factor" },
  { key: "distance_from_sma200", label: "Distance from SMA200", type: "float", source: "factor" },
  { key: "obv", label: "On-Balance Volume", type: "float", source: "factor" },
  { key: "volume_x_avg_20", label: "Volume x 20d avg", type: "float", source: "factor" },
  { key: "up_down_vol_ratio_20", label: "Up/Down vol ratio (20d)", type: "float", source: "factor" },
  { key: "rs_vs_nifty_3m", label: "Rel strength vs NIFTY 3m", type: "float", source: "factor" },
  { key: "rs_vs_nifty_6m", label: "Rel strength vs NIFTY 6m", type: "float", source: "factor" },
  { key: "rs_vs_sector_3m", label: "Rel strength vs sector 3m", type: "float", source: "factor" },
  // Intraday feature store (ASETPLTFRM-403 FE-2, Phase 1) – trend
  { key: "sma_20", label: "SMA 20 (intraday)", type: "float", source: "intraday_feature_store" },
  { key: "sma_100", label: "SMA 100 (intraday)", type: "float", source: "intraday_feature_store" },
  { key: "ema_20", label: "EMA 20", type: "float", source: "intraday_feature_store" },
  { key: "ema_50", label: "EMA 50", type: "float", source: "intraday_feature_store" },
  { key: "ema_20_slope_5bar", label: "EMA 20 slope (5-bar)", type: "float", source: "intraday_feature_store" },
  { key: "dist_from_vwap_pct", label: "Distance from VWAP %", type: "float", source: "intraday_feature_store" },
  { key: "golden_cross_bars_ago", label: "Golden cross (bars ago, intraday)", type: "int", source: "intraday_feature_store" },
  // Intraday – momentum
  { key: "rsi_14", label: "RSI(14)", type: "float", source: "intraday_feature_store" },
  { key: "rsi_5", label: "RSI(5)", type: "float", source: "intraday_feature_store" },
  { key: "roc_5", label: "ROC(5)", type: "float", source: "intraday_feature_store" },
  // Intraday – volatility
  { key: "atr_14", label: "ATR(14)", type: "float", source: "intraday_feature_store" },
  { key: "range_expansion", label: "Range expansion ((H-L)/ATR)", type: "float", source: "intraday_feature_store" },
  { key: "bb_width", label: "BB Width (20)", type: "float", source: "intraday_feature_store" },
  // Intraday – volume
  { key: "relative_volume", label: "Relative volume (TOD avg, 20d)", type: "float", source: "intraday_feature_store" },
  { key: "volume_spike", label: "Volume spike (>2x avg)", type: "int", source: "intraday_feature_store" },
  // Intraday – structure
  { key: "gap_pct", label: "Gap % (today open vs prev close)", type: "float", source: "intraday_feature_store" },
  { key: "orb_high_15min", label: "ORB High (15m)", type: "float", source: "intraday_feature_store" },
  { key: "orb_low_15min", label: "ORB Low (15m)", type: "float", source: "intraday_feature_store" },
  { key: "dist_from_prev_day_high_pct", label: "Distance from prev day high %", type: "float", source: "intraday_feature_store" },
  { key: "dist_from_prev_day_low_pct", label: "Distance from prev day low %", type: "float", source: "intraday_feature_store" },
  // Intraday – time
  { key: "minutes_since_open", label: "Minutes since 09:15 IST", type: "int", source: "intraday_feature_store" },
  { key: "time_of_day_bucket", label: "Time-of-day bucket", type: "string", source: "intraday_feature_store" },
];

export const STRATEGY_FEATURE_KEY_SET: Set<string> = new Set(
  STRATEGY_FEATURES.map((f) => f.key),
);

export const STRATEGY_FEATURE_BY_KEY: Record<string, StrategyFeature> =
  Object.fromEntries(STRATEGY_FEATURES.map((f) => [f.key, f]));
