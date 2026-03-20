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
    annualized_return_pct: float | None = None
    annualized_volatility_pct: float | None = None
    sharpe_ratio: float | None = None
    sector: str | None = None
    market: str = "us"


class ScreenerResponse(BaseModel):
    """Screener tab response."""

    rows: list[ScreenerRow] = Field(
        default_factory=list,
    )
    sectors: list[str] = Field(
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
