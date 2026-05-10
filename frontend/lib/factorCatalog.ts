/**
 * Factor catalog — static metadata for the 21 factor keys persisted
 * in stocks.daily_factors (REGIME-2a). Used by the Factor Scores tab
 * for column-selector grouping + label rendering.
 *
 * KEEP IN SYNC with `backend/algo/factors/iceberg_init.py::ALL_FACTOR_KEYS`.
 * The CI test `test_feature_registry_sync.py` enforces that every
 * key here appears in `FEATURE_KEYS`.
 */

export type FactorCategory =
  | "momentum"
  | "quality"
  | "lowvol"
  | "trend"
  | "volume"
  | "relative_strength"
  | "breadth";

export interface FactorDef {
  key: string;
  label: string;
  category: FactorCategory;
  unit?: string;
  description: string;
}

export const FACTOR_CATALOG: FactorDef[] = [
  // Momentum (4) — skip-month convention is non-negotiable
  {
    key: "mom_12_1",
    label: "Mom 12-1",
    category: "momentum",
    description:
      "Trailing 12-month return excluding the last 21 trading days",
  },
  {
    key: "mom_6_1",
    label: "Mom 6-1",
    category: "momentum",
    description:
      "Trailing 6-month return excluding the last 21 trading days",
  },
  {
    key: "mom_3_1",
    label: "Mom 3-1",
    category: "momentum",
    description:
      "Trailing 3-month return excluding the last 21 trading days",
  },
  {
    key: "prox_52w",
    label: "52w Proximity",
    category: "momentum",
    unit: "ratio",
    description: "Close / max(close[t-252:t])",
  },

  // Quality
  {
    key: "f_score",
    label: "Piotroski F",
    category: "quality",
    unit: "0-9",
    description: "Forward-filled Piotroski 9-factor score",
  },

  // Low-volatility
  {
    key: "realized_vol_60d",
    label: "Real Vol 60d",
    category: "lowvol",
    unit: "ann.",
    description: "Annualised stdev of 60d log-returns",
  },
  {
    key: "beta_to_nifty",
    label: "Beta NIFTY",
    category: "lowvol",
    description: "OLS slope vs NIFTY 50, 252-day window",
  },

  // Trend
  {
    key: "adx_14",
    label: "ADX(14)",
    category: "trend",
    unit: "0-100",
    description: "Average Directional Index, 14-period",
  },
  {
    key: "sma200_slope",
    label: "SMA200 Slope",
    category: "trend",
    unit: "21d %",
    description: "(SMA200[t] - SMA200[t-21]) / SMA200[t-21]",
  },
  {
    key: "distance_from_sma200",
    label: "Dist SMA200",
    category: "trend",
    unit: "ratio",
    description: "(close - SMA200) / SMA200",
  },

  // Volume
  {
    key: "obv",
    label: "OBV",
    category: "volume",
    description: "On-balance volume cumulative",
  },
  {
    key: "volume_x_avg_20",
    label: "Vol × 20d Avg",
    category: "volume",
    description: "Today volume / 20-day average",
  },
  {
    key: "up_down_vol_ratio_20",
    label: "Up/Dn Vol 20d",
    category: "volume",
    description: "Sum(green-day vol) / sum(red-day vol) over 20d",
  },

  // Relative strength
  {
    key: "rs_vs_nifty_3m",
    label: "RS NIFTY 3m",
    category: "relative_strength",
    description:
      "(stock[t]/stock[t-63]) / (nifty[t]/nifty[t-63])",
  },
  {
    key: "rs_vs_nifty_6m",
    label: "RS NIFTY 6m",
    category: "relative_strength",
    description:
      "(stock[t]/stock[t-126]) / (nifty[t]/nifty[t-126])",
  },
  {
    key: "rs_vs_sector_3m",
    label: "RS Sector 3m",
    category: "relative_strength",
    description:
      "(stock[t]/stock[t-63]) / (sector[t]/sector[t-63])",
  },

  // Breadth (universe-level)
  {
    key: "pct_above_50sma",
    label: "% > 50SMA",
    category: "breadth",
    unit: "%",
    description: "Universe-wide breadth: % of stocks above 50d SMA",
  },
  {
    key: "pct_above_200sma",
    label: "% > 200SMA",
    category: "breadth",
    unit: "%",
    description: "Universe-wide breadth: % of stocks above 200d SMA",
  },
  {
    key: "midcap_largecap_ratio",
    label: "Mid/Large",
    category: "breadth",
    description: "NIFMDCP150 / NIFTY50 close ratio",
  },
];

export const FACTOR_KEYS: string[] = FACTOR_CATALOG.map((f) => f.key);

export const FACTOR_BY_KEY: Record<string, FactorDef> =
  Object.fromEntries(FACTOR_CATALOG.map((f) => [f.key, f]));

export const DEFAULT_VISIBLE_FACTORS: string[] = [
  "mom_12_1",
  "f_score",
  "realized_vol_60d",
  "adx_14",
  "rs_vs_nifty_3m",
];
