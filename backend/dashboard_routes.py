"""Dashboard API endpoints.

Provides aggregated data for the native Next.js dashboard
widgets: watchlist, forecasts, analysis signals, and LLM
usage summary.  Queries the Iceberg data layer via
:class:`~stocks.repository.StockRepository`.
"""

import logging

from fastapi import APIRouter, Depends

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_current_user
from auth.models import UserContext
from dashboard_models import (
    AnalysisResponse,
    ForecastsResponse,
    ForecastTarget,
    LLMUsageResponse,
    ModelUsage,
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
