"""Pydantic models for dashboard and audit API endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TickerPrice(BaseModel):
    ticker: str
    company_name: str | None = None
    current_price: float
    previous_close: float
    change: float
    change_pct: float
    currency: str = "USD"
    market: str = "us"
    sparkline: list[float] = Field(default_factory=list)


class WatchlistResponse(BaseModel):
    tickers: list[TickerPrice] = Field(
        default_factory=list,
    )
    portfolio_value: float | None = None
    daily_change: float | None = None
    daily_change_pct: float | None = None


class ForecastTarget(BaseModel):
    horizon_months: int
    target_date: str
    target_price: float
    pct_change: float
    lower_bound: float
    upper_bound: float


class TickerForecast(BaseModel):
    ticker: str
    run_date: str
    current_price: float
    sentiment: str | None = None
    targets: list[ForecastTarget] = Field(
        default_factory=list,
    )
    mae: float | None = None
    rmse: float | None = None
    mape: float | None = None


class ForecastsResponse(BaseModel):
    forecasts: list[TickerForecast] = Field(
        default_factory=list,
    )


class SignalInfo(BaseModel):
    name: str
    value: str
    signal: str
    description: str


class TickerAnalysis(BaseModel):
    ticker: str
    analysis_date: str
    signals: list[SignalInfo] = Field(
        default_factory=list,
    )
    sharpe_ratio: float | None = None
    annualized_return_pct: float | None = None
    annualized_volatility_pct: float | None = None
    max_drawdown_pct: float | None = None


class AnalysisResponse(BaseModel):
    analyses: list[TickerAnalysis] = Field(
        default_factory=list,
    )


class ModelUsage(BaseModel):
    model: str
    provider: str
    request_count: int
    total_tokens: int
    estimated_cost_usd: float


class LLMUsageResponse(BaseModel):
    total_requests: int = 0
    total_cost_usd: float = 0.0
    avg_latency_ms: float | None = None
    models: list[ModelUsage] = Field(
        default_factory=list,
    )
    daily_trend: list[dict] = Field(
        default_factory=list,
    )


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: str
    agent_id: str | None = None


class ChatSessionCreate(BaseModel):
    session_id: str = Field(..., min_length=1)
    messages: list[ChatMessage] = Field(
        ..., min_length=1,
    )


class ChatSessionSummary(BaseModel):
    session_id: str
    started_at: str
    ended_at: str
    message_count: int
    preview: str
    agent_ids_used: list[str] = Field(
        default_factory=list,
    )


class ChatSessionDetail(ChatSessionSummary):
    messages: list[ChatMessage] = Field(
        default_factory=list,
    )


class RegistryTicker(BaseModel):
    ticker: str
    company_name: str | None = None
    market: str = "us"
    currency: str = "USD"
    current_price: float | None = None
    last_fetch_date: str | None = None


class RegistryResponse(BaseModel):
    tickers: list[RegistryTicker] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------
# Compare
# ---------------------------------------------------------------

class CompareSeriesItem(BaseModel):
    ticker: str
    dates: list[str]
    normalized: list[float]


class CompareMetric(BaseModel):
    ticker: str
    annualized_return_pct: float | None = None
    annualized_volatility_pct: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    current_price: float | None = None
    currency: str = "USD"
    rsi_14: float | None = None
    macd_signal: str | None = None
    sentiment: str | None = None


class CompareResponse(BaseModel):
    tickers: list[str]
    series: list[CompareSeriesItem] = Field(
        default_factory=list,
    )
    correlation: list[list[float]] = Field(
        default_factory=list,
    )
    metrics: list[CompareMetric] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------
# Chart endpoints (Analysis page)
# ---------------------------------------------------------------

class OHLCVPoint(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class OHLCVResponse(BaseModel):
    ticker: str
    data: list[OHLCVPoint] = Field(
        default_factory=list,
    )


class IndicatorPoint(BaseModel):
    date: str
    sma_50: float | None = None
    sma_200: float | None = None
    ema_20: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None


class IndicatorsResponse(BaseModel):
    ticker: str
    data: list[IndicatorPoint] = Field(
        default_factory=list,
    )


class ForecastPoint(BaseModel):
    date: str
    predicted: float
    lower: float
    upper: float


class ForecastSeriesResponse(BaseModel):
    ticker: str
    horizon_months: int
    data: list[ForecastPoint] = Field(
        default_factory=list,
    )


class DashboardHomeResponse(BaseModel):
    """Aggregate response for ``/dashboard/home``.

    Returns all widget data in a single request so
    the frontend can render the dashboard with one
    network round-trip instead of 4.
    """

    watchlist: WatchlistResponse = Field(
        default_factory=WatchlistResponse,
    )
    forecasts: ForecastsResponse = Field(
        default_factory=ForecastsResponse,
    )
    analysis: AnalysisResponse = Field(
        default_factory=AnalysisResponse,
    )
    llm_usage: LLMUsageResponse = Field(
        default_factory=LLMUsageResponse,
    )


# ---------------------------------------------------------------
# Portfolio Performance & Forecast
# ---------------------------------------------------------------

class PortfolioDailyPoint(BaseModel):
    date: str
    value: float
    invested_value: float
    daily_pnl: float
    daily_return_pct: float


class PortfolioMetrics(BaseModel):
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float | None = None
    best_day_pct: float
    best_day_date: str
    worst_day_pct: float
    worst_day_date: str


class PortfolioPerformanceResponse(BaseModel):
    data: list[PortfolioDailyPoint] = Field(
        default_factory=list,
    )
    metrics: PortfolioMetrics | None = None
    currency: str = "USD"


class PortfolioForecastPoint(BaseModel):
    date: str
    predicted: float
    lower: float
    upper: float


class PortfolioForecastResponse(BaseModel):
    data: list[PortfolioForecastPoint] = Field(
        default_factory=list,
    )
    horizon_months: int = 9
    current_value: float = 0.0
    total_invested: float = 0.0
    currency: str = "USD"
