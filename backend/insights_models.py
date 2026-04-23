"""Pydantic models for Insights API endpoints.

Mirrors the 7-tab Insights page: Screener, Price Targets,
Dividends, Risk Metrics, Sectors, Correlation, Quarterly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------
# Screener
# ---------------------------------------------------------------


class ScreenerRow(BaseModel):
    """Single row in the Screener table."""

    ticker: str
    price: float | None = None
    rsi_14: float | None = None
    rsi_signal: str | None = None
    macd_signal: str | None = None
    sma_200_signal: str | None = None
    sentiment_score: float | None = None
    sentiment_headlines: int | None = None
    annualized_return_pct: float | None = None
    annualized_volatility_pct: float | None = None
    sharpe_ratio: float | None = None
    sector: str | None = None
    market: str = "us"
    tags: list[str] = Field(default_factory=list)


class ScreenerResponse(BaseModel):
    """Screener tab response."""

    rows: list[ScreenerRow] = Field(
        default_factory=list,
    )
    sectors: list[str] = Field(
        default_factory=list,
    )
    tags: list[str] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------
# Price Targets
# ---------------------------------------------------------------


class TargetRow(BaseModel):
    """Single row in the Price Targets table."""

    ticker: str
    horizon_months: int | None = None
    run_date: str | None = None
    current_price: float | None = None
    target_3m_price: float | None = None
    target_3m_pct: float | None = None
    target_6m_price: float | None = None
    target_6m_pct: float | None = None
    target_9m_price: float | None = None
    target_9m_pct: float | None = None
    sentiment: str | None = None
    market: str = "us"
    sector: str | None = None
    confidence_score: float | None = None
    confidence_components: dict | None = None


class TargetsResponse(BaseModel):
    """Price Targets tab response."""

    rows: list[TargetRow] = Field(
        default_factory=list,
    )
    tickers: list[str] = Field(
        default_factory=list,
    )
    sectors: list[str] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------
# Dividends
# ---------------------------------------------------------------


class DividendRow(BaseModel):
    """Single row in the Dividends table."""

    ticker: str
    ex_date: str | None = None
    amount: float | None = None
    currency: str = "USD"
    market: str = "us"
    sector: str | None = None


class DividendsResponse(BaseModel):
    """Dividends tab response."""

    rows: list[DividendRow] = Field(
        default_factory=list,
    )
    tickers: list[str] = Field(
        default_factory=list,
    )
    sectors: list[str] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------
# Risk Metrics
# ---------------------------------------------------------------


class RiskRow(BaseModel):
    """Single row in the Risk Metrics table."""

    ticker: str
    annualized_return_pct: float | None = None
    annualized_volatility_pct: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    max_drawdown_days: int | None = None
    bull_phase_pct: float | None = None
    bear_phase_pct: float | None = None
    market: str = "us"
    sector: str | None = None


class RiskResponse(BaseModel):
    """Risk Metrics tab response."""

    rows: list[RiskRow] = Field(
        default_factory=list,
    )
    sectors: list[str] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------
# Sectors
# ---------------------------------------------------------------


class SectorRow(BaseModel):
    """Aggregated sector summary row."""

    sector: str
    stock_count: int = 0
    avg_return_pct: float | None = None
    avg_sharpe: float | None = None
    avg_volatility_pct: float | None = None


class SectorsResponse(BaseModel):
    """Sectors tab response (chart + table)."""

    rows: list[SectorRow] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------


class CorrelationResponse(BaseModel):
    """Correlation heatmap data."""

    tickers: list[str] = Field(
        default_factory=list,
    )
    matrix: list[list[float]] = Field(
        default_factory=list,
    )
    period: str = "1y"


# ---------------------------------------------------------------
# Quarterly
# ---------------------------------------------------------------


class QuarterlyRow(BaseModel):
    """Single quarterly results row."""

    ticker: str
    quarter_label: str | None = None
    quarter_end: str | None = None
    statement_type: str | None = None
    revenue: float | None = None
    net_income: float | None = None
    eps: float | None = None
    total_assets: float | None = None
    total_equity: float | None = None
    operating_cashflow: float | None = None
    free_cashflow: float | None = None
    market: str = "us"
    sector: str | None = None


class QuarterlyResponse(BaseModel):
    """Quarterly tab response."""

    rows: list[QuarterlyRow] = Field(
        default_factory=list,
    )
    tickers: list[str] = Field(
        default_factory=list,
    )
    sectors: list[str] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------
# Piotroski F-Score
# ---------------------------------------------------------------


class PiotroskiRow(BaseModel):
    """Single row in the Piotroski F-Score table."""

    ticker: str
    company_name: str | None = None
    total_score: int = 0
    label: str = "Weak"
    roa_positive: bool = False
    operating_cf_positive: bool = False
    roa_increasing: bool = False
    cf_gt_net_income: bool = False
    leverage_decreasing: bool = False
    current_ratio_increasing: bool = False
    no_dilution: bool = False
    gross_margin_increasing: bool = False
    asset_turnover_increasing: bool = False
    market_cap: int | None = None
    revenue: float | None = None
    avg_volume: int | None = None
    sector: str | None = None
    industry: str | None = None
    score_date: str | None = None


class PiotroskiResponse(BaseModel):
    """Piotroski F-Score tab response."""

    rows: list[PiotroskiRow] = Field(
        default_factory=list,
    )
    sectors: list[str] = Field(
        default_factory=list,
    )
    score_date: str | None = None


# ---------------------------------------------------------------
# ScreenQL (universal screener)
# ---------------------------------------------------------------


class ScreenQLRequest(BaseModel):
    """ScreenQL query request."""

    query: str = Field(
        ..., min_length=1, max_length=2000,
    )
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=100)
    sort_by: str | None = None
    sort_dir: str = Field("desc")


class ScreenQLResponse(BaseModel):
    """ScreenQL query response."""

    rows: list[dict] = Field(
        default_factory=list,
    )
    total: int = 0
    page: int = 1
    page_size: int = 25
    columns_used: list[str] = Field(
        default_factory=list,
    )
    excluded_null_count: int = 0


class ScreenFieldDef(BaseModel):
    """Field definition for autocomplete."""

    name: str
    label: str
    type: str
    category: str


class ScreenFieldsResponse(BaseModel):
    """Field catalog response."""

    fields: list[ScreenFieldDef] = Field(
        default_factory=list,
    )
