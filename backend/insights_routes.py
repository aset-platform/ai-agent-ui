"""Insights API endpoints.

Provides data for the native Next.js Insights page (7 tabs):
Screener, Price Targets, Dividends, Risk Metrics, Sectors,
Correlation, and Quarterly.  All queries are scoped to the
authenticated user's linked tickers.

Responses are cached in Redis with 300 s TTL.  Cache keys
use the ``cache:insights:`` prefix and are invalidated by
:mod:`stocks.repository` on Iceberg writes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query, Response

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_current_user
from auth.models import UserContext
from cache import get_cache, TTL_STABLE
from insights_models import (
    CorrelationResponse,
    DividendRow,
    DividendsResponse,
    QuarterlyResponse,
    QuarterlyRow,
    RiskResponse,
    RiskRow,
    ScreenerResponse,
    ScreenerRow,
    SectorRow,
    SectorsResponse,
    TargetRow,
    TargetsResponse,
)

_logger = logging.getLogger(__name__)


def _get_stock_repo():
    """Return the process-wide StockRepository."""
    from tools._stock_shared import _require_repo

    return _require_repo()


def _market(ticker: str) -> str:
    """Return 'india' or 'us' based on ticker suffix."""
    if ticker.endswith(".NS") or ticker.endswith(".BO"):
        return "india"
    return "us"


def _safe(val) -> float | None:
    """Convert to float or return None for NaN/inf."""
    if val is None:
        return None
    try:
        import math

        f = float(val)
        return None if math.isnan(f) else round(f, 4)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> int | None:
    """Convert to int or return None."""
    if val is None:
        return None
    try:
        import math

        f = float(val)
        if math.isnan(f):
            return None
        return int(f)
    except (ValueError, TypeError):
        return None


async def _get_user_tickers(user: UserContext) -> list[str]:
    """Fetch user's linked tickers."""
    repo = _helpers._get_repo()
    return await repo.get_user_tickers(user.user_id)


