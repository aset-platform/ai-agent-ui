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
