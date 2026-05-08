/**
 * Advanced Analytics types — mirrors
 * `backend/advanced_analytics_models.py` AA-7.
 *
 * One superset row model serves all 7 reports — the
 * frontend column selector chooses which subset to
 * render per tab (§5.4 tabular-page-pattern).
 */

export type AdvancedReportName =
  | "current-day-upmove"
  | "previous-day-breakout"
  | "mom-volume-delivery"
  | "wow-volume-delivery"
  | "two-day-scan"
  | "three-day-scan"
  | "top-50-delivery-by-qty";

export type StaleReason =
  | "nan_close"
  | "missing_delivery"
  | "missing_quarterly"
  | "missing_promoter";

export interface StaleTicker {
  ticker: string;
  reason: StaleReason;
}

export interface AdvancedRow {
  ticker: string;
  company_name: string | null;
  sector: string | null;
  sub_sector: string | null;
  pscore: number | null;
  rsi: number | null;
  avg_emv_score: number | null;
  avg_14d_emv: number | null;
  sma_50: number | null;
  sma_200: number | null;
  /** Trading days since SMA 50 last crossed above SMA 200.
   *  null → no golden cross.  0–10 → recent (amber).
   *  11+ / 999 → established bullish (green). */
  golden_cross_days_ago: number | null;
  today_ltp: number | null;
  prev_day_ltp: number | null;
  prev_2_prev_day_ltp: number | null;
  current_ppc: number | null;
  avg_10d_ppc: number | null;
  avg_20d_ppc: number | null;
  week_52_high: number | null;
  week_52_low: number | null;
  away_from_52week_high: number | null;
  today_vol: number | null;
  prev_day_vol: number | null;
  avg_10d_vol: number | null;
  avg_20d_vol: number | null;
  today_x_vol: number | null;
  prev_day_x_vol: number | null;
  x_vol_10d: number | null;
  x_vol_20d: number | null;
  today_dv: number | null;
  prev_day_dv: number | null;
  avg_10d_dv: number | null;
  avg_20d_dv: number | null;
  today_dpc: number | null;
  prev_day_dpc: number | null;
  avg_10d_dpc: number | null;
  avg_20d_dpc: number | null;
  today_x_dv: number | null;
  prev_day_x_dv: number | null;
  x_dv_10d: number | null;
  x_dv_20d: number | null;
  current_dpc: number | null;
  today_not: number | null;
  avg_10d_not: number | null;
  avg_20d_not: number | null;
  debt_to_eq: number | null;
  yoy_qtr_prft: number | null;
  yoy_qtr_sales: number | null;
  sales_growth_3yrs: number | null;
  prft_growth_3yrs: number | null;
  sales_growth_5yrs: number | null;
  prft_growth_5yrs: number | null;
  roce: number | null;
  chng_in_prom_hld: number | null;
  pledged: number | null;
  prom_hld: number | null;
  event: string | null;
  event_date: string | null;
}

export interface AdvancedReportResponse {
  rows: AdvancedRow[];
  total: number;
  page: number;
  page_size: number;
  stale_tickers: StaleTicker[];
}

export const ADVANCED_REPORT_LABELS: Record<AdvancedReportName, string> = {
  "current-day-upmove": "Current Day Upmove",
  "previous-day-breakout": "Previous Day Breakout",
  "mom-volume-delivery": "MoM Volume / Delivery",
  "wow-volume-delivery": "WoW Volume / Delivery",
  "two-day-scan": "Two-Day Scan",
  "three-day-scan": "Three-Day Scan",
  "top-50-delivery-by-qty": "Top 50 by Delivery Qty",
};

export const ADVANCED_REPORT_ORDER: AdvancedReportName[] = [
  "current-day-upmove",
  "previous-day-breakout",
  "mom-volume-delivery",
  "wow-volume-delivery",
  "two-day-scan",
  "three-day-scan",
  "top-50-delivery-by-qty",
];

/** Tab IDs include the data reports plus the frontend-only
 *  Help tab. Keep AdvancedReportName separate — it mirrors
 *  the backend endpoint set and drives RSC pre-fetch + URL
 *  validation. */
export type AdvancedTabId = AdvancedReportName | "help";

export const ADVANCED_TAB_LABELS: Record<AdvancedTabId, string> = {
  ...ADVANCED_REPORT_LABELS,
  help: "Help",
};

export const ADVANCED_TAB_ORDER: AdvancedTabId[] = [
  ...ADVANCED_REPORT_ORDER,
  "help",
];

export type MarketFilter = "all" | "india" | "us";
export type TickerTypeFilter = "all" | "stock" | "etf";

export const MARKET_FILTER_OPTIONS: { value: MarketFilter; label: string }[] = [
  { value: "all", label: "All markets" },
  { value: "india", label: "India" },
  { value: "us", label: "US" },
];

export const TICKER_TYPE_FILTER_OPTIONS: {
  value: TickerTypeFilter;
  label: string;
}[] = [
  { value: "all", label: "Stocks + ETFs" },
  { value: "stock", label: "Stocks only" },
  { value: "etf", label: "ETFs only" },
];

// ---- Filter bundles --------------------------------------------------

export type TechFilterKey =
  | "golden_recent"
  | "golden_established"
  | "price_gt_sma50"
  | "price_gt_sma200"
  | "rsi_oversold"
  | "rsi_neutral"
  | "rsi_overbought"
  | "vol_surge"
  | "near_52w_high";

export type FundFilterKey =
  | "fscore_ge_7"
  | "fscore_le_3"
  | "debt_lt_0_5"
  | "roce_gt_20"
  | "sales_3y_gt_15"
  | "profit_3y_gt_15"
  | "prom_hld_gt_50"
  | "pledged_lt_5";

export type FilterBundleId = "tech" | "fund";

/** Hard cap mirrored from backend ``_MAX_EXPORT_ROWS``. */
export const FILTER_EXPORT_ROW_CAP = 10_000;
