"""Dashboard API endpoints.

Provides aggregated data for the native Next.js dashboard
widgets: watchlist, forecasts, analysis signals, and LLM
usage summary.  Queries the Iceberg data layer via
:class:`~stocks.repository.StockRepository`.
"""

import logging

from fastapi import APIRouter, Depends, Query

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_current_user
from auth.models import UserContext
from dashboard_models import (
    AnalysisResponse,
    CompareMetric,
    CompareResponse,
    CompareSeriesItem,
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
    RegistryResponse,
    RegistryTicker,
    SignalInfo,
    TickerAnalysis,
    TickerForecast,
    TickerPrice,
    WatchlistResponse,
)

_logger = logging.getLogger(__name__)


def _get_stock_repo():
    """Lazy import to avoid circular imports."""
    from stocks.repository import StockRepository

    return StockRepository()


def create_dashboard_router() -> APIRouter:
    """Build the ``/dashboard`` router."""
    router = APIRouter(
        prefix="/dashboard",
        tags=["dashboard"],
    )

    @router.get(
        "/watchlist",
        response_model=WatchlistResponse,
    )
    async def get_watchlist(
        user: UserContext = Depends(get_current_user),
    ):
        """User's linked tickers + latest prices."""
        repo = _helpers._get_repo()
        stock_repo = _get_stock_repo()
        tickers = repo.get_user_tickers(user.user_id)

        if not tickers:
            return WatchlistResponse()

        items: list[TickerPrice] = []
        total_value = 0.0
        total_prev = 0.0

        for t in tickers:
            ohlcv = stock_repo.get_dashboard_ohlcv(t, 30)
            info = stock_repo.get_dashboard_company_info(t)

            if ohlcv.empty:
                continue

            latest = ohlcv.iloc[-1]
            cur = float(latest.get("close", 0))
            prev = float(
                ohlcv.iloc[-2]["close"]
                if len(ohlcv) > 1
                else cur
            )
            chg = cur - prev
            pct = (chg / prev * 100) if prev else 0.0

            sparkline = ohlcv["close"].tolist()[-30:]

            # Currency + market from company_info
            ccy = (
                info.get("currency", "USD")
                if info
                else "USD"
            )
            mkt = "india" if (
                t.endswith(".NS") or t.endswith(".BO")
            ) else "us"

            items.append(
                TickerPrice(
                    ticker=t,
                    company_name=(
                        info.get("company_name")
                        if info
                        else None
                    ),
                    current_price=round(cur, 2),
                    previous_close=round(prev, 2),
                    change=round(chg, 2),
                    change_pct=round(pct, 2),
                    currency=str(ccy or "USD"),
                    market=mkt,
                    sparkline=[
                        round(float(v), 2)
                        for v in sparkline
                    ],
                )
            )
            total_value += cur
            total_prev += prev

        daily_chg = total_value - total_prev
        daily_pct = (
            (daily_chg / total_prev * 100)
            if total_prev
            else 0.0
        )

        return WatchlistResponse(
            tickers=items,
            portfolio_value=round(total_value, 2),
            daily_change=round(daily_chg, 2),
            daily_change_pct=round(daily_pct, 2),
        )

    @router.get(
        "/forecasts/summary",
        response_model=ForecastsResponse,
    )
    async def get_forecasts_summary(
        user: UserContext = Depends(get_current_user),
    ):
        """Latest forecast runs per linked ticker."""
        repo = _helpers._get_repo()
        stock_repo = _get_stock_repo()
        tickers = repo.get_user_tickers(user.user_id)

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
                            target_date=str(
                                row[date_col]
                            ),
                            target_price=float(
                                row.get(price_col, 0)
                            ),
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
                    current_price=float(
                        row.get("current_price_at_run", 0)
                    ),
                    sentiment=str(
                        row.get("sentiment", "")
                    ) or None,
                    targets=targets,
                    mae=_safe(row.get("mae")),
                    rmse=_safe(row.get("rmse")),
                    mape=_safe(row.get("mape")),
                )
            )

        return ForecastsResponse(forecasts=forecasts)

    @router.get(
        "/analysis/latest",
        response_model=AnalysisResponse,
    )
    async def get_analysis_latest(
        user: UserContext = Depends(get_current_user),
    ):
        """Latest analysis summaries + signals."""
        repo = _helpers._get_repo()
        stock_repo = _get_stock_repo()
        tickers = repo.get_user_tickers(user.user_id)

        if not tickers:
            return AnalysisResponse()

        df = stock_repo.get_dashboard_analysis(tickers)
        if df.empty:
            return AnalysisResponse()

        analyses: list[TickerAnalysis] = []
        for _, row in df.iterrows():
            # Fetch numeric indicator values from
            # technical_indicators (analysis_summary
            # only stores signal text, not numbers).
            ticker = str(row["ticker"])
            ti = stock_repo.get_technical_indicators(
                ticker,
            )
            ti_vals: dict = {}
            if not ti.empty:
                latest = ti.iloc[-1]
                ti_vals = {
                    "rsi_14": latest.get("rsi_14"),
                    "macd": latest.get("macd"),
                    "sma_50": latest.get("sma_50"),
                    "sma_200": latest.get("sma_200"),
                }

            signals: list[SignalInfo] = []
            _add_signal(
                signals, row, "rsi_signal",
                "RSI 14", "rsi_14",
                ti_vals,
            )
            _add_signal(
                signals, row, "macd_signal_text",
                "MACD", "macd",
                ti_vals,
            )
            _add_signal(
                signals, row, "sma_50_signal",
                "SMA 50", "sma_50",
                ti_vals,
            )
            _add_signal(
                signals, row, "sma_200_signal",
                "SMA 200", "sma_200",
                ti_vals,
            )

            analyses.append(
                TickerAnalysis(
                    ticker=str(row["ticker"]),
                    analysis_date=str(
                        row.get("analysis_date", "")
                    ),
                    signals=signals,
                    sharpe_ratio=_safe(
                        row.get("sharpe_ratio")
                    ),
                    annualized_return_pct=_safe(
                        row.get("annualized_return_pct")
                    ),
                    annualized_volatility_pct=_safe(
                        row.get(
                            "annualized_volatility_pct"
                        )
                    ),
                    max_drawdown_pct=_safe(
                        row.get("max_drawdown_pct")
                    ),
                )
            )

        return AnalysisResponse(analyses=analyses)

    @router.get(
        "/llm-usage",
        response_model=LLMUsageResponse,
    )
    async def get_llm_usage(
        user: UserContext = Depends(get_current_user),
    ):
        """LLM usage stats — own usage or all (superuser)."""
        stock_repo = _get_stock_repo()
        uid = (
            None
            if user.role == "superuser"
            else user.user_id
        )
        data = stock_repo.get_dashboard_llm_usage(
            user_id=uid, days=30,
        )

        # per_model is a dict: {model_name: {requests, cost}}
        per_model = data.get("per_model", {})
        total_req = int(
            data.get("total_requests", 0)
        )
        models = [
            ModelUsage(
                model=name,
                provider="groq",
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

        return LLMUsageResponse(
            total_requests=total_req,
            total_cost_usd=float(
                data.get("total_cost", 0) or 0
            ),
            avg_latency_ms=_safe(
                data.get("avg_latency_ms")
            ),
            models=models,
            daily_trend=data.get("daily_trend", []),
        )

    @router.get(
        "/registry",
        response_model=RegistryResponse,
    )
    async def get_registry(
        user: UserContext = Depends(get_current_user),
    ):
        """All registered tickers with company info."""
        stock_repo = _get_stock_repo()
        registry = stock_repo.get_all_registry()

        items: list[RegistryTicker] = []
        for ticker, meta in registry.items():
            info = (
                stock_repo.get_dashboard_company_info(
                    ticker,
                )
            )
            mkt = "india" if (
                ticker.endswith(".NS")
                or ticker.endswith(".BO")
            ) else "us"
            ccy = "INR" if mkt == "india" else "USD"
            company = None
            price = None

            if info:
                company = info.get("company_name")
                ccy = str(
                    info.get("currency", ccy) or ccy
                )
                raw = info.get("current_price")
                if raw is not None:
                    try:
                        price = round(float(raw), 2)
                    except (ValueError, TypeError):
                        pass

            items.append(
                RegistryTicker(
                    ticker=ticker,
                    company_name=company,
                    market=mkt,
                    currency=ccy,
                    current_price=price,
                    last_fetch_date=meta.get(
                        "last_fetch_date", ""
                    ) or None,
                )
            )

        items.sort(key=lambda t: t.ticker)
        return RegistryResponse(tickers=items)

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
        import numpy as np
        import pandas as pd

        stock_repo = _get_stock_repo()
        symbols = [
            t.strip().upper()
            for t in tickers.split(",")
            if t.strip()
        ]
        if len(symbols) < 2:
            return CompareResponse(tickers=symbols)

        series: list[CompareSeriesItem] = []
        metrics: list[CompareMetric] = []
        returns_map: dict[str, pd.Series] = {}

        for sym in symbols:
            ohlcv = stock_repo.get_ohlcv(sym)
            if ohlcv.empty or len(ohlcv) < 2:
                continue

            close = ohlcv["close"].astype(float)
            first = close.iloc[0]
            if not first or first == 0:
                continue

            norm = (close / first * 100).tolist()
            dates = [
                str(d) for d in ohlcv["date"].tolist()
            ]

            series.append(
                CompareSeriesItem(
                    ticker=sym,
                    dates=dates,
                    normalized=[
                        round(v, 4) for v in norm
                    ],
                )
            )

            daily_ret = close.pct_change().dropna()
            returns_map[sym] = daily_ret

            # Metrics from analysis_summary
            summary = (
                stock_repo
                .get_latest_analysis_summary(sym)
            )
            cur_price = round(float(close.iloc[-1]), 2)
            ccy = "USD"
            info = (
                stock_repo
                .get_dashboard_company_info(sym)
            )
            if info:
                ccy = str(
                    info.get("currency", "USD")
                    or "USD"
                )

            if summary:
                metrics.append(
                    CompareMetric(
                        ticker=sym,
                        annualized_return_pct=_safe(
                            summary.get(
                                "annualized_return_pct"
                            )
                        ),
                        annualized_volatility_pct=_safe(
                            summary.get(
                                "annualized_volatility_pct"
                            )
                        ),
                        sharpe_ratio=_safe(
                            summary.get("sharpe_ratio")
                        ),
                        max_drawdown_pct=_safe(
                            summary.get(
                                "max_drawdown_pct"
                            )
                        ),
                        current_price=cur_price,
                        currency=ccy,
                    )
                )
            else:
                metrics.append(
                    CompareMetric(
                        ticker=sym,
                        current_price=cur_price,
                        currency=ccy,
                    )
                )

        # Build correlation matrix
        corr_matrix: list[list[float]] = []
        valid = [
            s for s in symbols if s in returns_map
        ]
        if len(valid) >= 2:
            ret_df = pd.DataFrame(
                {s: returns_map[s] for s in valid},
            )
            corr = ret_df.corr().values
            corr_matrix = [
                [
                    round(float(v), 4)
                    if not np.isnan(v)
                    else 0.0
                    for v in row
                ]
                for row in corr
            ]

        return CompareResponse(
            tickers=valid,
            series=series,
            correlation=corr_matrix,
            metrics=metrics,
        )

    # -------------------------------------------------------
    # Chart endpoints (Analysis page)
    # -------------------------------------------------------

    @router.get(
        "/chart/ohlcv",
        response_model=OHLCVResponse,
    )
    async def get_chart_ohlcv(
        ticker: str = Query(
            ..., description="Ticker symbol",
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """OHLCV time series for a single ticker."""
        _logger.info(
            "chart/ohlcv ticker=%s user=%s",
            ticker, user.user_id,
        )
        stock_repo = _get_stock_repo()
        df = stock_repo.get_ohlcv(ticker.upper())

        if df.empty:
            return OHLCVResponse(ticker=ticker.upper())

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

        return OHLCVResponse(
            ticker=ticker.upper(),
            data=points,
        )

    @router.get(
        "/chart/indicators",
        response_model=IndicatorsResponse,
    )
    async def get_chart_indicators(
        ticker: str = Query(
            ..., description="Ticker symbol",
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """Technical indicators time series."""
        _logger.info(
            "chart/indicators ticker=%s user=%s",
            ticker, user.user_id,
        )
        stock_repo = _get_stock_repo()
        df = stock_repo.get_technical_indicators(
            ticker.upper(),
        )

        if df.empty:
            return IndicatorsResponse(
                ticker=ticker.upper(),
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

        return IndicatorsResponse(
            ticker=ticker.upper(),
            data=points,
        )

    @router.get(
        "/chart/forecast-series",
        response_model=ForecastSeriesResponse,
    )
    async def get_chart_forecast_series(
        ticker: str = Query(
            ..., description="Ticker symbol",
        ),
        horizon: int = Query(
            9,
            description="Forecast horizon in months",
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """Forecast time series with confidence bands."""
        _logger.info(
            "chart/forecast-series ticker=%s "
            "horizon=%d user=%s",
            ticker, horizon, user.user_id,
        )
        stock_repo = _get_stock_repo()
        df = stock_repo.get_latest_forecast_series(
            ticker.upper(), horizon,
        )

        if df.empty:
            return ForecastSeriesResponse(
                ticker=ticker.upper(),
                horizon_months=horizon,
            )

        points: list[ForecastPoint] = []
        for _, row in df.iterrows():
            points.append(
                ForecastPoint(
                    date=str(
                        row.get("forecast_date", "")
                    ),
                    predicted=float(
                        row.get("predicted_price", 0)
                    ),
                    lower=float(
                        row.get("lower_bound", 0)
                    ),
                    upper=float(
                        row.get("upper_bound", 0)
                    ),
                )
            )

        return ForecastSeriesResponse(
            ticker=ticker.upper(),
            horizon_months=horizon,
            data=points,
        )

    return router


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
    elif (
        "bear" in lower
        or "below" in lower
        or "oversold" in lower
    ):
        signal = "Bearish"
    elif "overbought" in lower:
        signal = "Bearish"
    else:
        signal = "Neutral"

    # Numeric from technical_indicators first,
    # then analysis_summary, then signal text.
    val = (
        (ti_vals or {}).get(value_col)
        or row.get(value_col)
    )
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
