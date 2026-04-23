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
  confidence_score: number | null;
  confidence_components: ForecastConfidence | null;
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
  change: number | null;
  change_pct: number | null;
  sparkline: number[];
  last_fetch_date: string | null;
}

export interface RegistryResponse {
  tickers: RegistryTicker[];
}

// ---------------------------------------------------------------
// Dashboard Aggregate
// ---------------------------------------------------------------

export interface DashboardHomeResponse {
  watchlist: WatchlistResponse;
  forecasts: ForecastsResponse;
  analysis: AnalysisResponse;
  llm_usage: LLMUsageResponse;
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
  rsi_14: number | null;
  macd_signal: string | null;
  sentiment: string | null;
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

// ---------------------------------------------------------------
// Portfolio Performance & Forecast
// ---------------------------------------------------------------

export interface PortfolioDailyPoint {
  date: string;
  value: number;
  invested_value: number;
  daily_pnl: number;
  daily_return_pct: number;
}

export interface PortfolioMetrics {
  total_return_pct: number;
  annualized_return_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number | null;
  best_day_pct: number;
  best_day_date: string;
  worst_day_pct: number;
  worst_day_date: string;
}

export interface StalePriceTicker {
  ticker: string;
  last_valid_close_date: string;
  days_stale: number;
}

export interface PortfolioPerformanceResponse {
  data: PortfolioDailyPoint[];
  metrics: PortfolioMetrics | null;
  currency: string;
  stale_tickers: StalePriceTicker[];
}

export interface PortfolioForecastPoint {
  date: string;
  predicted: number;
  lower: number;
  upper: number;
}

export interface PortfolioForecastResponse {
  data: PortfolioForecastPoint[];
  horizon_months: number;
  current_value: number;
  total_invested: number;
  currency: string;
}

// ---------------------------------------------------------------
// Insights
// ---------------------------------------------------------------

export interface ScreenerRow {
  // Identity
  ticker: string;
  company_name: string | null;
  sector: string | null;
  industry: string | null;
  market: string;
  currency: string | null;
  tags: string[];

  // Pricing
  price: number | null;
  current_price: number | null;
  week_52_high: number | null;
  week_52_low: number | null;

  // Valuation
  market_cap: number | null;
  pe_ratio: number | null;
  price_to_book: number | null;
  dividend_yield: number | null;
  peg_ratio: number | null;
  peg_ratio_yf: number | null;
  peg_ratio_ttm: number | null;

  // Profitability
  profit_margins: number | null;
  earnings_growth: number | null;
  revenue_growth: number | null;
  eps: number | null;
  revenue: number | null;
  net_income: number | null;

  // Risk
  annualized_return_pct: number | null;
  annualized_volatility_pct: number | null;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
  beta: number | null;

  // Technical
  rsi_14: number | null;
  rsi_signal: string | null;
  macd_signal: string | null;
  sma_200_signal: string | null;
  sentiment_score: number | null;
  sentiment_headlines: number | null;

  // Quality
  piotroski_score: number | null;
  piotroski_label: string | null;
  forecast_confidence: number | null;

  // Forecast
  target_3m_pct: number | null;
  target_6m_pct: number | null;
  target_9m_pct: number | null;