def _get_company_info_df(
    stock_repo,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """Load company_info with optional ticker filter.

    Uses batch predicate push-down when *tickers*
    is provided, avoiding a full table scan.
    """
    try:
        if tickers:
            return stock_repo.get_company_info_batch(
                tickers,
            )
        return stock_repo._table_to_df(
            "stocks.company_info",
        )
    except Exception:
        return pd.DataFrame()


def _sector_for_ticker(
    ticker: str,
    company_df: pd.DataFrame,
) -> str | None:
    """Look up sector for a ticker from company_info."""
    if company_df.empty or "ticker" not in company_df:
        return None
    match = company_df[company_df["ticker"] == ticker]
    if match.empty:
        return None
    return str(match.iloc[-1].get("sector", "")) or None


def _collect_sectors(
    company_df: pd.DataFrame,
    tickers: list[str],
) -> list[str]:
    """Unique sorted sector names for given tickers."""
    if company_df.empty or "sector" not in company_df:
        return []
    filtered = company_df[
        company_df["ticker"].isin(tickers)
    ]
    sectors = (
        filtered["sector"]
        .dropna()
        .unique()
        .tolist()
    )
    return sorted(str(s) for s in sectors if s)


def _set_cache_header(response: Response):
    """Router dependency: set Cache-Control on all."""
    yield
    response.headers["Cache-Control"] = (
        "private, max-age=300"
    )


def create_insights_router() -> APIRouter:
    """Build the ``/insights`` router."""
    router = APIRouter(
        prefix="/insights",
        tags=["insights"],
        dependencies=[Depends(_set_cache_header)],
    )

    # -----------------------------------------------------------
    # Tab 1: Screener
    # -----------------------------------------------------------

    @router.get(
        "/screener",
        response_model=ScreenerResponse,
    )
    async def get_screener(
        user: UserContext = Depends(get_current_user),
    ):
        """Screener: analysis summary per ticker."""
        cache = get_cache()
        ck = (
            f"cache:insights:screener:"
            f"{user.user_id}"
        )
        hit = cache.get(ck)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        tickers = await _get_user_tickers(user)
        if not tickers:
            return ScreenerResponse()

        # Batch reads — 3 queries instead of 2N+1.
        try:
            df = (
                stock_repo
                .get_analysis_summary_batch(tickers)
            )
        except Exception as exc:
            _logger.error("screener read: %s", exc)
            return ScreenerResponse()

        if df.empty:
            return ScreenerResponse()

        # Batch OHLCV for prices.
        ohlcv_df = stock_repo.get_ohlcv_batch(
            tickers,
        )
        price_map: dict[str, float | None] = {}
        if not ohlcv_df.empty:
            latest = ohlcv_df.drop_duplicates(
                subset=["ticker"], keep="last",
            )
            for _, r in latest.iterrows():
                price_map[str(r["ticker"])] = (
                    _safe(r["close"])
                )

        # Batch TI for RSI.
        ti_df = (
            stock_repo
            .get_technical_indicators_batch(tickers)
        )
        rsi_map: dict[str, float | None] = {}
        if not ti_df.empty:
            latest_ti = ti_df.drop_duplicates(
                subset=["ticker"], keep="last",
            )
            for _, r in latest_ti.iterrows():
                rsi_map[str(r["ticker"])] = (
                    _safe(r.get("rsi_14"))
                )

        company_df = _get_company_info_df(
            stock_repo, tickers,
        )
        sectors = _collect_sectors(
            company_df, tickers,
        )

        rows: list[ScreenerRow] = []
        for _, row in df.iterrows():
            t = str(row["ticker"])
            rows.append(
                ScreenerRow(
                    ticker=t,
                    price=price_map.get(t),
                    rsi_14=rsi_map.get(t),
                    rsi_signal=str(
                        row.get("rsi_signal", "")
                    ) or None,
                    macd_signal=str(
                        row.get(
                            "macd_signal_text", ""
                        )
                    ) or None,
                    sma_200_signal=str(
                        row.get(
                            "sma_200_signal", ""
                        )
                    ) or None,
                    annualized_return_pct=_safe(
                        row.get(
                            "annualized_return_pct"
                        )
                    ),
                    annualized_volatility_pct=_safe(
                        row.get(
                            "annualized_volatility_pct"
                        )
                    ),
                    sharpe_ratio=_safe(
                        row.get("sharpe_ratio")
                    ),
                    sector=_sector_for_ticker(
                        t, company_df,
                    ),
                    market=_market(t),
                )
            )

        result = ScreenerResponse(
            rows=rows, sectors=sectors,
        )
        cache.set(
            ck, result.model_dump_json(), TTL_STABLE,
        )
        return result

    # -----------------------------------------------------------
    # Tab 2: Price Targets
    # -----------------------------------------------------------

    @router.get(
        "/targets",
        response_model=TargetsResponse,
    )
    async def get_targets(
        user: UserContext = Depends(get_current_user),
    ):
        """Price targets from forecast runs."""
        cache = get_cache()
        ck = (
            f"cache:insights:targets:"
            f"{user.user_id}"
        )
        hit = cache.get(ck)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        tickers = await _get_user_tickers(user)
        if not tickers:
            return TargetsResponse()

        try:
            df = stock_repo._scan_tickers(
                "stocks.forecast_runs", tickers,
            )
        except Exception as exc:
            _logger.error("targets read: %s", exc)
            return TargetsResponse()

        if df.empty:
            return TargetsResponse()

        # Deduplicate: latest run per (ticker, horizon).
        if "run_date" in df.columns:
            df = df.sort_values("run_date")
        dedup_cols = ["ticker"]
        if "horizon_months" in df.columns:
            dedup_cols.append("horizon_months")
        df = df.drop_duplicates(
            subset=dedup_cols, keep="last",
        )

        company_df = _get_company_info_df(
            stock_repo, tickers,
        )
        sectors = _collect_sectors(
            company_df, tickers,
        )

        rows: list[TargetRow] = []
        for _, row in df.iterrows():
            t = str(row["ticker"])
            rows.append(
                TargetRow(
                    ticker=t,
                    horizon_months=_safe_int(
                        row.get("horizon_months")
                    ),
                    run_date=str(
                        row.get("run_date", "")
                    ) or None,
                    current_price=_safe(
                        row.get("current_price_at_run")
                    ),
                    target_3m_price=_safe(
                        row.get("target_3m_price")
                    ),
                    target_3m_pct=_safe(
                        row.get("target_3m_pct_change")
                    ),
                    target_6m_price=_safe(
                        row.get("target_6m_price")
                    ),
                    target_6m_pct=_safe(
                        row.get("target_6m_pct_change")
                    ),
                    target_9m_price=_safe(
                        row.get("target_9m_price")
                    ),
                    target_9m_pct=_safe(
                        row.get("target_9m_pct_change")
                    ),
                    sentiment=str(
                        row.get("sentiment", "")
                    ) or None,
                    market=_market(t),
                    sector=_sector_for_ticker(
                        t, company_df,
                    ),
                )
            )

        result = TargetsResponse(
            rows=rows,
            tickers=sorted(
                df["ticker"].unique().tolist()
            ),
            sectors=sectors,
        )
        cache.set(
            ck, result.model_dump_json(), TTL_STABLE,
        )
        return result

    # -----------------------------------------------------------
    # Tab 3: Dividends
    # -----------------------------------------------------------

    @router.get(
        "/dividends",
        response_model=DividendsResponse,
    )
    async def get_dividends(
        user: UserContext = Depends(get_current_user),
    ):
        """Dividend history for user's tickers."""
        cache = get_cache()
        ck = (
            f"cache:insights:dividends:"
            f"{user.user_id}"
        )
        hit = cache.get(ck)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        tickers = await _get_user_tickers(user)
        if not tickers:
            return DividendsResponse()

        try:
            df = stock_repo._scan_tickers(
                "stocks.dividends", tickers,
            )
        except Exception as exc:
            _logger.error("dividends read: %s", exc)
            return DividendsResponse()

        if df.empty:
            return DividendsResponse()

        # Sort by ex_date descending.
        if "ex_date" in df.columns:
            df = df.sort_values(
                "ex_date", ascending=False,
            )

        company_df = _get_company_info_df(
            stock_repo, tickers,
        )
        sectors = _collect_sectors(
            company_df, tickers,
        )

        rows: list[DividendRow] = []
        for _, row in df.iterrows():
            t = str(row["ticker"])
            rows.append(
                DividendRow(
                    ticker=t,
                    ex_date=str(
                        row.get("ex_date", "")
                    ) or None,
                    amount=_safe(
                        row.get("dividend_amount")
                    ),
                    currency=str(
                        row.get("currency", "USD")
                    ) or "USD",
                    market=_market(t),
                    sector=_sector_for_ticker(
                        t, company_df,
                    ),
                )
            )

        result = DividendsResponse(
            rows=rows,
            tickers=sorted(
                df["ticker"].unique().tolist()
            ),
            sectors=sectors,
        )
        cache.set(
            ck, result.model_dump_json(), TTL_STABLE,
        )
        return result

    # -----------------------------------------------------------
    # Tab 4: Risk Metrics
    # -----------------------------------------------------------

    @router.get(
        "/risk",
        response_model=RiskResponse,
    )
    async def get_risk(
        user: UserContext = Depends(get_current_user),
    ):
        """Risk metrics from analysis summary."""
        cache = get_cache()
        ck = (
            f"cache:insights:risk:{user.user_id}"
        )
        hit = cache.get(ck)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        tickers = await _get_user_tickers(user)
        if not tickers:
            return RiskResponse()

        try:
            df = (
                stock_repo
                .get_analysis_summary_batch(tickers)
            )
        except Exception as exc:
            _logger.error("risk read: %s", exc)
            return RiskResponse()

        if df.empty:
            return RiskResponse()

        company_df = _get_company_info_df(
            stock_repo, tickers,
        )
        sectors = _collect_sectors(
            company_df, tickers,
        )

        rows: list[RiskRow] = []
        for _, row in df.iterrows():
            t = str(row["ticker"])
            rows.append(
                RiskRow(
                    ticker=t,
                    annualized_return_pct=_safe(
                        row.get(
                            "annualized_return_pct"
                        )
                    ),
                    annualized_volatility_pct=_safe(
                        row.get(
                            "annualized_volatility_pct"
                        )
                    ),
                    sharpe_ratio=_safe(
                        row.get("sharpe_ratio")
                    ),
                    max_drawdown_pct=_safe(
                        row.get("max_drawdown_pct")
                    ),
                    max_drawdown_days=_safe_int(
                        row.get(
                            "max_drawdown_duration_days"
                        )
                    ),
                    bull_phase_pct=_safe(
                        row.get("bull_phase_pct")
                    ),
                    bear_phase_pct=_safe(
                        row.get("bear_phase_pct")
                    ),
                    market=_market(t),
                    sector=_sector_for_ticker(
                        t, company_df,
                    ),
                )
            )

        result = RiskResponse(
            rows=rows, sectors=sectors,
        )
        cache.set(
            ck, result.model_dump_json(), TTL_STABLE,
        )
        return result

    # -----------------------------------------------------------
    # Tab 5: Sectors
    # -----------------------------------------------------------

    @router.get(
        "/sectors",
        response_model=SectorsResponse,
    )
    async def get_sectors(
        market: str = Query(
            "all",
            description="'all', 'india', or 'us'",
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """Sector-level aggregated metrics."""
        cache = get_cache()
        ck = (
            f"cache:insights:sectors:"
            f"{user.user_id}:{market}"
        )
        hit = cache.get(ck)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        tickers = await _get_user_tickers(user)
        if not tickers:
            return SectorsResponse()

        try:
            analysis_df = (
                stock_repo
                .get_analysis_summary_batch(tickers)
            )
            company_df = _get_company_info_df(
                stock_repo, tickers,
            )
        except Exception as exc:
            _logger.error("sectors read: %s", exc)
            return SectorsResponse()

        if analysis_df.empty or company_df.empty:
            return SectorsResponse()

        # Market filter.
        if market == "india":
            analysis_df = analysis_df[
                analysis_df["ticker"].str.endswith(
                    (".NS", ".BO")
                )
            ]
        elif market == "us":
            analysis_df = analysis_df[
                ~analysis_df["ticker"].str.endswith(
                    (".NS", ".BO")
                )
            ]

        if analysis_df.empty:
            return SectorsResponse()

        # Latest company_info per ticker for sector.
        if "fetched_at" in company_df.columns:
            company_df = company_df.sort_values(
                "fetched_at",
            )
        company_df = company_df.drop_duplicates(
            subset=["ticker"], keep="last",
        )
        sector_map = company_df.set_index("ticker")[
            "sector"
        ].to_dict()

        # Join sector onto analysis.
        analysis_df["sector"] = (
            analysis_df["ticker"].map(sector_map)
        )
        analysis_df = analysis_df.dropna(
            subset=["sector"],
        )
        if analysis_df.empty:
            return SectorsResponse()

        # Aggregate per sector.
        grouped = analysis_df.groupby("sector").agg(
            stock_count=("ticker", "count"),
            avg_return=(
                "annualized_return_pct", "mean"
            ),
            avg_sharpe=("sharpe_ratio", "mean"),
            avg_vol=(
                "annualized_volatility_pct", "mean"
            ),
        )
        grouped = grouped.sort_values(
            "avg_return", ascending=False,
        )

        rows: list[SectorRow] = []
        for sector, agg in grouped.iterrows():
            rows.append(
                SectorRow(
                    sector=str(sector),
                    stock_count=int(
                        agg["stock_count"]
                    ),
                    avg_return_pct=_safe(
                        agg["avg_return"]
                    ),
                    avg_sharpe=_safe(
                        agg["avg_sharpe"]
                    ),
                    avg_volatility_pct=_safe(
                        agg["avg_vol"]
                    ),
                )
            )

        result = SectorsResponse(rows=rows)
        cache.set(
            ck, result.model_dump_json(), TTL_STABLE,
        )
        return result

    # -----------------------------------------------------------
    # Tab 6: Correlation
    # -----------------------------------------------------------

    @router.get(
        "/correlation",
        response_model=CorrelationResponse,
    )
    async def get_correlation(
        period: str = Query(
            "1y",
            description="'1y', '3y', or 'all'",
        ),
        market: str = Query(
            "all",
            description="'all', 'india', or 'us'",
        ),
        source: str = Query(
            "portfolio",
            description=(
                "'portfolio' or 'watchlist'"
            ),
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """Pairwise daily-returns correlation matrix."""
        cache = get_cache()
        ck = (
            f"cache:insights:correlation:"
            f"{user.user_id}:{period}:{market}"
            f":{source}"
        )
        hit = cache.get(ck)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()

        # Source: portfolio or watchlist
        if source == "portfolio":
            holdings_df = (
                stock_repo.get_portfolio_holdings(
                    user.user_id,
                )
            )
            if holdings_df.empty:
                tickers = []
            else:
                tickers = list(
                    holdings_df["ticker"]
                    .astype(str)
                    .unique(),
                )
        else:
            tickers = await _get_user_tickers(user)

        if not tickers:
            return CorrelationResponse(period=period)

        # Market filter on tickers.
        if market == "india":
            tickers = [
                t for t in tickers
                if t.endswith((".NS", ".BO"))
            ]
        elif market == "us":
            tickers = [
                t for t in tickers
                if not t.endswith((".NS", ".BO"))
            ]

        if len(tickers) < 2:
            return CorrelationResponse(
                tickers=sorted(tickers),
                period=period,
            )

        # Period cutoff.
        cutoff = None
        now = datetime.utcnow().date()
        if period == "1y":
            cutoff = now - timedelta(days=365)
        elif period == "3y":
            cutoff = now - timedelta(days=1095)

        # Batch OHLCV — 1 query instead of N.
        all_ohlcv = stock_repo.get_ohlcv_batch(
            tickers,
        )
        if all_ohlcv.empty:
            return CorrelationResponse(
                tickers=sorted(tickers),
                period=period,
            )

        if "date" in all_ohlcv.columns:
            all_ohlcv["date"] = pd.to_datetime(
                all_ohlcv["date"],
            )
            if cutoff:
                all_ohlcv = all_ohlcv[
                    all_ohlcv["date"]
                    >= pd.Timestamp(cutoff)
                ]

        returns: dict[str, pd.Series] = {}
        for t, grp in all_ohlcv.groupby("ticker"):
            if len(grp) < 10:
                continue
            close = grp["close"].astype(float)
            daily = close.pct_change().dropna()
            daily.index = range(len(daily))
            returns[str(t)] = daily

        valid = sorted(returns.keys())
        if len(valid) < 2:
            return CorrelationResponse(
                tickers=valid, period=period,
            )

        # Align on common length.
        ret_df = pd.DataFrame(returns)
        corr = ret_df.corr().values
        matrix = [
            [
                round(float(v), 4)
                if not np.isnan(v)
                else 0.0
                for v in row
            ]
            for row in corr
        ]

        result = CorrelationResponse(
            tickers=valid,
            matrix=matrix,
            period=period,
        )
        cache.set(
            ck, result.model_dump_json(), TTL_STABLE,
        )
        return result

    # -----------------------------------------------------------
    # Tab 7: Quarterly
    # -----------------------------------------------------------

    @router.get(
        "/quarterly",
        response_model=QuarterlyResponse,
    )
    async def get_quarterly(
        statement_type: str = Query(
            "income",
            description=(
                "'income', 'balance', or 'cashflow'"
            ),
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """Quarterly financial results."""
        cache = get_cache()
        ck = (
            f"cache:insights:quarterly:"
            f"{user.user_id}:{statement_type}"
        )
        hit = cache.get(ck)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        tickers = await _get_user_tickers(user)
        if not tickers:
            return QuarterlyResponse()

        try:
            df = stock_repo._scan_tickers(
                "stocks.quarterly_results",
                tickers,
            )
        except Exception as exc:
            _logger.error("quarterly read: %s", exc)
            return QuarterlyResponse()

        if df.empty:
            return QuarterlyResponse()

        # Filter by statement type if column exists.
        if (
            "statement_type" in df.columns
            and statement_type != "all"
        ):
            df = df[
                df["statement_type"] == statement_type
            ]

        # Deduplicate per (ticker, quarter_end).
        if "quarter_end" in df.columns:
            df = df.sort_values("quarter_end")
        dedup = ["ticker"]
        if "quarter_end" in df.columns:
            dedup.append("quarter_end")
        df = df.drop_duplicates(
            subset=dedup, keep="last",
        )

        company_df = _get_company_info_df(
            stock_repo, tickers,
        )
        sectors = _collect_sectors(
            company_df, tickers,
        )

        rows: list[QuarterlyRow] = []
        for _, row in df.iterrows():
            t = str(row["ticker"])
            # Build quarter label.
            fq = row.get("fiscal_quarter", "")
            fy = row.get("fiscal_year", "")
            label = None
            if fq and fy:
                label = f"{fq} {fy}"

            rows.append(
                QuarterlyRow(
                    ticker=t,
                    quarter_label=label,
                    quarter_end=str(
                        row.get("quarter_end", "")
                    ) or None,
                    statement_type=str(
                        row.get("statement_type", "")
                    ) or None,
                    revenue=_safe(
                        row.get("revenue")
                    ),
                    net_income=_safe(
                        row.get("net_income")
                    ),
                    eps=_safe(row.get("eps")),
                    total_assets=_safe(
                        row.get("total_assets")
                    ),
                    total_equity=_safe(
                        row.get("total_equity")
                    ),
                    operating_cashflow=_safe(
                        row.get("operating_cashflow")
                    ),
                    free_cashflow=_safe(
                        row.get("free_cashflow")
                    ),
                    market=_market(t),
                    sector=_sector_for_ticker(
                        t, company_df,
                    ),
                )
            )

        result = QuarterlyResponse(
            rows=rows,
            tickers=sorted(
                df["ticker"].unique().tolist()
            ),
            sectors=sectors,
        )
        cache.set(
            ck, result.model_dump_json(), TTL_STABLE,
        )
        return result

    return router
