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
    """Single row in the Screener table.

    All fields beyond ``ticker`` are optional — not every
    ticker has data for every metric. The frontend column
    selector (ASETPLTFRM-333) lets users pick which subset
    to render; the server always returns the full shape so
    CSV export and client-side column toggles work without
    extra round-trips.
    """

    # --- Identity ---
    ticker: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market: str = "us"
    currency: str | None = None
    tags: list[str] = Field(default_factory=list)

    # --- Pricing / returns (analysis_summary + ohlcv) ---
    price: float | None = None
    current_price: float | None = None
    week_52_high: float | None = None
    week_52_low: float | None = None

    # --- Valuation (company_info + derived) ---
    market_cap: float | None = None
    pe_ratio: float | None = None
    price_to_book: float | None = None
    dividend_yield: float | None = None
    # PEG (Price/Earnings-to-Growth). Three sources:
    # - `peg_ratio` — P/E ÷ (earnings_growth × 100)
    #   using yfinance-sourced pe_ratio +
    #   earningsGrowth on company_info (trailing YoY).
    # - `peg_ratio_yf` — raw from yfinance's pegRatio
    #   (forward-looking, analyst-consensus growth).
    #   Sparse for Indian equities.
    # - `peg_ratio_ttm` — ground-truth PEG computed
    #   from our own quarterly_results filings. TTM EPS
    #   drives P/E, single-quarter YoY growth drives
    #   the denominator. Requires ≥5 quarters of
    #   income-statement history.
    # All null when earnings are negative or growth
    # ≤ 0 (standard PEG convention — can't value a
    # decline).
    peg_ratio: float | None = None
    peg_ratio_yf: float | None = None
    peg_ratio_ttm: float | None = None

    # --- Profitability (company_info + quarterly_results) ---
    profit_margins: float | None = None
    earnings_growth: float | None = None
    revenue_growth: float | None = None
    eps: float | None = None
    revenue: float | None = None
    net_income: float | None = None

    # --- Risk (analysis_summary) ---
    annualized_return_pct: float | None = None
    annualized_volatility_pct: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    beta: float | None = None

    # --- Technical (analysis_summary + sentiment_scores) ---
    rsi_14: float | None = None
    rsi_signal: str | None = None
    macd_signal: str | None = None
    sma_200_signal: str | None = None
    sentiment_score: float | None = None
    sentiment_headlines: int | None = None

    # --- Quality (piotroski_scores + forecast_runs) ---
    piotroski_score: int | None = None
    piotroski_label: str | None = None
    forecast_confidence: float | None = None

    # --- Forecast (forecast_runs) ---
    target_3m_pct: float | None = None
    target_6m_pct: float | None = None
    target_9m_pct: float | None = None


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
    # ASETPLTFRM-333: extra display columns to include
    # in the SELECT beyond what the WHERE clause
    # references. Unknown field names are silently
    # ignored; base columns + filter fields are always
    # included regardless.
    display_columns: list[str] = Field(
        default_factory=list,
    )


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
