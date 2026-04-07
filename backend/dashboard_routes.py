"""Dashboard API endpoints.

Provides aggregated data for the native Next.js dashboard
widgets: watchlist, forecasts, analysis signals, and LLM
usage summary.  Queries the Iceberg data layer via
:class:`~stocks.repository.StockRepository`.

Responses are cached in Redis (write-through) with per-key
TTL.  Cache keys use the ``cache:dash:`` prefix and are
invalidated by :mod:`stocks.repository` on Iceberg writes.
"""

import logging
import os
import threading

from cache import TTL_STABLE, TTL_VOLATILE, get_cache
from dashboard_models import (
    AnalysisResponse,
    CompareMetric,
    CompareResponse,
    CompareSeriesItem,
    DashboardHomeResponse,
    ForecastPoint,
    ForecastSeriesResponse,
    ForecastsResponse,
    ForecastTarget,
    IndicatorPoint,
    IndicatorsResponse,
    LLMUsageResponse,
    ModelUsage,
    OHLCVPoint,
    OHLCVResponse,
    PortfolioDailyPoint,
    PortfolioForecastPoint,
    PortfolioForecastResponse,
    PortfolioMetrics,
    PortfolioPerformanceResponse,
    RegistryResponse,
    RegistryTicker,
    SignalInfo,
    TickerAnalysis,
    TickerForecast,
    TickerPrice,
    WatchlistResponse,
)
from fastapi import APIRouter, Depends, Query, Response

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_current_user
from auth.models import UserContext

_logger = logging.getLogger(__name__)


def _get_stock_repo():
    """Return the process-wide StockRepository."""
    from tools._stock_shared import _require_repo

    return _require_repo()


def _backfill_company_info(tickers, repo) -> None:
    """Background: fetch company info for tickers missing
    from the ``company_info`` Iceberg table."""
    try:
        import yfinance as yf

        for ticker in tickers:
            try:
                info = yf.Ticker(ticker).info
                if info:
                    repo.insert_company_info(ticker, info)
                    _logger.info(
                        "Backfilled company info: %s",
                        ticker,
                    )
            except Exception:
                _logger.debug(
                    "Backfill failed: %s",
                    ticker,
                    exc_info=True,
                )
    except Exception:
        _logger.debug(
            "Backfill batch failed",
            exc_info=True,
        )


def _set_cache_header(response: Response):
    """Router dependency: set Cache-Control on all."""
    yield
    response.headers["Cache-Control"] = "private, max-age=60"


