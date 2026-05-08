/**
 * Filter catalog mirror — KEEP IN SYNC with
 * ``backend/advanced_analytics_filters.py``.
 *
 * CI gate: ``tests/backend/test_filter_catalog_sync.py`` parses
 * this file as text and asserts every ``key`` here is present
 * in the backend's ``TECH_KEYS`` / ``FUND_KEYS`` and vice-versa.
 * Adding / removing a key on one side without the other fails CI.
 */

export interface FilterOption {
  key: string;
  label: string;
  /** Radio group id; undefined → checkbox. */
  group?: string;
  /** Sub-section header inside the popover. */
  section: string;
  tooltip?: string;
}

export const TECH_FILTER_CATALOG: FilterOption[] = [
  {
    key: "golden_recent",
    label: "Recent (≤10d)",
    section: "Golden Cross",
    tooltip: "SMA 50 crossed above SMA 200 within the last 10 days",
  },
  {
    key: "golden_established",
    label: "Established",
    section: "Golden Cross",
    tooltip: "SMA 50 above SMA 200 for more than 10 days",
  },
  {
    key: "price_gt_sma50",
    label: "Price > SMA 50",
    section: "Trend",
  },
  {
    key: "price_gt_sma200",
    label: "Price > SMA 200",
    section: "Trend",
  },
  {
    key: "rsi_oversold",
    label: "Oversold (<30)",
    group: "rsi_band",
    section: "RSI",
  },
  {
    key: "rsi_neutral",
    label: "Neutral (30–70)",
    group: "rsi_band",
    section: "RSI",
  },
  {
    key: "rsi_overbought",
    label: "Overbought (>70)",
    group: "rsi_band",
    section: "RSI",
  },
  {
    key: "vol_surge",
    label: "Today × Vol ≥ 2",
    section: "Volume",
  },
  {
    key: "near_52w_high",
    label: "Within 5% of 52w high",
    section: "Range",
  },
];

export const FUND_FILTER_CATALOG: FilterOption[] = [
  {
    key: "fscore_ge_7",
    label: "F-Score ≥ 7",
    group: "fscore_band",
    section: "Quality",
  },
  {
    key: "fscore_le_3",
    label: "F-Score ≤ 3",
    group: "fscore_band",
    section: "Quality",
  },
  {
    key: "debt_lt_0_5",
    label: "Debt/Eq < 0.5",
    section: "Leverage",
  },
  {
    key: "roce_gt_20",
    label: "ROCE > 20%",
    section: "Profitability",
  },
  {
    key: "sales_3y_gt_15",
    label: "Sales 3y > 15%",
    section: "Growth",
  },
  {
    key: "profit_3y_gt_15",
    label: "Profit 3y > 15%",
    section: "Growth",
  },
  {
    key: "prom_hld_gt_50",
    label: "Promoter > 50%",
    section: "Promoter",
  },
  {
    key: "pledged_lt_5",
    label: "Pledged < 5%",
    section: "Promoter",
  },
];

export const TECH_KEY_SET: Set<string> = new Set(
  TECH_FILTER_CATALOG.map((o) => o.key),
);
export const FUND_KEY_SET: Set<string> = new Set(
  FUND_FILTER_CATALOG.map((o) => o.key),
);

/** Lookup a label by key across both bundles (chip rendering). */
export const FILTER_LABEL_BY_KEY: Record<string, string> = {
  ...Object.fromEntries(TECH_FILTER_CATALOG.map((o) => [o.key, o.label])),
  ...Object.fromEntries(FUND_FILTER_CATALOG.map((o) => [o.key, o.label])),
};
