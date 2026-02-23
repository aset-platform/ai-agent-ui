"""Price movement analysis tools for the Stock Analysis Agent.

This module provides private helper functions for computing technical
indicators, price movement statistics, and summary metrics, plus one
public LangChain ``@tool`` function that orchestrates the full analysis
pipeline for a given ticker.

All analysis reads from locally stored parquet files (written by
:mod:`tools.stock_data_tool`). Results are returned as formatted strings
suitable for LLM consumption.  An interactive Plotly HTML chart is saved
to ``charts/analysis/{TICKER}_analysis.html``.

Technical indicators calculated:

- SMA 50-day and SMA 200-day
- EMA 20-day
- RSI 14-day
- MACD line, signal line, histogram
- Bollinger Bands (upper, middle, lower)
- Average True Range (ATR) 14-day

Typical usage (via LangChain tool call)::

    from tools.price_analysis_tool import analyse_stock_price

    result = analyse_stock_price.invoke({"ticker": "AAPL"})
"""

import logging
import math
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import ta
from plotly.subplots import make_subplots
from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_RAW = _PROJECT_ROOT / "data" / "raw"
_CHARTS_ANALYSIS = _PROJECT_ROOT / "charts" / "analysis"
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache"

# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _load_cache(ticker: str, key: str) -> Optional[str]:
    """Return cached result text for today if it exists, otherwise None.

    Args:
        ticker: Stock ticker symbol (uppercased).
        key: Cache key string, e.g. ``"analysis"``.

    Returns:
        The cached result string, or ``None`` if no cache file exists for today.
    """
    path = _CACHE_DIR / f"{ticker}_{key}_{date.today()}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _save_cache(ticker: str, key: str, result: str) -> None:
    """Write result text to a dated cache file.

    Args:
        ticker: Stock ticker symbol (uppercased).
        key: Cache key string, e.g. ``"analysis"``.
        result: The string result to cache.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{ticker}_{key}_{date.today()}.txt"
    path.write_text(result, encoding="utf-8")
    logger.debug("Cache saved: %s", path)


def _load_parquet(ticker: str) -> Optional[pd.DataFrame]:
    """Load the raw OHLCV parquet file for a ticker.

    Args:
        ticker: Stock ticker symbol (already uppercased).

    Returns:
        A :class:`pandas.DataFrame` with a DatetimeIndex, or ``None`` if the
        parquet file does not exist.
    """
    file_path = _DATA_RAW / f"{ticker}_raw.parquet"
    if not file_path.exists():
        logger.warning("Parquet file not found for %s: %s", ticker, file_path)
        return None
    df = pd.read_parquet(file_path, engine="pyarrow")
    df.index = pd.to_datetime(df.index)
    return df


def _calculate_returns(df: pd.DataFrame) -> dict:
    """Calculate daily, monthly, annual, and cumulative returns.

    Args:
        df: OHLCV DataFrame with a DatetimeIndex and a ``Close`` column.

    Returns:
        Dictionary with keys ``"daily"``, ``"monthly"``, ``"annual"``,
        and ``"cumulative"``, each containing a :class:`pandas.Series`.
    """
    close = df["Close"]
    daily = close.pct_change().dropna()
    monthly = close.resample("ME").last().pct_change().dropna()
    annual = close.resample("YE").last().pct_change().dropna()
    cumulative = (1 + daily).cumprod() - 1
    return {
        "daily": daily,
        "monthly": monthly,
        "annual": annual,
        "cumulative": cumulative,
    }


def _calculate_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add technical indicator columns to the OHLCV DataFrame.

    Adds the following columns in-place on a copy of ``df``:
    ``SMA_50``, ``SMA_200``, ``EMA_20``, ``RSI_14``, ``MACD``,
    ``MACD_Signal``, ``MACD_Hist``, ``BB_Upper``, ``BB_Middle``,
    ``BB_Lower``, ``ATR_14``.

    Args:
        df: OHLCV DataFrame with ``Open``, ``High``, ``Low``, ``Close``
            columns and a DatetimeIndex.

    Returns:
        A new DataFrame with all indicator columns appended.
    """
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    df["SMA_50"] = ta.trend.SMAIndicator(close=close, window=50).sma_indicator()
    df["SMA_200"] = ta.trend.SMAIndicator(close=close, window=200).sma_indicator()
    df["EMA_20"] = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()

    df["RSI_14"] = ta.momentum.RSIIndicator(close=close, window=14).rsi()

    macd_obj = ta.trend.MACD(close=close)
    df["MACD"] = macd_obj.macd()
    df["MACD_Signal"] = macd_obj.macd_signal()
    df["MACD_Hist"] = macd_obj.macd_diff()

    bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    df["BB_Upper"] = bb.bollinger_hband()
    df["BB_Middle"] = bb.bollinger_mavg()
    df["BB_Lower"] = bb.bollinger_lband()

    df["ATR_14"] = ta.volatility.AverageTrueRange(
        high=high, low=low, close=close, window=14
    ).average_true_range()

    logger.debug("Technical indicators calculated for DataFrame with %d rows", len(df))
    return df