def create_dashboard_router() -> APIRouter:
    """Build the ``/dashboard`` router."""
    router = APIRouter(
        prefix="/dashboard",
        tags=["dashboard"],
        dependencies=[Depends(_set_cache_header)],
    )

    @router.get(
        "/watchlist",
        response_model=WatchlistResponse,
    )
    async def get_watchlist(
        user: UserContext = Depends(get_current_user),
    ):
        """User's linked tickers + latest prices."""
        cache = get_cache()
        cache_key = f"cache:dash:watchlist:{user.user_id}"
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        repo = _helpers._get_repo()
        stock_repo = _get_stock_repo()
        tickers = await repo.get_user_tickers(user.user_id)

        if not tickers:
            return WatchlistResponse()

        # Batch fetch: 2 queries instead of 2N
        ohlcv_df = stock_repo.get_ohlcv_batch(tickers)
        info_df = stock_repo.get_company_info_batch(
            tickers,
        )

        # Index company info by ticker for O(1) lookup
        info_map: dict = {}
        if not info_df.empty:
            for _, row in info_df.iterrows():
                info_map[row["ticker"]] = row.to_dict()

        items: list[TickerPrice] = []
        total_value = 0.0
        total_prev = 0.0

        for t in tickers:
            t_ohlcv = (
                ohlcv_df[ohlcv_df["ticker"] == t]
                if not ohlcv_df.empty
                else ohlcv_df
            )

            if t_ohlcv.empty:
                continue

            # Keep last 30 rows for sparkline
            t_ohlcv = t_ohlcv.tail(30)
            t_valid = t_ohlcv.dropna(subset=["close"])
            if t_valid.empty:
                continue
            latest = t_valid.iloc[-1]
            cur = float(latest.get("close", 0))
            prev = float(
                t_valid.iloc[-2]["close"] if len(t_valid) > 1 else cur
            )
            chg = cur - prev
            pct = (chg / prev * 100) if prev else 0.0

            sparkline = t_valid["close"].tolist()[-30:]

            info = info_map.get(t)
            ccy = info.get("currency", "USD") if info else "USD"
            mkt = detect_market(t)

            items.append(
                TickerPrice(
                    ticker=t,
                    company_name=(info.get("company_name") if info else None),
                    current_price=round(cur, 2),
                    previous_close=round(prev, 2),
                    change=round(chg, 2),
                    change_pct=round(pct, 2),
                    currency=str(ccy or "USD"),
                    market=mkt,
                    sparkline=[round(float(v), 2) for v in sparkline],
                )
            )
            total_value += cur
            total_prev += prev

        daily_chg = total_value - total_prev
        daily_pct = (daily_chg / total_prev * 100) if total_prev else 0.0

        result = WatchlistResponse(
            tickers=items,
            portfolio_value=round(total_value, 2),
            daily_change=round(daily_chg, 2),
            daily_change_pct=round(daily_pct, 2),
        )
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_VOLATILE,
        )
        return result

    @router.get(
        "/forecasts/summary",
        response_model=ForecastsResponse,
    )
    async def get_forecasts_summary(
        user: UserContext = Depends(get_current_user),
        ticker: str | None = Query(
            None,
            description=(
                "Include this ticker even if unlinked"
            ),
        ),
    ):
        """Latest forecast runs per linked ticker.

        If *ticker* is provided, its forecast is included
        even when the ticker is not in the user's watchlist.
        """
        cache = get_cache()
        cache_key = (
            f"cache:dash:forecasts:{user.user_id}"
            f":{ticker or ''}"
        )
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        repo = _helpers._get_repo()
        stock_repo = _get_stock_repo()
        tickers = await repo.get_user_tickers(
            user.user_id,
        )

        if not tickers:
            tickers = []

        # Include the requested ticker even if unlinked
        if ticker and isinstance(ticker, str):
            t_upper = ticker.upper().strip()
            if t_upper not in [
                t.upper() for t in tickers
            ]:
                tickers = list(tickers) + [t_upper]

        if not tickers:
            return ForecastsResponse()

        df = stock_repo.get_dashboard_forecast_runs(
            tickers,
        )
        if df.empty:
            return ForecastsResponse()

        forecasts: list[TickerForecast] = []
        for _, row in df.iterrows():
            targets: list[ForecastTarget] = []
            for h in (3, 6, 9):
                date_col = f"target_{h}m_date"
                price_col = f"target_{h}m_price"
                if date_col in row and row[date_col]:
                    targets.append(
                        ForecastTarget(
                            horizon_months=h,
                            target_date=str(row[date_col]),
                            target_price=float(row.get(price_col, 0)),
                            pct_change=float(
                                row.get(
                                    f"target_{h}m_pct_change",
                                    0,
                                )
                            ),
                            lower_bound=float(
                                row.get(
                                    f"target_{h}m_lower",
                                    0,
                                )
                            ),
                            upper_bound=float(
                                row.get(
                                    f"target_{h}m_upper",
                                    0,
                                )
                            ),
                        )
                    )

            forecasts.append(
                TickerForecast(
                    ticker=str(row["ticker"]),
                    run_date=str(row.get("run_date", "")),
                    current_price=float(row.get("current_price_at_run", 0)),
                    sentiment=str(row.get("sentiment", "")) or None,
                    targets=targets,
                    mae=_safe(row.get("mae")),
                    rmse=_safe(row.get("rmse")),
                    mape=_safe(row.get("mape")),
                )
            )

        result = ForecastsResponse(forecasts=forecasts)
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result

    @router.get(
        "/analysis/latest",
        response_model=AnalysisResponse,
    )
    async def get_analysis_latest(
        user: UserContext = Depends(get_current_user),
    ):
        """Latest analysis summaries + signals."""
        cache = get_cache()
        cache_key = f"cache:dash:analysis:{user.user_id}"
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        repo = _helpers._get_repo()
        stock_repo = _get_stock_repo()
        tickers = await repo.get_user_tickers(user.user_id)

        if not tickers:
            return AnalysisResponse()

        df = stock_repo.get_dashboard_analysis(tickers)
        if df.empty:
            return AnalysisResponse()

        # Batch fetch indicators: 1 query instead of N
        ti_df = stock_repo.get_technical_indicators_batch(tickers)
        # Build map: ticker -> latest indicator vals
        ti_map: dict = {}
        if not ti_df.empty:
            for ticker_val, grp in ti_df.groupby("ticker"):
                latest = grp.iloc[-1]
                ti_map[ticker_val] = {
                    "rsi_14": latest.get("rsi_14"),
                    "macd": latest.get("macd"),
                    "sma_50": latest.get("sma_50"),
                    "sma_200": latest.get("sma_200"),
                }

        analyses: list[TickerAnalysis] = []
        for _, row in df.iterrows():
            ticker = str(row["ticker"])
            ti_vals = ti_map.get(ticker, {})

            signals: list[SignalInfo] = []
            _add_signal(
                signals,
                row,
                "rsi_signal",
                "RSI 14",
                "rsi_14",
                ti_vals,
            )
            _add_signal(
                signals,
                row,
                "macd_signal_text",
                "MACD",
                "macd",
                ti_vals,
            )
            _add_signal(
                signals,
                row,
                "sma_50_signal",
                "SMA 50",
                "sma_50",
                ti_vals,
            )
            _add_signal(
                signals,
                row,
                "sma_200_signal",
                "SMA 200",
                "sma_200",
                ti_vals,
            )

            analyses.append(
                TickerAnalysis(
                    ticker=str(row["ticker"]),
                    analysis_date=str(row.get("analysis_date", "")),
                    signals=signals,
                    sharpe_ratio=_safe(row.get("sharpe_ratio")),
                    annualized_return_pct=_safe(
                        row.get("annualized_return_pct")
                    ),
                    annualized_volatility_pct=_safe(
                        row.get("annualized_volatility_pct")
                    ),
                    max_drawdown_pct=_safe(row.get("max_drawdown_pct")),
                )
            )

        result = AnalysisResponse(analyses=analyses)
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result

    @router.get(
        "/llm-usage",
        response_model=LLMUsageResponse,
    )
    async def get_llm_usage(
        user: UserContext = Depends(get_current_user),
    ):
        """LLM usage stats — own usage or all (superuser)."""
        cache = get_cache()
        cache_uid = "all" if user.role == "superuser" else user.user_id
        cache_key = f"cache:dash:llm-usage:{cache_uid}"
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        uid = None if user.role == "superuser" else user.user_id
        data = stock_repo.get_dashboard_llm_usage(
            user_id=uid,
            days=30,
        )

        # per_model is a dict: {model_name: {requests, cost}}
        per_model = data.get("per_model", {})
        total_req = int(data.get("total_requests", 0))
        models = [
            ModelUsage(
                model=name,
                provider=str(
                    info.get("provider", "") or "groq"
                ),
                request_count=int(
                    info.get("requests", 0)
                ),
                total_tokens=0,
                estimated_cost_usd=float(
                    info.get("cost", 0) or 0
                ),
            )
            for name, info in per_model.items()
        ]

        result = LLMUsageResponse(
            total_requests=total_req,
            total_cost_usd=float(data.get("total_cost", 0) or 0),
            avg_latency_ms=_safe(data.get("avg_latency_ms")),
            models=models,
            daily_trend=data.get("daily_trend", []),
        )
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_VOLATILE,
        )
        return result

    @router.get(
        "/registry",
        response_model=RegistryResponse,
    )
    async def get_registry(
        user: UserContext = Depends(get_current_user),
    ):
        """All registered tickers with company info."""
        cache = get_cache()
        cache_key = "cache:dash:registry"
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        registry = stock_repo.get_all_registry()

        # Batch fetch company info: 1 query instead
        # of M (one per registered ticker)
        reg_tickers = list(registry.keys())
        info_df = (
            stock_repo.get_company_info_batch(
                reg_tickers,
            )
            if reg_tickers
            else None
        )
        info_map: dict = {}
        if info_df is not None and not info_df.empty:
            for _, row in info_df.iterrows():
                info_map[row["ticker"]] = row.to_dict()

        # Backfill missing company info (fire-and-forget).
        _missing = [t for t in reg_tickers if t not in info_map]
        if _missing:
            threading.Thread(
                target=_backfill_company_info,
                args=(_missing, stock_repo),
                daemon=True,
            ).start()

        items: list[RegistryTicker] = []
        for ticker, meta in registry.items():
            info = info_map.get(ticker)
            mkt = detect_market(
                ticker, meta.get("market"),
            )
            ccy = "INR" if mkt == "india" else "USD"
            company = None
            price = None

            if info:
                company = info.get("company_name")
                # Market-derived ccy takes precedence for
                # Indian stocks (yfinance sometimes returns
                # USD for NSE tickers).
                if mkt != "india":
                    info_ccy = info.get("currency")
                    if info_ccy and str(info_ccy) != "nan":
                        ccy = str(info_ccy)
                raw = info.get("current_price")
                if raw is not None:
                    try:
                        p = float(raw)
                        if p == p:  # not NaN
                            price = round(p, 2)
                    except (ValueError, TypeError):
                        pass

            items.append(
                RegistryTicker(
                    ticker=ticker,
                    company_name=company,
                    market=mkt,
                    currency=ccy,
                    current_price=price,
                    last_fetch_date=meta.get("last_fetch_date", "") or None,
                )
            )

        # Enrich with OHLCV: sparkline, change, price
        try:
            ohlcv_df = stock_repo.get_ohlcv_batch(
                reg_tickers,
            )
            if ohlcv_df is not None and not ohlcv_df.empty:
                _ohlcv_map: dict[str, dict] = {}
                for t in reg_tickers:
                    t_df = ohlcv_df[
                        ohlcv_df["ticker"] == t
                    ]
                    if t_df.empty:
                        continue
                    t30 = t_df.tail(30).dropna(
                        subset=["close"],
                    )
                    if t30.empty:
                        continue
                    closes = [
                        round(float(v), 2)
                        for v in t30["close"]
                        if v == v
                    ]
                    cur = closes[-1] if closes else None
                    prev = (
                        closes[-2]
                        if len(closes) > 1
                        else cur
                    )
                    chg = (
                        round(cur - prev, 2)
                        if cur and prev
                        else None
                    )
                    pct = (
                        round(chg / prev * 100, 2)
                        if chg is not None and prev
                        else None
                    )
                    _ohlcv_map[t] = {
                        "sparkline": closes,
                        "price": cur,
                        "change": chg,
                        "change_pct": pct,
                    }
                for it in items:
                    od = _ohlcv_map.get(it.ticker)
                    if not od:
                        continue
                    it.sparkline = od["sparkline"]
                    if od["change"] is not None:
                        it.change = od["change"]
                    if od["change_pct"] is not None:
                        it.change_pct = od["change_pct"]
                    if it.current_price is None:
                        it.current_price = od["price"]
        except Exception:
            pass

        items.sort(key=lambda t: t.ticker)
        result = RegistryResponse(tickers=items)
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result

    @router.get(
        "/compare",
        response_model=CompareResponse,
    )
    async def get_compare(
        tickers: str = Query(
            ...,
            description="Comma-separated ticker symbols",
        ),
        _user: UserContext = Depends(get_current_user),
    ):
        """Normalized price comparison + correlation."""
        import hashlib

        import numpy as np
        import pandas as pd

        stock_repo = _get_stock_repo()
        symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        if len(symbols) < 2:
            return CompareResponse(tickers=symbols)

        cache = get_cache()
        tickers_hash = hashlib.md5(
            ",".join(sorted(symbols)).encode(),
        ).hexdigest()[:12]
        cache_key = f"cache:dash:compare:{tickers_hash}"
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        # Batch fetch: 4 queries instead of 4N
        ohlcv_df = stock_repo.get_ohlcv_batch(symbols)
        summary_df = stock_repo.get_analysis_summary_batch(symbols)
        info_df = stock_repo.get_company_info_batch(
            symbols,
        )
        ti_df = stock_repo.get_technical_indicators_batch(symbols)

        # Index batch results by ticker
        summary_map: dict = {}
        if not summary_df.empty:
            for _, row in summary_df.iterrows():
                summary_map[row["ticker"]] = row.to_dict()
        info_map: dict = {}
        if not info_df.empty:
            for _, row in info_df.iterrows():
                info_map[row["ticker"]] = row.to_dict()
        ti_map: dict = {}
        if not ti_df.empty:
            for t_val, grp in ti_df.groupby("ticker"):
                latest = grp.iloc[-1]
                ti_map[t_val] = latest.to_dict()

        series: list[CompareSeriesItem] = []
        metrics: list[CompareMetric] = []
        returns_map: dict[str, pd.Series] = {}

        for sym in symbols:
            ohlcv = (
                ohlcv_df[ohlcv_df["ticker"] == sym]
                if not ohlcv_df.empty
                else ohlcv_df
            )
            if ohlcv.empty or len(ohlcv) < 2:
                continue

            ohlcv = ohlcv.dropna(subset=["close"])
            if ohlcv.empty or len(ohlcv) < 2:
                continue
            close = ohlcv["close"].astype(float)
            first = close.iloc[0]
            if not first or first == 0:
                continue

            norm = (close / first * 100).tolist()
            dates = [str(d) for d in ohlcv["date"].tolist()]

            series.append(
                CompareSeriesItem(
                    ticker=sym,
                    dates=dates,
                    normalized=[round(v, 4) for v in norm],
                )
            )

            daily_ret = close.pct_change().dropna()
            returns_map[sym] = daily_ret

            summary = summary_map.get(sym)
            cur_price = round(
                float(close.iloc[-1]),
                2,
            )
            ccy = "USD"
            info = info_map.get(sym)
            if info:
                ccy = str(info.get("currency", "USD") or "USD")

            # RSI + MACD from technical indicators
            ti = ti_map.get(sym, {})
            rsi_val = _safe(ti.get("rsi_14"))
            macd_v = ti.get("macd")
            sig_v = ti.get("macd_signal")
            macd_lbl = None
            if macd_v is not None and (sig_v is not None):
                try:
                    macd_lbl = (
                        "Bullish"
                        if float(macd_v) > float(sig_v)
                        else "Bearish"
                    )
                except (ValueError, TypeError):
                    pass

            # Sentiment from summary
            sent = None
            if summary:
                rsi_sig = str(summary.get("rsi_signal", "")).lower()
                macd_sig = str(summary.get("macd_signal_text", "")).lower()
                bull = sum(
                    1
                    for s in (rsi_sig, macd_sig)
                    if "bull" in s or "above" in s
                )
                bear = sum(
                    1
                    for s in (rsi_sig, macd_sig)
                    if "bear" in s or "below" in s
                )
                if bull > bear:
                    sent = "Bullish"
                elif bear > bull:
                    sent = "Bearish"
                else:
                    sent = "Neutral"

            common = dict(
                ticker=sym,
                current_price=cur_price,
                currency=ccy,
                rsi_14=rsi_val,
                macd_signal=macd_lbl,
                sentiment=sent,
            )
            if summary:
                metrics.append(
                    CompareMetric(
                        annualized_return_pct=_safe(
                            summary.get("annualized_return_pct")
                        ),
                        annualized_volatility_pct=_safe(
                            summary.get("annualized_volatility_pct")
                        ),
                        sharpe_ratio=_safe(summary.get("sharpe_ratio")),
                        max_drawdown_pct=_safe(
                            summary.get("max_drawdown_pct")
                        ),
                        **common,
                    )
                )
            else:
                metrics.append(CompareMetric(**common))

        # Build correlation matrix
        corr_matrix: list[list[float]] = []
        valid = [s for s in symbols if s in returns_map]
        if len(valid) >= 2:
            ret_df = pd.DataFrame(
                {s: returns_map[s] for s in valid},
            )
            corr = ret_df.corr().values
            corr_matrix = [
                [round(float(v), 4) if not np.isnan(v) else 0.0 for v in row]
                for row in corr
            ]

        result = CompareResponse(
            tickers=valid,
            series=series,
            correlation=corr_matrix,
            metrics=metrics,
        )
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result

    # -------------------------------------------------------
    # Aggregate endpoint (single request for all widgets)
    # -------------------------------------------------------

    @router.get(
        "/home",
        response_model=DashboardHomeResponse,
    )
    async def get_dashboard_home(
        user: UserContext = Depends(get_current_user),
    ):
        """All dashboard widget data in one response.

        Returns watchlist, forecasts, analysis, and
        LLM usage so the frontend can render the
        entire dashboard with a single network call.
        """
        cache = get_cache()
        cache_key = f"cache:dash:home:{user.user_id}"
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        # Reuse existing endpoint functions — they
        # each check their own cache internally.
        wl = await get_watchlist(user)
        fc = await get_forecasts_summary(user, ticker=None)
        an = await get_analysis_latest(user)
        lu = await get_llm_usage(user)

        # If the sub-call returned a raw Response
        # (cache hit), parse it back to the model.
        def _ensure_model(val, cls):
            if isinstance(val, Response):
                return cls.model_validate_json(
                    val.body,
                )
            return val

        result = DashboardHomeResponse(
            watchlist=_ensure_model(
                wl,
                WatchlistResponse,
            ),
            forecasts=_ensure_model(
                fc,
                ForecastsResponse,
            ),
            analysis=_ensure_model(
                an,
                AnalysisResponse,
            ),
            llm_usage=_ensure_model(
                lu,
                LLMUsageResponse,
            ),
        )
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_VOLATILE,
        )
        return result

    # -------------------------------------------------------
    # Chart endpoints (Analysis page)
    # -------------------------------------------------------

    @router.get(
        "/chart/ohlcv",
        response_model=OHLCVResponse,
    )
    async def get_chart_ohlcv(
        ticker: str = Query(
            ...,
            description="Ticker symbol",
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """OHLCV time series for a single ticker."""
        _logger.info(
            "chart/ohlcv ticker=%s user=%s",
            ticker,
            user.user_id,
        )
        cache = get_cache()
        t_upper = ticker.upper()
        cache_key = f"cache:chart:ohlcv:{t_upper}"
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        df = stock_repo.get_ohlcv(t_upper)

        if df.empty:
            return OHLCVResponse(ticker=t_upper)

        points: list[OHLCVPoint] = []
        for _, row in df.iterrows():
            points.append(
                OHLCVPoint(
                    date=str(row["date"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                )
            )

        result = OHLCVResponse(
            ticker=t_upper,
            data=points,
        )
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result

    @router.get(
        "/chart/indicators",
        response_model=IndicatorsResponse,
    )
    async def get_chart_indicators(
        ticker: str = Query(
            ...,
            description="Ticker symbol",
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """Technical indicators time series."""
        _logger.info(
            "chart/indicators ticker=%s user=%s",
            ticker,
            user.user_id,
        )
        cache = get_cache()
        t_upper = ticker.upper()
        cache_key = f"cache:chart:indicators:{t_upper}"
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        df = stock_repo.get_technical_indicators(
            t_upper,
        )

        if df.empty:
            return IndicatorsResponse(
                ticker=t_upper,
            )

        points: list[IndicatorPoint] = []
        for _, row in df.iterrows():
            points.append(
                IndicatorPoint(
                    date=str(row.get("date", "")),
                    sma_50=_safe(row.get("sma_50")),
                    sma_200=_safe(
                        row.get("sma_200"),
                    ),
                    ema_20=_safe(row.get("ema_20")),
                    rsi_14=_safe(row.get("rsi_14")),
                    macd=_safe(row.get("macd")),
                    macd_signal=_safe(
                        row.get("macd_signal"),
                    ),
                    macd_hist=_safe(
                        row.get("macd_hist"),
                    ),
                    bb_upper=_safe(
                        row.get("bb_upper"),
                    ),
                    bb_lower=_safe(
                        row.get("bb_lower"),
                    ),
                )
            )

        result = IndicatorsResponse(
            ticker=t_upper,
            data=points,
        )
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result

    @router.get(
        "/chart/forecast-series",
        response_model=ForecastSeriesResponse,
    )
    async def get_chart_forecast_series(
        ticker: str = Query(
            ...,
            description="Ticker symbol",
        ),
        horizon: int = Query(
            9,
            description="Forecast horizon in months",
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """Forecast time series with confidence bands."""
        _logger.info(
            "chart/forecast-series ticker=%s " "horizon=%d user=%s",
            ticker,
            horizon,
            user.user_id,
        )
        cache = get_cache()
        t_upper = ticker.upper()
        cache_key = f"cache:chart:forecast:" f"{t_upper}:{horizon}"
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        stock_repo = _get_stock_repo()
        df = stock_repo.get_latest_forecast_series(
            t_upper,
            horizon,
        )

        if df.empty:
            return ForecastSeriesResponse(
                ticker=t_upper,
                horizon_months=horizon,
            )

        points: list[ForecastPoint] = []
        for _, row in df.iterrows():
            points.append(
                ForecastPoint(
                    date=str(row.get("forecast_date", "")),
                    predicted=float(row.get("predicted_price", 0)),
                    lower=float(row.get("lower_bound", 0)),
                    upper=float(row.get("upper_bound", 0)),
                )
            )

        result = ForecastSeriesResponse(
            ticker=t_upper,
            horizon_months=horizon,
            data=points,
        )
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result

    # -------------------------------------------------------
    # Per-ticker refresh (background pipeline)
    # -------------------------------------------------------

    # Process-wide RefreshManager shared across requests.
    import sys as _sys

    _project_root = (
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if "__file__" not in dir()
        else os.path.dirname(os.path.dirname(__file__))
    )
    _dash_dir = os.path.join(
        _project_root,
        "dashboard",
    )
    if _dash_dir not in _sys.path:
        _sys.path.insert(0, _dash_dir)

    from dashboard.callbacks.refresh_state import (
        RefreshManager,
    )

    _refresh_mgr = RefreshManager(max_workers=2)

    @router.post("/refresh/{ticker}")
    async def start_refresh(
        ticker: str,
        user: UserContext = Depends(get_current_user),
    ):
        """Start a background refresh for *ticker*.

        Runs the 6-step pipeline (OHLCV fetch,
        company info, dividends, technical analysis,
        quarterly results, Prophet forecast) in a
        background thread.  Returns immediately with
        ``{"status": "started"}`` or
        ``{"status": "already_running"}``.
        """
        t = ticker.upper()
        _logger.info(
            "refresh requested ticker=%s user=%s",
            t,
            user.user_id,
        )
        from dashboard.services.stock_refresh import (
            run_full_refresh,
        )

        submitted = _refresh_mgr.submit_if_idle(
            t,
            run_full_refresh,
            t,
            9,
        )
        return {
            "ticker": t,
            "status": ("started" if submitted else "already_running"),
        }

    @router.get("/refresh/{ticker}/status")
    async def refresh_status(
        ticker: str,
        _user: UserContext = Depends(get_current_user),
    ):
        """Poll refresh status for *ticker*.

        Returns ``pending``, ``success``, ``error``,
        or ``idle`` (no refresh in progress).
        """
        t = ticker.upper()
        fut = _refresh_mgr.get(t)
        if fut is None:
            return {"ticker": t, "status": "idle"}
        if not fut.done():
            return {"ticker": t, "status": "pending"}

        # Harvest result
        _refresh_mgr.pop(t)
        try:
            result = fut.result()
            # Invalidate all caches for this ticker
            cache = get_cache()
            for pattern in [
                "cache:dash:*",
                f"cache:chart:ohlcv:{t}",
                f"cache:chart:indicators:{t}",
                f"cache:chart:forecast:{t}:*",
                "cache:insights:*",
            ]:
                if "*" in pattern:
                    cache.invalidate(pattern)
                else:
                    cache.invalidate_exact(pattern)

            return {
                "ticker": t,
                "status": "success",
                "steps": (result.steps if hasattr(result, "steps") else []),
                "accuracy": (
                    result.accuracy if hasattr(result, "accuracy") else None
                ),
            }
        except Exception as exc:
            return {
                "ticker": t,
                "status": "error",
                "error": str(exc),
            }

    # -------------------------------------------------------
    # Portfolio Performance & Forecast
    # -------------------------------------------------------

    @router.get(
        "/portfolio/performance",
        response_model=PortfolioPerformanceResponse,
    )
    async def get_portfolio_performance(
        period: str = Query(
            "ALL",
            description=("1D|1W|1M|3M|6M|1Y|ALL"),
        ),
        currency: str = Query(
            "USD",
            description="USD|INR",
        ),
        user: UserContext = Depends(
            get_current_user,
        ),
    ):
        """Daily portfolio value time series."""
        cache = get_cache()
        cache_key = (
            f"cache:portfolio:perf:" f"{user.user_id}:{currency}:{period}"
        )
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        result = _build_portfolio_performance(
            user.user_id,
            currency,
            period,
        )
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_VOLATILE,
        )
        return result

    @router.get(
        "/portfolio/forecast",
        response_model=PortfolioForecastResponse,
    )
    async def get_portfolio_forecast(
        horizon: int = Query(
            9,
            description="3|6|9",
        ),
        currency: str = Query(
            "USD",
            description="USD|INR",
        ),
        user: UserContext = Depends(
            get_current_user,
        ),
    ):
        """Weighted portfolio forecast."""
        cache = get_cache()
        cache_key = (
            f"cache:portfolio:forecast:" f"{user.user_id}:{currency}:{horizon}"
        )
        hit = cache.get(cache_key)
        if hit is not None:
            return Response(
                content=hit,
                media_type="application/json",
            )

        result = _build_portfolio_forecast(
            user.user_id,
            currency,
            horizon,
        )
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result

    return router


# ----------------------------------------------------------
# Portfolio helpers
# ----------------------------------------------------------

_PERIOD_DAYS = {
    "1D": 1,
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
}


from market_utils import detect_currency, detect_market


def _is_currency_match(
    ticker: str,
    currency: str,
) -> bool:
    """True if ticker belongs to *currency*."""
    mkt = detect_market(ticker)
    return (
        (currency == "INR" and mkt == "india")
        or (currency == "USD" and mkt == "us")
    )


def _safe_float(val) -> float:
    """Convert to float; NaN / None → 0.0."""
    if val is None:
        return 0.0
    try:
        import math as _m

        f = float(val)
        return 0.0 if _m.isnan(f) else f
    except (ValueError, TypeError):
        return 0.0


def _build_portfolio_performance(
    user_id: str,
    currency: str,
    period: str,
) -> PortfolioPerformanceResponse:
    """Compute daily portfolio value series."""
    import math

    stock_repo = _get_stock_repo()

    txn_df = stock_repo.get_portfolio_transactions(
        user_id,
    )
    if txn_df.empty:
        return PortfolioPerformanceResponse(
            currency=currency,
        )

    # Filter BUY + currency
    buys = txn_df[txn_df["side"] == "BUY"].copy()
    buys = buys[
        buys["ticker"].apply(
            lambda t: _is_currency_match(
                t,
                currency,
            )
        )
    ]
    if buys.empty:
        return PortfolioPerformanceResponse(
            currency=currency,
        )

    tickers = buys["ticker"].unique().tolist()
    ohlcv_df = stock_repo.get_ohlcv_batch(tickers)
    if ohlcv_df.empty:
        return PortfolioPerformanceResponse(
            currency=currency,
        )

    # Build per-ticker close maps and lot lists
    close_maps: dict[str, dict[str, float]] = {}
    for t in tickers:
        t_df = ohlcv_df[ohlcv_df["ticker"] == t]
        if t_df.empty:
            continue
        close_maps[t] = {
            str(row["date"]): float(row["close"]) for _, row in t_df.iterrows()
        }

    # Lots: (ticker, qty, trade_date, buy_price)
    # If price is 0/NULL, use OHLCV close on
    # trade_date as fallback.
    lots: list[tuple[str, float, str, float]] = []
    for _, row in buys.iterrows():
        t = str(row["ticker"])
        if t not in close_maps:
            continue
        qty = float(row["quantity"])
        td = str(row["trade_date"])
        bp = _safe_float(row.get("price"))
        if bp <= 0:
            bp = close_maps[t].get(td, 0) or 0
        lots.append((t, qty, td, bp))

    if not lots:
        return PortfolioPerformanceResponse(
            currency=currency,
        )

    # Union of all dates across tickers
    all_dates: set[str] = set()
    for cm in close_maps.values():
        all_dates.update(cm.keys())
    sorted_dates = sorted(all_dates)

    # Compute daily portfolio value + invested
    values: list[tuple[str, float, float]] = []
    for d in sorted_dates:
        val = 0.0
        inv = 0.0
        for t, qty, td, bp in lots:
            if d < td:
                continue
            price = close_maps[t].get(d)
            if price is not None:
                val += qty * price
                inv += qty * bp
        if val > 0:
            values.append(
                (
                    d,
                    round(val, 2),
                    round(inv, 2),
                )
            )

    if not values:
        return PortfolioPerformanceResponse(
            currency=currency,
        )

    # Period filter
    cutoff_days = _PERIOD_DAYS.get(
        period.upper(),
    )
    if cutoff_days is not None:
        from datetime import datetime, timedelta

        last = datetime.strptime(
            values[-1][0],
            "%Y-%m-%d",
        )
        cutoff = (last - timedelta(days=cutoff_days)).strftime("%Y-%m-%d")
        values = [(d, v, iv) for d, v, iv in values if d >= cutoff]

    if len(values) < 2:
        return PortfolioPerformanceResponse(
            currency=currency,
        )

    # Daily P&L and returns — adjusted for
    # capital contributions (cash-flow neutral).
    points: list[PortfolioDailyPoint] = []
    returns: list[float] = []
    for i, (d, v, iv) in enumerate(values):
        if i == 0:
            points.append(
                PortfolioDailyPoint(
                    date=d,
                    value=v,
                    invested_value=iv,
                    daily_pnl=0.0,
                    daily_return_pct=0.0,
                )
            )
            continue
        prev_v = values[i - 1][1]
        prev_iv = values[i - 1][2]
        # Strip new capital added today
        cashflow = iv - prev_iv
        pnl = round(v - prev_v - cashflow, 2)
        ret = (
            round(
                (v - prev_v - cashflow) / prev_v * 100,
                4,
            )
            if prev_v
            else 0.0
        )
        returns.append(ret)
        points.append(
            PortfolioDailyPoint(
                date=d,
                value=v,
                invested_value=iv,
                daily_pnl=pnl,
                daily_return_pct=ret,
            )
        )

    # Metrics — all based on invested cost basis
    last_v = values[-1][1]
    last_iv = values[-1][2]
    total_ret = (last_v - last_iv) / last_iv * 100 if last_iv else 0.0
    n_days = len(values)
    gain_ratio = last_v / last_iv if last_iv else 1.0
    ann_ret = (
        (gain_ratio ** (252 / n_days) - 1) * 100
        if last_iv and n_days > 1
        else 0.0
    )

    # Max drawdown — on gain% series so capital
    # injections don't distort the trough.
    max_dd = 0.0
    peak_g = None
    for _, v, iv in values:
        g = (v - iv) / iv * 100 if iv else 0.0
        if peak_g is None or g > peak_g:
            peak_g = g
        dd = g - peak_g
        if dd < max_dd:
            max_dd = dd

    # Sharpe (annualized, risk-free=0)
    sharpe = None
    if returns:
        avg_r = sum(returns) / len(returns)
        if len(returns) > 1:
            var = sum((r - avg_r) ** 2 for r in returns) / (len(returns) - 1)
            std = math.sqrt(var)
            if std > 0:
                sharpe = round(
                    avg_r / std * math.sqrt(252),
                    4,
                )

    # Best / worst day
    best_i = max(
        range(1, len(points)),
        key=lambda i: points[i].daily_return_pct,
    )
    worst_i = min(
        range(1, len(points)),
        key=lambda i: points[i].daily_return_pct,
    )

    metrics = PortfolioMetrics(
        total_return_pct=round(total_ret, 2),
        annualized_return_pct=round(ann_ret, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=sharpe,
        best_day_pct=round(
            points[best_i].daily_return_pct,
            2,
        ),
        best_day_date=points[best_i].date,
        worst_day_pct=round(
            points[worst_i].daily_return_pct,
            2,
        ),
        worst_day_date=points[worst_i].date,
    )

    return PortfolioPerformanceResponse(
        data=points,
        metrics=metrics,
        currency=currency,
    )


def _build_portfolio_forecast(
    user_id: str,
    currency: str,
    horizon: int,
) -> PortfolioForecastResponse:
    """Weighted aggregation of per-ticker forecasts."""
    stock_repo = _get_stock_repo()
    holdings_df = stock_repo.get_portfolio_holdings(
        user_id,
    )
    if holdings_df.empty:
        return PortfolioForecastResponse(
            currency=currency,
            horizon_months=horizon,
        )

    # Filter by currency
    holdings_df = holdings_df[
        holdings_df["ticker"].apply(
            lambda t: _is_currency_match(
                t,
                currency,
            )
        )
    ]
    if holdings_df.empty:
        return PortfolioForecastResponse(
            currency=currency,
            horizon_months=horizon,
        )

    # Gather per-ticker forecasts + current prices
    forecasts: dict[str, list[tuple[str, float, float, float]]] = {}
    weights: dict[str, float] = {}
    current_value = 0.0
    total_invested = 0.0

    for _, row in holdings_df.iterrows():
        t = str(row["ticker"])
        qty = float(row["quantity"])
        avg_p = _safe_float(row.get("avg_price"))

        # Current price from OHLCV (skip NaN rows)
        ohlcv = stock_repo.get_ohlcv(t)
        if ohlcv.empty:
            continue
        valid = ohlcv.dropna(subset=["close"])
        if valid.empty:
            continue
        cur_price = float(valid.iloc[-1]["close"])
        current_value += qty * cur_price
        # If avg_price missing/zero, fallback to
        # current price as cost estimate
        if avg_p <= 0:
            avg_p = cur_price
        total_invested += qty * avg_p
        weights[t] = qty

        # Always fetch 9M; client truncates
        fc_df = stock_repo.get_latest_forecast_series(
            t,
            9,
        )
        if fc_df.empty:
            continue
        forecasts[t] = [
            (
                str(r["forecast_date"]),
                float(r["predicted_price"]),
                float(r["lower_bound"]),
                float(r["upper_bound"]),
            )
            for _, r in fc_df.iterrows()
        ]

    if not forecasts:
        return PortfolioForecastResponse(
            currency=currency,
            horizon_months=horizon,
            current_value=round(current_value, 2),
            total_invested=round(
                total_invested,
                2,
            ),
        )

    # Union of forecast dates, forward-fill
    all_dates: set[str] = set()
    for pts in forecasts.values():
        for d, _, _, _ in pts:
            all_dates.add(d)
    sorted_dates = sorted(all_dates)

    # Build per-ticker date maps
    fc_maps: dict[
        str,
        dict[str, tuple[float, float, float]],
    ] = {}
    for t, pts in forecasts.items():
        m: dict[str, tuple[float, float, float]] = {}
        for d, p, lo, hi in pts:
            m[d] = (p, lo, hi)
        fc_maps[t] = m

    # Aggregate
    points: list[PortfolioForecastPoint] = []
    # Track last known values for forward-fill
    last_known: dict[str, tuple[float, float, float]] = {}
    for d in sorted_dates:
        pred = 0.0
        lower = 0.0
        upper = 0.0
        for t, qty in weights.items():
            if t not in fc_maps:
                continue
            vals = fc_maps[t].get(d)
            if vals:
                last_known[t] = vals
            else:
                vals = last_known.get(t)
            if vals:
                pred += qty * vals[0]
                lower += qty * vals[1]
                upper += qty * vals[2]
        if pred > 0:
            points.append(
                PortfolioForecastPoint(
                    date=d,
                    predicted=round(pred, 2),
                    lower=round(lower, 2),
                    upper=round(upper, 2),
                )
            )

    return PortfolioForecastResponse(
        data=points,
        horizon_months=horizon,
        current_value=round(current_value, 2),
        total_invested=round(
            total_invested,
            2,
        ),
        currency=currency,
    )


# ----------------------------------------------------------
# Helpers
# ----------------------------------------------------------


def _safe(val) -> float | None:
    """Convert to float or return None."""
    if val is None:
        return None
    try:
        import math

        f = float(val)
        return None if math.isnan(f) else round(f, 4)
    except (ValueError, TypeError):
        return None


def _add_signal(
    signals: list[SignalInfo],
    row,
    signal_col: str,
    name: str,
    value_col: str,
    ti_vals: dict | None = None,
) -> None:
    """Extract a signal from an analysis row.

    Numeric values come from ``ti_vals`` (technical
    indicators) since analysis_summary only stores
    signal text, not the raw indicator numbers.
    """
    sig_text = row.get(signal_col)
    if not sig_text:
        return
    sig_str = str(sig_text)
    # Classify signal
    lower = sig_str.lower()
    if "bull" in lower or "above" in lower:
        signal = "Bullish"
    elif "bear" in lower or "below" in lower or "oversold" in lower:
        signal = "Bearish"
    elif "overbought" in lower:
        signal = "Bearish"
    else:
        signal = "Neutral"

    # Numeric from technical_indicators first,
    # then analysis_summary, then signal text.
    val = (ti_vals or {}).get(value_col) or row.get(value_col)
    try:
        display_val = str(round(float(val), 2))
    except (ValueError, TypeError):
        display_val = sig_str

    signals.append(
        SignalInfo(
            name=name,
            value=display_val,
            signal=signal,
            description=sig_str,
        )
    )
