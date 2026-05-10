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
    | "regime";
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
];

export const STRATEGY_FEATURE_KEY_SET: Set<string> = new Set(
  STRATEGY_FEATURES.map((f) => f.key),
);

export const STRATEGY_FEATURE_BY_KEY: Record<string, StrategyFeature> =
  Object.fromEntries(STRATEGY_FEATURES.map((f) => [f.key, f]));