def _analyse_price_movement(df: pd.DataFrame) -> dict:
    """Analyse bull/bear phases, drawdown, support/resistance, volatility, Sharpe.

    Args:
        df: DataFrame with indicators already added by
            :func:`_calculate_technical_indicators`.

    Returns:
        Dictionary with keys: ``bull_phase_pct``, ``bear_phase_pct``,
        ``max_drawdown_pct``, ``max_drawdown_duration_days``,
        ``support_levels``, ``resistance_levels``,
        ``annualized_volatility_pct``, ``annualized_return_pct``,
        ``sharpe_ratio``.
    """
    close = df["Close"]
    daily_returns = close.pct_change().dropna()

    # Bull / bear phases — price vs SMA 200
    mask = df["SMA_200"].notna()
    above = (close[mask] > df["SMA_200"][mask])
    bull_pct = float(above.mean() * 100)
    bear_pct = 100.0 - bull_pct

    # Max drawdown
    rolling_max = close.cummax()
    drawdown = (close - rolling_max) / rolling_max
    max_drawdown = float(drawdown.min() * 100)

    # Longest drawdown duration (consecutive trading days below previous peak)
    in_drawdown = (drawdown < 0).astype(int)
    groups = in_drawdown * (in_drawdown.groupby(
        (in_drawdown != in_drawdown.shift()).cumsum()
    ).cumcount() + 1)
    max_dd_duration = int(groups.max())

    # Support / resistance from the most recent 252 trading days
    recent = df.tail(252)
    support_levels = sorted(recent["Low"].nsmallest(3).round(2).tolist())
    resistance_levels = sorted(
        recent["High"].nlargest(3).round(2).tolist(), reverse=True
    )

    # Annualised volatility
    ann_vol_pct = float(daily_returns.std() * math.sqrt(252) * 100)

    # Annualised return
    ann_return = float(daily_returns.mean() * 252)

    # Sharpe ratio (risk-free rate = 4 %)
    ann_vol_dec = daily_returns.std() * math.sqrt(252)
    sharpe = (ann_return - 0.04) / ann_vol_dec if ann_vol_dec > 0 else 0.0

    return {
        "bull_phase_pct": round(bull_pct, 1),
        "bear_phase_pct": round(bear_pct, 1),
        "max_drawdown_pct": round(max_drawdown, 2),
        "max_drawdown_duration_days": max_dd_duration,
        "support_levels": support_levels,
        "resistance_levels": resistance_levels,
        "annualized_volatility_pct": round(ann_vol_pct, 2),
        "annualized_return_pct": round(ann_return * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
    }


def _generate_summary_stats(df: pd.DataFrame, ticker: str) -> dict:
    """Generate high-level summary statistics for a ticker.

    Computes all-time high/low, best/worst calendar month and year,
    average annual return, current price vs moving averages, and RSI signal.

    Args:
        df: DataFrame with indicator columns added.
        ticker: Stock ticker symbol (used in the returned dict).

    Returns:
        Dictionary with summary statistics ready for report formatting.
    """
    close = df["Close"]

    ath_idx = close.idxmax()
    atl_idx = close.idxmin()

    monthly_close = close.resample("ME").last()
    monthly_ret = monthly_close.pct_change().dropna()
    best_month_idx = monthly_ret.idxmax()
    worst_month_idx = monthly_ret.idxmin()

    annual_close = close.resample("YE").last()
    annual_ret = annual_close.pct_change().dropna()
    best_year_idx = annual_ret.idxmax()
    worst_year_idx = annual_ret.idxmin()

    avg_annual_pct = float(annual_ret.mean() * 100)
    total_return_pct = float((close.iloc[-1] / close.iloc[0] - 1) * 100)
    current_price = float(close.iloc[-1])

    sma50 = float(df["SMA_50"].iloc[-1]) if "SMA_50" in df.columns else None
    sma200 = float(df["SMA_200"].iloc[-1]) if "SMA_200" in df.columns else None
    rsi = float(df["RSI_14"].iloc[-1]) if "RSI_14" in df.columns else None

    if rsi is not None:
        rsi_signal = "Overbought" if rsi >= 70 else ("Oversold" if rsi <= 30 else "Neutral")
    else:
        rsi_signal = "N/A"

    macd_val = float(df["MACD"].iloc[-1]) if "MACD" in df.columns else None
    macd_sig = float(df["MACD_Signal"].iloc[-1]) if "MACD_Signal" in df.columns else None
    if macd_val is not None and macd_sig is not None:
        macd_signal_str = "Bullish" if macd_val > macd_sig else "Bearish"
    else:
        macd_signal_str = "N/A"

    return {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "all_time_high": round(float(close.max()), 2),
        "all_time_high_date": str(ath_idx.date()),
        "all_time_low": round(float(close.min()), 2),
        "all_time_low_date": str(atl_idx.date()),
        "total_return_pct": round(total_return_pct, 2),
        "avg_annual_return_pct": round(avg_annual_pct, 2),
        "best_month": best_month_idx.strftime("%b %Y"),
        "best_month_return_pct": round(float(monthly_ret.max() * 100), 2),
        "worst_month": worst_month_idx.strftime("%b %Y"),
        "worst_month_return_pct": round(float(monthly_ret.min() * 100), 2),
        "best_year": str(best_year_idx.year),
        "best_year_return_pct": round(float(annual_ret.max() * 100), 2),
        "worst_year": str(worst_year_idx.year),
        "worst_year_return_pct": round(float(annual_ret.min() * 100), 2),
        "sma_50": round(sma50, 2) if sma50 is not None else "N/A",
        "sma_50_signal": (
            "Above" if sma50 and current_price > sma50 else "Below"
        ) if sma50 else "N/A",
        "sma_200": round(sma200, 2) if sma200 is not None else "N/A",
        "sma_200_signal": (
            "Above" if sma200 and current_price > sma200 else "Below"
        ) if sma200 else "N/A",
        "rsi_14": round(rsi, 1) if rsi is not None else "N/A",
        "rsi_signal": rsi_signal,
        "macd_signal": macd_signal_str,
    }


def _create_analysis_chart(df: pd.DataFrame, ticker: str) -> str:
    """Build and save a 3-panel interactive Plotly analysis chart.

    Panel 1 (60 %): Candlestick with SMA 50, SMA 200, Bollinger Bands.
    Panel 2 (20 %): Volume bars coloured green/red by price direction.
    Panel 3 (20 %): RSI with overbought/oversold zones.

    All panels use a dark Plotly theme.

    Args:
        df: DataFrame with indicator columns added.
        ticker: Stock ticker symbol (used in chart title and filename).

    Returns:
        Absolute path to the saved HTML chart file as a string.
    """
    _CHARTS_ANALYSIS.mkdir(parents=True, exist_ok=True)

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=(
            f"{ticker} — Price & Indicators",
            "Volume",
            "RSI (14)",
        ),
    )

    # ── Panel 1: Candlestick ──────────────────────────────────────────────
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="OHLC",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1,
        col=1,
    )

    if "SMA_50" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["SMA_50"],
                name="SMA 50",
                line=dict(color="orange", width=1.5),
            ),
            row=1,
            col=1,
        )

    if "SMA_200" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["SMA_200"],
                name="SMA 200",
                line=dict(color="tomato", width=1.5),
            ),
            row=1,
            col=1,
        )

    if "BB_Upper" in df.columns and "BB_Lower" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["BB_Upper"],
                name="BB Upper",
                line=dict(color="rgba(100,149,237,0.7)", width=1, dash="dot"),
                showlegend=True,
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["BB_Lower"],
                name="BB Lower",
                line=dict(color="rgba(100,149,237,0.7)", width=1, dash="dot"),
                fill="tonexty",
                fillcolor="rgba(100,149,237,0.07)",
            ),
            row=1,
            col=1,
        )

    # ── Panel 2: Volume ───────────────────────────────────────────────────
    vol_colors = [
        "#26a69a" if df["Close"].iloc[i] >= df["Open"].iloc[i] else "#ef5350"
        for i in range(len(df))
    ]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"],
            name="Volume",
            marker_color=vol_colors,
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    # ── Panel 3: RSI ──────────────────────────────────────────────────────
    if "RSI_14" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["RSI_14"],
                name="RSI (14)",
                line=dict(color="#ab47bc", width=1.5),
            ),
            row=3,
            col=1,
        )
        fig.add_hline(
            y=70, line_dash="dash", line_color="tomato", line_width=1, row=3, col=1
        )
        fig.add_hline(
            y=30, line_dash="dash", line_color="#26a69a", line_width=1, row=3, col=1
        )
        fig.add_hrect(
            y0=70, y1=100, fillcolor="tomato", opacity=0.07, line_width=0,
            row=3, col=1,
        )
        fig.add_hrect(
            y0=0, y1=30, fillcolor="#26a69a", opacity=0.07, line_width=0,
            row=3, col=1,
        )

    fig.update_layout(
        template="plotly_dark",
        title=dict(text=f"{ticker} — Technical Analysis", font=dict(size=16)),
        height=900,
        showlegend=True,
        xaxis_rangeslider_visible=False,
        margin=dict(l=60, r=30, t=80, b=30),
    )
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_yaxes(title_text="RSI", row=3, col=1, range=[0, 100])

    out_path = _CHARTS_ANALYSIS / f"{ticker}_analysis.html"
    fig.write_html(str(out_path))
    logger.info("Analysis chart saved: %s", out_path)
    return str(out_path)


