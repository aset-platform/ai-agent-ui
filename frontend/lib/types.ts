/**
 * TypeScript interfaces mirroring the backend Pydantic
 * dashboard and audit response models.
 *
 * Kept in sync with ``backend/dashboard_models.py``.
 */

// ---------------------------------------------------------------
// Watchlist
// ---------------------------------------------------------------

export interface TickerPrice {
  ticker: string;
  company_name: string | null;
  current_price: number;
  previous_close: number;
  change: number;
  change_pct: number;
  currency: string;
  market: string;
  sparkline: number[];
}

export interface WatchlistResponse {
  tickers: TickerPrice[];
  portfolio_value: number | null;
  daily_change: number | null;
  daily_change_pct: number | null;
}

// ---------------------------------------------------------------
// Forecasts
// ---------------------------------------------------------------

export interface ForecastTarget {
  horizon_months: number;
  target_date: string;
  target_price: number;
  pct_change: number;
  lower_bound: number;
  upper_bound: number;
}

export interface TickerForecast {
  ticker: string;
  run_date: string;
  current_price: number;
  sentiment: string | null;
  targets: ForecastTarget[];
  mae: number | null;
  rmse: number | null;
  mape: number | null;
}

export interface ForecastsResponse {
  forecasts: TickerForecast[];
}

// ---------------------------------------------------------------
// Analysis Signals
// ---------------------------------------------------------------

export interface SignalInfo {
  name: string;
  value: string;
  signal: string;
  description: string;
}

export interface TickerAnalysis {
  ticker: string;
  analysis_date: string;
  signals: SignalInfo[];
  sharpe_ratio: number | null;
  annualized_return_pct: number | null;
  annualized_volatility_pct: number | null;
  max_drawdown_pct: number | null;
}

export interface AnalysisResponse {
  analyses: TickerAnalysis[];
}

// ---------------------------------------------------------------
// LLM Usage
// ---------------------------------------------------------------

export interface ModelUsage {
  model: string;
  provider: string;
  request_count: number;
  total_tokens: number;
  estimated_cost_usd: number;
}

export interface DailyTrend {
  date: string;
  requests: number;
  cost: number;
}

export interface LLMUsageResponse {
  total_requests: number;
  total_cost_usd: number;
  avg_latency_ms: number | null;
  models: ModelUsage[];
  daily_trend: DailyTrend[];
}

// ---------------------------------------------------------------
// Ticker Registry
// ---------------------------------------------------------------

export interface RegistryTicker {
  ticker: string;
  company_name: string | null;
  market: string;
  currency: string;
  current_price: number | null;
  last_fetch_date: string | null;
}

export interface RegistryResponse {
  tickers: RegistryTicker[];
}

// ---------------------------------------------------------------
// Chat Audit
// ---------------------------------------------------------------

export interface ChatMessage {
  role: string;
  content: string;
  timestamp: string;
  agent_id: string | null;
}

export interface ChatSessionSummary {
  session_id: string;
  started_at: string;
  ended_at: string;
  message_count: number;
  preview: string;
  agent_ids_used: string[];
}

export interface ChatSessionDetail extends ChatSessionSummary {
  messages: ChatMessage[];
}

// ---------------------------------------------------------------
// Compare
// ---------------------------------------------------------------

export interface CompareSeriesItem {
  ticker: string;
  dates: string[];
  normalized: number[];
}

export interface CompareMetric {
  ticker: string;
  annualized_return_pct: number | null;
  annualized_volatility_pct: number | null;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
  current_price: number | null;
  currency: string;
}

export interface CompareResponse {
  tickers: string[];
  series: CompareSeriesItem[];
  correlation: number[][];
  metrics: CompareMetric[];
}

// ---------------------------------------------------------------
// OHLCV & Indicators (Analysis page)
// ---------------------------------------------------------------

export interface OHLCVPoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface OHLCVResponse {
  ticker: string;
  data: OHLCVPoint[];
}

export interface IndicatorPoint {
  date: string;
  sma_50: number | null;
  sma_200: number | null;
  ema_20: number | null;
  rsi_14: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
}

export interface IndicatorsResponse {
  ticker: string;
  data: IndicatorPoint[];
}

export interface ForecastPoint {
  date: string;
  predicted: number;
  lower: number;
  upper: number;
}

export interface ForecastSeriesResponse {
  ticker: string;
  horizon_months: number;
  data: ForecastPoint[];
}
