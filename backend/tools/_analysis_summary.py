"""Summary statistics builder for price analysis reports.

Functions
---------
- :func:`_generate_summary_stats` — ATH/ATL, calendar perf, signals.
"""

import logging

import pandas as pd

# Module-level logger; kept at module scope as a private constant.
_logger = logging.getLogger(__name__)


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

    sma50: float | None = (
        float(df["SMA_50"].iloc[-1]) if "SMA_50" in df.columns else None
    )
    sma200: float | None = (
        float(df["SMA_200"].iloc[-1]) if "SMA_200" in df.columns else None
    )
    rsi: float | None = (
        float(df["RSI_14"].iloc[-1]) if "RSI_14" in df.columns else None
    )

    if rsi is not None:
        rsi_signal = (
            "Overbought"
            if rsi >= 70
            else ("Oversold" if rsi <= 30 else "Neutral")
        )
    else:
        rsi_signal = "N/A"

    macd_val: float | None = (
        float(df["MACD"].iloc[-1]) if "MACD" in df.columns else None
    )
    macd_sig: float | None = (
        float(df["MACD_Signal"].iloc[-1])
        if "MACD_Signal" in df.columns
        else None
    )
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
            ("Above" if sma50 and current_price > sma50 else "Below")
            if sma50
            else "N/A"
        ),
        "sma_200": round(sma200, 2) if sma200 is not None else "N/A",
        "sma_200_signal": (
            ("Above" if sma200 and current_price > sma200 else "Below")
            if sma200
            else "N/A"
        ),
        "rsi_14": round(rsi, 1) if rsi is not None else "N/A",
        "rsi_signal": rsi_signal,
        "macd_signal": macd_signal_str,
    }