# ---------------------------------------------------------------------------
# Public @tool function
# ---------------------------------------------------------------------------


@tool
def analyse_stock_price(ticker: str) -> str:
    """Perform full technical price analysis on a stock and generate a chart.

    Loads locally stored OHLCV data (written by :func:`fetch_stock_data`),
    calculates technical indicators (SMA 50/200, EMA 20, RSI 14, MACD,
    Bollinger Bands, ATR 14), analyses bull/bear phases, max drawdown,
    support/resistance levels, annualised volatility, and Sharpe ratio.

    Saves an interactive 3-panel Plotly chart (candlestick + volume + RSI)
    in dark theme to ``charts/analysis/{TICKER}_analysis.html``.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``. Data must already be
            fetched via :func:`fetch_stock_data` before calling this tool.

    Returns:
        A formatted multi-section string report with all key metrics and
        the chart file path, or an error string if data is unavailable.

    Example:
        >>> result = analyse_stock_price.invoke({"ticker": "AAPL"})
        >>> "AAPL" in result
        True
    """
    ticker = ticker.upper().strip()
    logger.info("analyse_stock_price | ticker=%s", ticker)

    cached = _load_cache(ticker, "analysis")
    if cached:
        logger.info("Returning cached analysis for %s", ticker)
        return cached

    try:
        df = _load_parquet(ticker)
        if df is None:
            return (
                f"No local data found for '{ticker}'. "
                "Please run fetch_stock_data first."
            )

        df = _calculate_technical_indicators(df)
        movement = _analyse_price_movement(df)
        stats = _generate_summary_stats(df, ticker)
        chart_path = _create_analysis_chart(df, ticker)

        report = (
            f"=== PRICE ANALYSIS: {ticker} ===\n\n"
            f"PRICE SUMMARY\n"
            f"  Current Price   : ${stats['current_price']}\n"
            f"  All Time High   : ${stats['all_time_high']} ({stats['all_time_high_date']})\n"
            f"  All Time Low    : ${stats['all_time_low']} ({stats['all_time_low_date']})\n"
            f"  10Y Total Return: {stats['total_return_pct']:+.1f}%\n"
            f"  Avg Annual Ret  : {stats['avg_annual_return_pct']:+.1f}%\n\n"
            f"TECHNICAL INDICATORS\n"
            f"  SMA 50          : ${stats['sma_50']} ({stats['sma_50_signal']})\n"
            f"  SMA 200         : ${stats['sma_200']} ({stats['sma_200_signal']})\n"
            f"  RSI (14)        : {stats['rsi_14']} — {stats['rsi_signal']}\n"
            f"  MACD            : {stats['macd_signal']}\n"
            f"  Volatility      : {movement['annualized_volatility_pct']}% annualised\n"
            f"  Sharpe Ratio    : {movement['sharpe_ratio']}\n\n"
            f"MARKET PHASES (vs SMA 200)\n"
            f"  Bull phase      : {movement['bull_phase_pct']}% of time\n"
            f"  Bear phase      : {movement['bear_phase_pct']}% of time\n\n"
            f"DRAWDOWN\n"
            f"  Max Drawdown    : {movement['max_drawdown_pct']:.1f}%\n"
            f"  Max DD Duration : {movement['max_drawdown_duration_days']} trading days\n\n"
            f"KEY LEVELS (last 252 days)\n"
            f"  Support         : {movement['support_levels']}\n"
            f"  Resistance      : {movement['resistance_levels']}\n\n"
            f"CALENDAR PERFORMANCE\n"
            f"  Best Month      : {stats['best_month']} ({stats['best_month_return_pct']:+.1f}%)\n"
            f"  Worst Month     : {stats['worst_month']} ({stats['worst_month_return_pct']:+.1f}%)\n"
            f"  Best Year       : {stats['best_year']} ({stats['best_year_return_pct']:+.1f}%)\n"
            f"  Worst Year      : {stats['worst_year']} ({stats['worst_year_return_pct']:+.1f}%)\n\n"
            f"CHART\n"
            f"  Saved to: {chart_path}\n"
        )

        _save_cache(ticker, "analysis", report)
        logger.info("analyse_stock_price complete for %s", ticker)
        return report

    except Exception as e:
        logger.error(
            "analyse_stock_price failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error analysing '{ticker}': {e}"