  action?: string;
}

export interface ScreenerResponse {
  rows: ScreenerRow[];
  sectors: string[];
  tags: string[];
}

export interface TargetRow {
  ticker: string;
  horizon_months: number | null;
  run_date: string | null;
  current_price: number | null;
  target_3m_price: number | null;
  target_3m_pct: number | null;
  target_6m_price: number | null;
  target_6m_pct: number | null;
  target_9m_price: number | null;
  target_9m_pct: number | null;
  sentiment: string | null;
  market: string;
  sector: string | null;
}

export interface TargetsResponse {
  rows: TargetRow[];
  tickers: string[];
  sectors: string[];
}

export interface DividendRow {
  ticker: string;
  ex_date: string | null;
  amount: number | null;
  currency: string;
  market: string;
  sector: string | null;
}

export interface DividendsResponse {
  rows: DividendRow[];
  tickers: string[];
  sectors: string[];
}

export interface RiskRow {
  ticker: string;
  annualized_return_pct: number | null;
  annualized_volatility_pct: number | null;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
  max_drawdown_days: number | null;
  bull_phase_pct: number | null;
  bear_phase_pct: number | null;
  market: string;
  sector: string | null;
  action?: string;
}

export interface RiskResponse {
  rows: RiskRow[];
  sectors: string[];
}

export interface SectorRow {
  sector: string;
  stock_count: number;
  avg_return_pct: number | null;
  avg_sharpe: number | null;
  avg_volatility_pct: number | null;
}

export interface SectorsResponse {
  rows: SectorRow[];
}

export interface CorrelationResponse {
  tickers: string[];
  matrix: number[][];
  period: string;
}

export interface QuarterlyRow {
  ticker: string;
  quarter_label: string | null;
  quarter_end: string | null;
  statement_type: string | null;
  revenue: number | null;
  net_income: number | null;
  eps: number | null;
  total_assets: number | null;
  total_equity: number | null;
  operating_cashflow: number | null;
  free_cashflow: number | null;
  market: string;
  sector: string | null;
}

export interface QuarterlyResponse {
  rows: QuarterlyRow[];
  tickers: string[];
  sectors: string[];
}

// ---------------------------------------------------------------
// Admin — Users & Audit
// ---------------------------------------------------------------

export interface UserResponse {
  user_id: string;
  email: string;
  full_name: string;
  role: "superuser" | "general";
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
  last_login_at: string | null;
  avatar_url: string | null;
  page_permissions: Record<string, boolean> | null;
}

export interface AuditEvent {
  event_timestamp: string;
  event_type: string;
  actor_user_id: string;
  target_user_id: string | null;
  metadata: string | Record<string, unknown>;
}

// ---------------------------------------------------------------
// Admin — LLM Observability
// ---------------------------------------------------------------

export interface ModelBudget {
  tpm: string;
  rpm: string;
  tpd: string;
  rpd: string;
}

export interface CascadeEvent {
  timestamp: number;
  from_model: string;
  to_model: string;
  reason: string;
}

export interface CascadeStats {
  requests_total: number;
  requests_by_model: Record<string, number>;
  cascade_count: number;
  compression_count: number;
  cascade_log: CascadeEvent[];
  rpm_by_model: Record<string, number>;
  prompt_tokens_by_model: Record<string, number>;
  completion_tokens_by_model: Record<
    string,
    number
  >;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
}

export interface MetricsResponse {
  timestamp: number;
  scope?: "self" | "all";
  // scope="all" → cascade rate bars ({tpm, rpm, tpd, rpd}).
  // scope="self" → per-user usage rollup (see UserModelUsage).
  // Callers branch on ``scope`` before reading fields.
  models: Record<string, ModelBudget>;
  cascade_stats: CascadeStats;
  // Present only when scope === "self".
  quota?: BYOQuota;
  providers?: BYOProviderStatus[];
  daily_trend?: DailyTrendPoint[];
}

export interface UserModelUsage {
  requests: number;
  requests_platform: number;
  requests_user: number;
  cost: number;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  last_used_at: string | null;
}

export interface BYOQuota {
  free_allowance_total: number;
  free_allowance_used: number;
  byo_monthly_limit: number;
  byo_month_used: number;
}

export interface BYOProviderStatus {
  provider: "groq" | "anthropic" | "ollama";
  configured: boolean;
  label?: string | null;
  masked_key?: string | null;
  last_used_at?: string | null;
  request_count_30d?: number;
  native?: boolean;
}

export interface DailyTrendPoint {
  date: string;
  requests: number;
  cost: number;
}

export interface UserLLMKey {
  provider: "groq" | "anthropic";
  label: string | null;
  masked_key: string;
  last_used_at: string | null;
  request_count_30d: number;
  created_at: string;
  updated_at: string;
}

export interface TierHealth {
  model: string;
  status: "healthy" | "degraded" | "down" | "disabled";
  failures_5m: number;
  successes_5m: number;
  cascade_count: number;
  latency: {
    avg_ms: number;
    p95_ms: number;
  };
}

export interface HealthSummary {
  total: number;
  healthy: number;
  degraded: number;
  down: number;
  disabled: number;
}

// ---------------------------------------------------------------
// Portfolio Analytics (Sprint 6 — W1–W5)
// ---------------------------------------------------------------

export interface AllocationItem {
  sector: string;
  value: number;
  weight_pct: number;
  stock_count: number;
  tickers: string[];
}

export interface AllocationResponse {
  sectors: AllocationItem[];
  total_value: number;
  currency: string;
}

export interface NewsHeadline {
  title: string;
  url: string;
  source: string;
  published_at: string;
  ticker: string | null;
  sentiment: number;
}

export interface PortfolioNewsResponse {
  headlines: NewsHeadline[];
  portfolio_sentiment: number;
  portfolio_sentiment_label: string;
  market_sentiment: number;
  market_sentiment_label: string;
  unanalyzed_tickers: string[];
}

export interface Recommendation {
  type: string;
  severity: string;
  title: string;
  description: string;
  ticker: string | null;
  metric_value: number;
  threshold: number;
}

export interface RecommendationsResponse {
  recommendations: Recommendation[];
  portfolio_health: string;
}

// ---------------------------------------------------------------
// Forecast Confidence
// ---------------------------------------------------------------

export interface ForecastConfidence {
  score: number;
  badge: "High" | "Medium" | "Low" | "Rejected";
  reason: string;
  direction: number;
  mase: number;
  coverage: number;
  interval: number;
  data_completeness: number;
  regime: string;
}

// ---------------------------------------------------------------
// Forecast Backtest Overlay
// ---------------------------------------------------------------

export interface BacktestPoint {
  date: string;
  predicted: number;
  actual: number;
}

export interface BacktestAccuracy {
  directional_accuracy_pct: number;
  max_error_pct: number;
  p50_error_pct: number;
  p90_error_pct: number;
}

export interface ForecastBacktestResponse {
  ticker: string;
  data: BacktestPoint[];
  accuracy: BacktestAccuracy | null;
}

export interface TierHealthResponse {
  timestamp: number;
  health: {
    tiers: TierHealth[];
    summary: HealthSummary;
  };
}

// ---------------------------------------------------------------
// Piotroski F-Score
// ---------------------------------------------------------------

export interface PiotroskiRow {
  ticker: string;
  company_name: string | null;
  total_score: number;
  label: string;
  roa_positive: boolean;
  operating_cf_positive: boolean;
  roa_increasing: boolean;
  cf_gt_net_income: boolean;
  leverage_decreasing: boolean;
  current_ratio_increasing: boolean;
  no_dilution: boolean;
  gross_margin_increasing: boolean;
  asset_turnover_increasing: boolean;
  market_cap: number | null;
  revenue: number | null;
  avg_volume: number | null;
  sector: string | null;
  industry: string | null;
  score_date: string | null;
  action?: string;
}

export interface PiotroskiResponse {
  rows: PiotroskiRow[];
  sectors: string[];
  score_date: string | null;
}

// ---------------------------------------------------------------
// LLM Portfolio Recommendations (ASETPLTFRM-298)
// ---------------------------------------------------------------

export interface RecommendationItem {
  id: string;
  tier: "portfolio" | "watchlist" | "discovery";
  category: string;
  ticker: string | null;
  company_name?: string | null;
  action: string;
  severity: "high" | "medium" | "low";
  rationale: string;
  expected_impact?: string | null;
  data_signals: Record<string, number | string>;
  price_at_rec?: number | null;
  target_price?: number | null;
  expected_return_pct?: number | null;
  index_tags: string[];
  status: string;
  acted_on_date?: string | null;
}

export interface RecommendationResponse {
  run_id: string;
  run_date: string;
  run_type: string;
  health_score: number;
  health_label: string;
  health_assessment?: string | null;
  recommendations: RecommendationItem[];
  generated_at?: string | null;
  cached?: boolean;
  reset_at?: string | null;
  scope?: string | null;
}

export interface HistoryRunItem {
  run_id: string;
  run_date: string;
  created_at?: string;
  scope: string;
  run_type: string;
  health_score: number;
  health_label: string;
  total_recommendations: number;
  acted_on_count: number;
}

export interface AggregateStats {
  total_runs: number;
  total_recommendations: number;
  overall_hit_rate_30d?: number | null;
  overall_hit_rate_60d?: number | null;
  overall_hit_rate_90d?: number | null;
  adoption_rate_pct: number;
}

export interface RecommendationHistoryResponse {
  runs: HistoryRunItem[];
  aggregate_stats: AggregateStats;
}

export interface RecommendationStatsResponse {
  total_recommendations: number;
  total_acted_on: number;
  adoption_rate_pct: number;
  hit_rate_30d?: number | null;
  hit_rate_60d?: number | null;
  hit_rate_90d?: number | null;
  avg_return_30d?: number | null;
  avg_return_60d?: number | null;
  avg_return_90d?: number | null;
}

export interface PortfolioTransaction {
  transaction_id: string;
  trade_date: string;
  side: string;
  quantity: number;
  price: number;
  fees: number;
  notes?: string | null;
}

export interface PortfolioTransactionSummary {
  total_quantity: number;
  avg_price: number;
  invested: number;
  current_price: number | null;
  current_value: number | null;
  gain: number | null;
  gain_pct: number | null;
}

export interface PortfolioTransactionsResponse {
  ticker: string;
  currency: string;
  market: string;
  transactions: PortfolioTransaction[];
  summary: PortfolioTransactionSummary;
}
