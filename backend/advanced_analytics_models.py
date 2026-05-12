"""Pydantic models for Advanced Analytics endpoints (AA-7).

The 7 reports surfaced under ``/v1/advanced-analytics/`` all
return the same response shape — a paginated list of
:class:`AdvancedRow` plus a ``stale_tickers`` transparency
list (§5.5). One superset row model keeps CSV export, column
selector, and the shared ``<AdvancedAnalyticsTable />`` DRY
across the seven tabs (per plan §6).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


StaleReason = Literal[
    "nan_close",
    "missing_delivery",
    "missing_quarterly",
    "missing_promoter",
]


class StaleTicker(BaseModel):
    """One ticker omitted from / partially-populated in a row.

    Surfaced in the panel-title row as an amber chip
    (§5.5 ``portfolio-pl-stale-ticker-chip``) — preferred
    over silently dropping rows or ffilling NaN inputs.
    """

    ticker: str
    reason: StaleReason


class AdvancedRow(BaseModel):
    """Superset row for all 7 Advanced Analytics tabs.

    Every field beyond ``ticker`` is optional — a single
    response model serves seven distinct reports, and the
    frontend column selector (§5.4) chooses which subset
    to render per tab.
    """

    # --- Identity ---
    ticker: str
    company_name: str | None = None
    sector: str | None = None
    sub_sector: str | None = None

    # --- Piotroski ---
    pscore: int | None = None

    # --- Technical indicators ---
    rsi: float | None = None
    avg_emv_score: float | None = None
    avg_14d_emv: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    # Trading days since SMA 50 last crossed above SMA 200.
    # None → no golden cross (SMA 50 ≤ SMA 200 today).
    # 0–10 → recent cross (amber).  11+ / 999 → established (green).
    golden_cross_days_ago: int | None = None

    # --- Price (last 3 days + windowed averages) ---
    today_ltp: float | None = None
    prev_day_ltp: float | None = None
    prev_2_prev_day_ltp: float | None = None
    current_ppc: float | None = None
    avg_10d_ppc: float | None = None
    avg_20d_ppc: float | None = None
    week_52_high: float | None = None
    week_52_low: float | None = None
    away_from_52week_high: float | None = None

    # --- Volume + multipliers ---
    today_vol: float | None = None
    prev_day_vol: float | None = None
    avg_10d_vol: float | None = None
    avg_20d_vol: float | None = None
    today_x_vol: float | None = None
    prev_day_x_vol: float | None = None
    x_vol_10d: float | None = None
    x_vol_20d: float | None = None

    # --- Delivery (NSE bhavcopy) ---
    today_dv: float | None = None
    prev_day_dv: float | None = None
    avg_10d_dv: float | None = None
    avg_20d_dv: float | None = None
    today_dpc: float | None = None
    prev_day_dpc: float | None = None
    avg_10d_dpc: float | None = None
    avg_20d_dpc: float | None = None
    today_x_dv: float | None = None
    prev_day_x_dv: float | None = None
    x_dv_10d: float | None = None
    x_dv_20d: float | None = None
    current_dpc: float | None = None

    # --- Notional (vol × close, derived) ---
    today_not: float | None = None
    avg_10d_not: float | None = None
    avg_20d_not: float | None = None

    # --- Fundamentals snapshot ---
    debt_to_eq: float | None = None
    yoy_qtr_prft: float | None = None
    yoy_qtr_sales: float | None = None
    sales_growth_3yrs: float | None = None
    prft_growth_3yrs: float | None = None
    sales_growth_5yrs: float | None = None
    prft_growth_5yrs: float | None = None
    roce: float | None = None

    # --- Promoter holdings ---
    chng_in_prom_hld: float | None = None
    pledged: float | None = None
    prom_hld: float | None = None

    # --- Corporate events ---
    event: str | None = None
    event_date: str | None = None  # ISO 8601 UTC ``Z`` suffix

    # Swing-setup computed columns (Task 2-4 of plan).
    # OHLCV today snapshot for swing setups (lower-low break gate).
    today_low: float | None = None
    death_cross_days_ago: int | None = None
    rolling_low_20d_prev: float | None = None
    rolling_high_20d_prev: float | None = None
    rsi_3d_ago: float | None = None
    rsi_max_10d: float | None = None

    # Recommendation-engine join (Task 10 of plan).
    rec_category: str | None = None
    rec_severity: str | None = None
    rec_expected_return_pct: float | None = None


class AdvancedReportResponse(BaseModel):
    """Paginated response for any of the 7 reports."""

    rows: list[AdvancedRow] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 25
    stale_tickers: list[StaleTicker] = Field(default_factory=list)
