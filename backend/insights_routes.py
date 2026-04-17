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
from market_utils import detect_market as _market
from insights_models import (
    CorrelationResponse,
    DividendRow,
    DividendsResponse,
    ScreenFieldsResponse,
    ScreenQLRequest,
    ScreenQLResponse,
    PiotroskiResponse,
    PiotroskiRow,
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


def _parse_confidence(val) -> dict | None:
    """Parse confidence_components from JSON string or dict."""
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            import json

            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _safe_str(val) -> str | None:
    """Convert to str or return None for NaN."""
    if val is None:
        return None
    try:
        import math

        if isinstance(val, float) and math.isnan(val):
            return None
        return str(val)
    except (ValueError, TypeError):
        return None


async def _get_user_tickers(user: UserContext) -> list[str]:
    """Fetch tickers visible to user.

    Superusers see all registry tickers (full universe).
    Other roles see only their linked watchlist tickers.
    """
    repo = _helpers._get_repo()
    user_list = await repo.get_user_tickers(user.user_id)

    if user.role != "superuser":
        return user_list

    # Superuser: merge with full registry
    stock_repo = _get_stock_repo()
    registry = stock_repo.get_all_registry()

    seen = set(t.upper() for t in user_list)
    merged = list(user_list)
    for t in registry:
        if t.upper() not in seen:
            merged.append(t)
            seen.add(t.upper())
    return merged


def _get_company_info_df(
    stock_repo,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """Load company_info via DuckDB with ticker filter."""
    try:
        from backend.db.duckdb_engine import (
            query_iceberg_df,
        )

        if tickers:
            ph = ",".join(f"'{t}'" for t in tickers)
            return query_iceberg_df(
                "stocks.company_info",
                "SELECT * FROM company_info "
                f"WHERE ticker IN ({ph})",
            )
        return query_iceberg_df(
            "stocks.company_info",
            "SELECT * FROM company_info",
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
    filtered = company_df[company_df["ticker"].isin(tickers)]
    sectors = filtered["sector"].dropna().unique().tolist()
    return sorted(str(s) for s in sectors if s)


def _set_cache_header(response: Response):
    """Router dependency: set Cache-Control on all."""
    yield
    response.headers["Cache-Control"] = "private, max-age=300"


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
        ck = f"cache:insights:screener:" f"{user.user_id}"
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

        # Batch reads via DuckDB.
        try:
            from backend.db.duckdb_engine import (
                query_iceberg_df,
            )

            ph = ",".join(f"'{t}'" for t in tickers)
            df = query_iceberg_df(
                "stocks.analysis_summary",
                "SELECT ticker, rsi_signal, "
                "macd_signal_text, "
                "sma_200_signal, "
                "annualized_return_pct, "
                "annualized_volatility_pct, "
                "sharpe_ratio "
                "FROM analysis_summary "
                f"WHERE ticker IN ({ph})",
            )
        except Exception as exc:
            _logger.error("screener read: %s", exc)
            return ScreenerResponse()

        if df.empty:
            return ScreenerResponse()

        # Latest close per ticker (not full history).
        try:
            ohlcv_df = query_iceberg_df(
                "stocks.ohlcv",
                "SELECT ticker, close, date FROM ("
                "  SELECT ticker, close, date,"
                "  ROW_NUMBER() OVER ("
                "    PARTITION BY ticker "
                "    ORDER BY date DESC"
                "  ) AS rn FROM ohlcv "
                f"  WHERE ticker IN ({ph})"
                "    AND close IS NOT NULL"
                ") WHERE rn = 1",
            )
        except Exception:
            ohlcv_df = pd.DataFrame()
        price_map: dict[str, float | None] = {}
        if not ohlcv_df.empty:
            # Drop rows with NaN close before picking
            # latest — some tickers have NaN on the
            # most recent date (yfinance data gap).
            valid = ohlcv_df.dropna(subset=["close"])
            if not valid.empty:
                latest = valid.drop_duplicates(
                    subset=["ticker"],
                    keep="last",
                )
                for _, r in latest.iterrows():
                    price_map[str(r["ticker"])] = _safe(
                        r["close"],
                    )

        # Extract RSI numeric from signal text.
        # Signal format: "Neutral (RSI: 45.2)" or similar.
        import re

        rsi_map: dict[str, float | None] = {}
        for _, r in df.iterrows():
            sig = str(r.get("rsi_signal", ""))
            m = re.search(r"RSI:\s*([\d.]+)", sig)
            if m:
                rsi_map[str(r["ticker"])] = float(
                    m.group(1),
                )
            else:
                rsi_map[str(r["ticker"])] = None

        # Latest sentiment per ticker.
        sent_map: dict[str, tuple[float, int]] = {}
        try:
            sent_df = query_iceberg_df(
                "stocks.sentiment_scores",
                "SELECT ticker, avg_score, "
                "headline_count FROM ("
                "  SELECT ticker, avg_score, "
                "  headline_count, "
                "  ROW_NUMBER() OVER ("
                "    PARTITION BY ticker "
                "    ORDER BY scored_at DESC"
                "  ) AS rn "
                "  FROM sentiment_scores "
                f"  WHERE ticker IN ({ph})"
                ") WHERE rn = 1",
            )
            if not sent_df.empty:
                for _, sr in sent_df.iterrows():
                    sent_map[str(sr["ticker"])] = (
                        float(sr["avg_score"]),
                        int(sr["headline_count"]),
                    )
        except Exception:
            pass

        company_df = _get_company_info_df(
            stock_repo,
            tickers,
        )
        sectors = _collect_sectors(
            company_df,
            tickers,
        )

        # Load tags from PG (stock_tags + stock_master).
        tags_map: dict[str, list[str]] = {}
        all_tags: set[str] = set()
        try:
            from sqlalchemy import text as _text

            async def _load_tags():
                from backend.db.engine import (
                    get_session_factory,
                )

                async with get_session_factory()() as s:
                    r = await s.execute(
                        _text(
                            "SELECT sm.yf_ticker, "
                            "st.tag "
                            "FROM stock_tags st "
                            "JOIN stock_master sm "
                            "ON st.stock_id = sm.id "
                            "WHERE st.removed_at "
                            "IS NULL",
                        )
                    )
                    for row in r.fetchall():
                        tk = str(row[0])
                        tg = str(row[1])
                        tags_map.setdefault(
                            tk, [],
                        ).append(tg)
                        all_tags.add(tg)

            await _load_tags()
        except Exception:
            _logger.debug(
                "Tags load failed",
                exc_info=True,
            )

        rows: list[ScreenerRow] = []
        for _, row in df.iterrows():
            t = str(row["ticker"])
            s_tup = sent_map.get(t)
            rows.append(
                ScreenerRow(
                    ticker=t,
                    price=price_map.get(t),
                    rsi_14=rsi_map.get(t),
                    rsi_signal=str(row.get("rsi_signal", "")) or None,
                    macd_signal=str(row.get("macd_signal_text", "")) or None,
                    sma_200_signal=str(row.get("sma_200_signal", "")) or None,
                    sentiment_score=(
                        s_tup[0] if s_tup else None
                    ),
                    sentiment_headlines=(
                        s_tup[1] if s_tup else None
                    ),
                    annualized_return_pct=_safe(
                        row.get("annualized_return_pct")
                    ),
                    annualized_volatility_pct=_safe(
                        row.get("annualized_volatility_pct")
                    ),
                    sharpe_ratio=_safe(row.get("sharpe_ratio")),
                    sector=_sector_for_ticker(
                        t,
                        company_df,
                    ),
                    market=_market(t),
                    tags=tags_map.get(t, []),
                )
            )

        result = ScreenerResponse(
            rows=rows,
            sectors=sectors,
            tags=sorted(all_tags),
        )
        cache.set(
            ck,
            result.model_dump_json(),
            TTL_STABLE,
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
        ck = f"cache:insights:targets:" f"{user.user_id}"
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
            from backend.db.duckdb_engine import (
                query_iceberg_df,
            )

            ph = ",".join(f"'{t}'" for t in tickers)
            df = query_iceberg_df(
                "stocks.forecast_runs",
                "SELECT ticker, horizon_months, "
                "run_date, "
                "current_price_at_run, "
                "target_3m_price, "
                "target_3m_pct_change, "
                "target_6m_price, "
                "target_6m_pct_change, "
                "target_9m_price, "
                "target_9m_pct_change, "
                "sentiment, "
                "confidence_score, "
                "confidence_components "
                "FROM forecast_runs "
                f"WHERE ticker IN ({ph})",
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
            subset=dedup_cols,
            keep="last",
        )

        company_df = _get_company_info_df(
            stock_repo,
            tickers,
        )
        sectors = _collect_sectors(
            company_df,
            tickers,
        )

        rows: list[TargetRow] = []
        for _, row in df.iterrows():
            t = str(row["ticker"])
            rows.append(
                TargetRow(
                    ticker=t,
                    horizon_months=_safe_int(row.get("horizon_months")),
                    run_date=str(row.get("run_date", "")) or None,
                    current_price=_safe(row.get("current_price_at_run")),
                    target_3m_price=_safe(row.get("target_3m_price")),
                    target_3m_pct=_safe(row.get("target_3m_pct_change")),
                    target_6m_price=_safe(row.get("target_6m_price")),
                    target_6m_pct=_safe(row.get("target_6m_pct_change")),
                    target_9m_price=_safe(row.get("target_9m_price")),
                    target_9m_pct=_safe(row.get("target_9m_pct_change")),
                    sentiment=str(row.get("sentiment", "")) or None,
                    confidence_score=_safe(
                        row.get("confidence_score")
                    ),
                    confidence_components=_parse_confidence(
                        row.get("confidence_components")
                    ),
                    market=_market(t),
                    sector=_sector_for_ticker(
                        t,
                        company_df,
                    ),
                )
            )

        result = TargetsResponse(
            rows=rows,
            tickers=sorted(df["ticker"].unique().tolist()),
            sectors=sectors,
        )
        cache.set(
            ck,
            result.model_dump_json(),
            TTL_STABLE,
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
        ck = f"cache:insights:dividends:" f"{user.user_id}"
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
            from backend.db.duckdb_engine import (
                query_iceberg_df,
            )

            placeholders = ",".join(
                f"'{t}'" for t in tickers
            )
            cutoff = (
                pd.Timestamp.now()
                - pd.DateOffset(years=2)
            ).strftime("%Y-%m-%d")
            df = query_iceberg_df(
                "stocks.dividends",
                "SELECT ticker, ex_date, "
                "dividend_amount, currency "
                "FROM dividends "
                f"WHERE ticker IN ({placeholders}) "
                f"AND ex_date >= '{cutoff}' "
                "ORDER BY ex_date DESC",
            )
        except Exception as exc:
            _logger.error("dividends read: %s", exc)
            return DividendsResponse()

        if df.empty:
            return DividendsResponse()

        company_df = _get_company_info_df(
            stock_repo,
            tickers,
        )
        sectors = _collect_sectors(
            company_df,
            tickers,
        )

        rows: list[DividendRow] = []
        for _, row in df.iterrows():
            t = str(row["ticker"])
            rows.append(
                DividendRow(
                    ticker=t,
                    ex_date=str(row.get("ex_date", "")) or None,
                    amount=_safe(row.get("dividend_amount")),
                    currency=str(row.get("currency", "USD")) or "USD",
                    market=_market(t),
                    sector=_sector_for_ticker(
                        t,
                        company_df,
                    ),
                )
            )

        result = DividendsResponse(
            rows=rows,
            tickers=sorted(df["ticker"].unique().tolist()),
            sectors=sectors,
        )
        cache.set(
            ck,
            result.model_dump_json(),
            TTL_STABLE,
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
        ck = f"cache:insights:risk:{user.user_id}"
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
            from backend.db.duckdb_engine import (
                query_iceberg_df,
            )

            ph = ",".join(f"'{t}'" for t in tickers)
            df = query_iceberg_df(
                "stocks.analysis_summary",
                "SELECT ticker, "
                "annualized_return_pct, "
                "annualized_volatility_pct, "
                "sharpe_ratio, "
                "max_drawdown_pct, "
                "max_drawdown_duration_days, "
                "bull_phase_pct, "
                "bear_phase_pct "
                "FROM analysis_summary "
                f"WHERE ticker IN ({ph})",
            )
        except Exception as exc:
            _logger.error("risk read: %s", exc)
            return RiskResponse()

        if df.empty:
            return RiskResponse()

        company_df = _get_company_info_df(
            stock_repo,
            tickers,
        )
        sectors = _collect_sectors(
            company_df,
            tickers,
        )

        rows: list[RiskRow] = []
        for _, row in df.iterrows():
            t = str(row["ticker"])
            rows.append(
                RiskRow(
                    ticker=t,
                    annualized_return_pct=_safe(
                        row.get("annualized_return_pct")
                    ),
                    annualized_volatility_pct=_safe(
                        row.get("annualized_volatility_pct")
                    ),
                    sharpe_ratio=_safe(row.get("sharpe_ratio")),
                    max_drawdown_pct=_safe(row.get("max_drawdown_pct")),
                    max_drawdown_days=_safe_int(
                        row.get("max_drawdown_duration_days")
                    ),
                    bull_phase_pct=_safe(row.get("bull_phase_pct")),
                    bear_phase_pct=_safe(row.get("bear_phase_pct")),
                    market=_market(t),
                    sector=_sector_for_ticker(
                        t,
                        company_df,
                    ),
                )
            )

        result = RiskResponse(
            rows=rows,
            sectors=sectors,
        )
        cache.set(
            ck,
            result.model_dump_json(),
            TTL_STABLE,
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
        ck = f"cache:insights:sectors:" f"{user.user_id}:{market}"
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
            from backend.db.duckdb_engine import (
                query_iceberg_df,
            )

            ph = ",".join(f"'{t}'" for t in tickers)
            analysis_df = query_iceberg_df(
                "stocks.analysis_summary",
                "SELECT ticker, "
                "annualized_return_pct, "
                "sharpe_ratio, "
                "annualized_volatility_pct "
                "FROM analysis_summary "
                f"WHERE ticker IN ({ph})",
            )
            company_df = query_iceberg_df(
                "stocks.company_info",
                "SELECT ticker, sector "
                "FROM company_info "
                f"WHERE ticker IN ({ph})",
            )
        except Exception as exc:
            _logger.error("sectors read: %s", exc)
            return SectorsResponse()

        if analysis_df.empty or company_df.empty:
            return SectorsResponse()

        # Market filter.
        if market == "india":
            analysis_df = analysis_df[
                analysis_df["ticker"].str.endswith((".NS", ".BO"))
            ]
        elif market == "us":
            analysis_df = analysis_df[
                ~analysis_df["ticker"].str.endswith((".NS", ".BO"))
            ]

        if analysis_df.empty:
            return SectorsResponse()

        # Latest company_info per ticker for sector.
        if "fetched_at" in company_df.columns:
            company_df = company_df.sort_values(
                "fetched_at",
            )
        company_df = company_df.drop_duplicates(
            subset=["ticker"],
            keep="last",
        )
        sector_map = company_df.set_index("ticker")["sector"].to_dict()

        # Join sector onto analysis.
        analysis_df["sector"] = analysis_df["ticker"].map(sector_map)
        analysis_df = analysis_df.dropna(
            subset=["sector"],
        )
        if analysis_df.empty:
            return SectorsResponse()

        # Aggregate per sector.
        grouped = analysis_df.groupby("sector").agg(
            stock_count=("ticker", "count"),
            avg_return=("annualized_return_pct", "mean"),
            avg_sharpe=("sharpe_ratio", "mean"),
            avg_vol=("annualized_volatility_pct", "mean"),
        )
        grouped = grouped.sort_values(
            "avg_return",
            ascending=False,
        )

        rows: list[SectorRow] = []
        for sector, agg in grouped.iterrows():
            rows.append(
                SectorRow(
                    sector=str(sector),
                    stock_count=int(agg["stock_count"]),
                    avg_return_pct=_safe(agg["avg_return"]),
                    avg_sharpe=_safe(agg["avg_sharpe"]),
                    avg_volatility_pct=_safe(agg["avg_vol"]),
                )
            )

        result = SectorsResponse(rows=rows)
        cache.set(
            ck,
            result.model_dump_json(),
            TTL_STABLE,
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
            description=("'portfolio' or 'watchlist'"),
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
            holdings_df = stock_repo.get_portfolio_holdings(
                user.user_id,
            )
            if holdings_df.empty:
                tickers = []
            else:
                tickers = list(
                    holdings_df["ticker"].astype(str).unique(),
                )
        else:
            tickers = await _get_user_tickers(user)

        if not tickers:
            return CorrelationResponse(period=period)

        # Market filter on tickers — check registry too.
        if market in ("india", "us"):
            reg = stock_repo.get_all_registry()
            tickers = [
                t
                for t in tickers
                if _market(
                    t,
                    reg.get(t, {}).get("market"),
                )
                == market
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

        # Batch OHLCV via DuckDB.
        try:
            from backend.db.duckdb_engine import (
                query_iceberg_df,
            )

            ph = ",".join(f"'{t}'" for t in tickers)
            cutoff_sql = (
                f"AND date >= '{cutoff.strftime('%Y-%m-%d')}'"
                if cutoff else ""
            )
            all_ohlcv = query_iceberg_df(
                "stocks.ohlcv",
                "SELECT ticker, date, close "
                "FROM ohlcv "
                f"WHERE ticker IN ({ph}) "
                f"{cutoff_sql} "
                "ORDER BY ticker, date",
            )
        except Exception:
            all_ohlcv = pd.DataFrame()
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
                    all_ohlcv["date"] >= pd.Timestamp(cutoff)
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
                tickers=valid,
                period=period,
            )

        # Align on common length.
        ret_df = pd.DataFrame(returns)
        corr = ret_df.corr().values
        matrix = [
            [round(float(v), 4) if not np.isnan(v) else 0.0 for v in row]
            for row in corr
        ]

        result = CorrelationResponse(
            tickers=valid,
            matrix=matrix,
            period=period,
        )
        cache.set(
            ck,
            result.model_dump_json(),
            TTL_STABLE,
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
            description=("'income', 'balance', or 'cashflow'"),
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """Quarterly financial results."""
        cache = get_cache()
        ck = f"cache:insights:quarterly:" f"{user.user_id}:{statement_type}"
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
            from backend.db.duckdb_engine import (
                query_iceberg_df,
            )

            ph = ",".join(f"'{t}'" for t in tickers)
            cutoff = (
                pd.Timestamp.now()
                - pd.DateOffset(years=2)
            ).strftime("%Y-%m-%d")
            st_filter = (
                f"AND statement_type = "
                f"'{statement_type}'"
                if statement_type != "all"
                else ""
            )
            df = query_iceberg_df(
                "stocks.quarterly_results",
                "SELECT ticker, fiscal_quarter, "
                "fiscal_year, quarter_end, "
                "statement_type, revenue, "
                "net_income, eps_basic AS eps, "
                "total_assets, total_equity, "
                "operating_cashflow, "
                "free_cashflow "
                "FROM quarterly_results "
                f"WHERE ticker IN ({ph}) "
                f"AND quarter_end >= '{cutoff}' "
                f"{st_filter} "
                "ORDER BY quarter_end DESC",
            )
        except Exception as exc:
            _logger.error("quarterly read: %s", exc)
            return QuarterlyResponse()

        if df.empty:
            return QuarterlyResponse()

        # statement_type filter pushed to DuckDB SQL.

        # Deduplicate per (ticker, quarter_end).
        if "quarter_end" in df.columns:
            df = df.sort_values("quarter_end")
        dedup = ["ticker"]
        if "quarter_end" in df.columns:
            dedup.append("quarter_end")
        df = df.drop_duplicates(
            subset=dedup,
            keep="last",
        )

        company_df = _get_company_info_df(
            stock_repo,
            tickers,
        )
        sectors = _collect_sectors(
            company_df,
            tickers,
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
                    quarter_end=str(row.get("quarter_end", "")) or None,
                    statement_type=str(row.get("statement_type", "")) or None,
                    revenue=_safe(row.get("revenue")),
                    net_income=_safe(row.get("net_income")),
                    eps=_safe(row.get("eps")),
                    total_assets=_safe(row.get("total_assets")),
                    total_equity=_safe(row.get("total_equity")),
                    operating_cashflow=_safe(row.get("operating_cashflow")),
                    free_cashflow=_safe(row.get("free_cashflow")),
                    market=_market(t),
                    sector=_sector_for_ticker(
                        t,
                        company_df,
                    ),
                )
            )

        result = QuarterlyResponse(
            rows=rows,
            tickers=sorted(df["ticker"].unique().tolist()),
            sectors=sectors,
        )
        cache.set(
            ck,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result

    # -----------------------------------------------------------
    # Tab 8: Piotroski F-Score
    # -----------------------------------------------------------

    @router.get(
        "/piotroski",
        response_model=PiotroskiResponse,
    )
    async def get_piotroski(
        min_score: int = Query(0, ge=0, le=9),
        sector: str = Query("all"),
        market: str = Query("all"),
        user: UserContext = Depends(
            get_current_user,
        ),
    ):
        """Return latest Piotroski F-Score results."""
        cache = get_cache()
        ck = (
            f"cache:insights:piotroski:"
            f"{min_score}:{sector}:{market}"
        )
        hit = cache.get(ck)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        try:
            from backend.db.duckdb_engine import (
                query_iceberg_df,
            )

            df = query_iceberg_df(
                "stocks.piotroski_scores",
                "SELECT * FROM piotroski_scores",
            )
        except Exception:
            repo = _get_stock_repo()
            df = repo.get_piotroski_scores()

        if df.empty:
            return PiotroskiResponse()

        # Patch empty company_name from company_info,
        # then stock_master PG as final fallback
        empty_mask = (
            df["company_name"].isna()
            | (df["company_name"] == "")
            | (df["company_name"] == "None")
        )
        if empty_mask.any():
            try:
                ci = query_iceberg_df(
                    "stocks.company_info",
                    "SELECT ticker, company_name, "
                    "ROW_NUMBER() OVER ("
                    "  PARTITION BY ticker "
                    "  ORDER BY fetched_at DESC"
                    ") AS rn "
                    "FROM company_info",
                )
                ci = ci[ci["rn"] == 1].set_index(
                    "ticker",
                )
                for idx in df[empty_mask].index:
                    tk = df.at[idx, "ticker"]
                    if tk in ci.index:
                        name = ci.at[tk, "company_name"]
                        if name and str(name) not in (
                            "", "None",
                        ):
                            df.at[
                                idx, "company_name"
                            ] = name
            except Exception:
                pass

        # Re-check: stock_master PG fallback for
        # tickers still missing names
        empty_mask = (
            df["company_name"].isna()
            | (df["company_name"] == "")
            | (df["company_name"] == "None")
        )
        if empty_mask.any():
            try:
                from sqlalchemy import select

                from backend.db.engine import (
                    get_session_factory,
                )
                from backend.db.models.stock_master import (
                    StockMaster,
                )

                missing = df.loc[
                    empty_mask, "ticker"
                ].tolist()
                async with (
                    get_session_factory()() as s
                ):
                    rows = (
                        await s.execute(
                            select(
                                StockMaster.yf_ticker,
                                StockMaster.name,
                            ).where(
                                StockMaster.yf_ticker.in_(
                                    missing,
                                ),
                            ),
                        )
                    ).all()
                sm_map = {
                    r.yf_ticker: r.name
                    for r in rows
                }
                for idx in df[empty_mask].index:
                    tk = df.at[idx, "ticker"]
                    if tk in sm_map and sm_map[tk]:
                        df.at[
                            idx, "company_name"
                        ] = sm_map[tk]
            except Exception:
                _logger.debug(
                    "stock_master name fallback "
                    "failed for piotroski",
                    exc_info=True,
                )

        latest_date = (
            df["score_date"].max()
            if "score_date" in df.columns
            else ""
        )

        # Apply filters.
        if market == "india":
            df = df[
                df["ticker"].str.endswith(
                    (".NS", ".BO"),
                )
            ]
        elif market == "us":
            df = df[
                ~df["ticker"].str.endswith(
                    (".NS", ".BO"),
                )
            ]
        if min_score > 0:
            df = df[df["total_score"] >= min_score]
        if sector != "all":
            df = df[df["sector"] == sector]

        rows: list[PiotroskiRow] = []
        for _, r in df.iterrows():
            rows.append(
                PiotroskiRow(
                    ticker=r["ticker"],
                    company_name=_safe_str(
                        r.get("company_name"),
                    ),
                    total_score=int(r.get("total_score", 0)),
                    label=_safe_str(
                        r.get("label"),
                    )
                    or "Weak",
                    roa_positive=bool(r.get("roa_positive", False)),
                    operating_cf_positive=bool(
                        r.get(
                            "operating_cf_positive",
                            False,
                        )
                    ),
                    roa_increasing=bool(r.get("roa_increasing", False)),
                    cf_gt_net_income=bool(
                        r.get(
                            "cf_gt_net_income",
                            False,
                        )
                    ),
                    leverage_decreasing=bool(
                        r.get(
                            "leverage_decreasing",
                            False,
                        )
                    ),
                    current_ratio_increasing=bool(
                        r.get(
                            "current_ratio_" "increasing",
                            False,
                        )
                    ),
                    no_dilution=bool(r.get("no_dilution", False)),
                    gross_margin_increasing=bool(
                        r.get(
                            "gross_margin_" "increasing",
                            False,
                        )
                    ),
                    asset_turnover_increasing=bool(
                        r.get(
                            "asset_turnover_" "increasing",
                            False,
                        )
                    ),
                    market_cap=_safe_int(r.get("market_cap")),
                    revenue=_safe(r.get("revenue")),
                    avg_volume=_safe_int(r.get("avg_volume")),
                    sector=_safe_str(r.get("sector")),
                    industry=_safe_str(
                        r.get("industry"),
                    ),
                    score_date=str(latest_date),
                )
            )

        # Unique sectors for filter dropdown.
        all_sectors = sorted({r.sector for r in rows if r.sector})

        result = PiotroskiResponse(
            rows=rows,
            sectors=all_sectors,
            score_date=str(latest_date),
        )
        cache.set(
            ck,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result

    # -----------------------------------------------------------
    # ScreenQL — universal screener
    # -----------------------------------------------------------

    @router.get(
        "/screen/fields",
        response_model=ScreenFieldsResponse,
    )
    async def get_screen_fields():
        """Return field catalog for autocomplete."""
        from backend.insights.screen_parser import (
            get_field_catalog_json,
        )

        return ScreenFieldsResponse(
            fields=get_field_catalog_json(),
        )

    @router.post(
        "/screen",
        response_model=ScreenQLResponse,
    )
    async def run_screen(
        req: ScreenQLRequest,
        user: UserContext = Depends(
            get_current_user,
        ),
    ):
        """Execute a ScreenQL query."""
        from hashlib import sha256

        from backend.insights.screen_parser import (
            ScreenQLError,
            generate_sql,
            parse_query,
        )

        # Cache check
        cache = get_cache()
        ck = (
            "cache:insights:screenql:"
            + sha256(
                f"{req.query}:{req.page}:"
                f"{req.page_size}:"
                f"{req.sort_by}:"
                f"{req.sort_dir}:"
                f"{user.user_id}".encode()
            ).hexdigest()[:16]
        )
        if cache:
            hit = cache.get(ck)
            if hit:
                return ScreenQLResponse.model_validate_json(
                    hit,
                )

        # Parse query
        try:
            ast = parse_query(req.query)
        except ScreenQLError as e:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=422,
                detail=str(e),
            )

        # User scope
        tickers = await _get_user_tickers(user)

        # Generate SQL
        gen = generate_sql(
            ast,
            page=req.page,
            page_size=req.page_size,
            sort_by=req.sort_by,
            sort_dir=req.sort_dir,
            ticker_filter=(
                tickers if tickers else None
            ),
        )

        # Execute via DuckDB
        try:
            from backend.db.duckdb_engine import (
                query_iceberg_multi,
            )

            # Map table aliases to Iceberg names
            alias_to_iceberg = {
                "ci": "stocks.company_info",
                "as_": "stocks.analysis_summary",
                "ps": "stocks.piotroski_scores",
                "fr": "stocks.forecast_runs",
                "ss": "stocks.sentiment_scores",
                "qr": "stocks.quarterly_results",
            }
            tables = [
                alias_to_iceberg[a]
                for a in gen.tables_used
                if a in alias_to_iceberg
            ]
            rows = query_iceberg_multi(
                tables,
                gen.sql,
                params=gen.params,
            )
            count_rows = query_iceberg_multi(
                tables,
                gen.count_sql,
                params=gen.params[
                    : -2  # exclude LIMIT/OFFSET
                ],
            )
            total = (
                count_rows[0]["cnt"]
                if count_rows
                else 0
            )
        except Exception:
            _logger.exception(
                "ScreenQL query failed",
            )
            rows = []
            total = 0

        # Sanitize rows
        clean: list[dict] = []
        for r in rows:
            d: dict = {}
            for k, v in r.items():
                if k == "rn":
                    continue
                if v is None:
                    d[k] = None
                elif isinstance(v, float):
                    d[k] = (
                        None
                        if pd.isna(v)
                        else round(v, 4)
                    )
                else:
                    d[k] = v
            clean.append(d)

        # Patch blank company_name from stock_master
        missing = [
            d["ticker"]
            for d in clean
            if not d.get("company_name")
            and d.get("ticker")
        ]
        if missing:
            try:
                from sqlalchemy import select

                from backend.db.engine import (
                    get_session_factory,
                )
                from backend.db.models.stock_master import (
                    StockMaster,
                )

                async with (
                    get_session_factory()() as s
                ):
                    sm_rows = (
                        await s.execute(
                            select(
                                StockMaster.yf_ticker,
                                StockMaster.name,
                            ).where(
                                StockMaster.yf_ticker.in_(
                                    missing,
                                ),
                            ),
                        )
                    ).all()
                sm_map = {
                    r.yf_ticker: r.name
                    for r in sm_rows
                }
                for d in clean:
                    tk = d.get("ticker")
                    if (
                        not d.get("company_name")
                        and tk in sm_map
                    ):
                        d["company_name"] = (
                            sm_map[tk]
                        )
            except Exception:
                _logger.debug(
                    "stock_master fallback "
                    "failed for screenql",
                    exc_info=True,
                )

        result = ScreenQLResponse(
            rows=clean,
            total=int(total),
            page=req.page,
            page_size=req.page_size,
            columns_used=gen.columns_used,
            excluded_null_count=max(
                0, int(total) - len(clean),
            ),
        )

        if cache:
            cache.set(
                ck,
                result.model_dump_json(),
                TTL_STABLE,
            )

        return result

    return router
