"""Technical indicator calculations for price analysis.

Functions
---------
- :func:`_calculate_technical_indicators` — SMA/EMA/RSI/MACD/BB/ATR.
"""

import logging

import pandas as pd
import ta

# Module-level logger; kept here as this module is not class-based.
_logger = logging.getLogger(__name__)


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

    df["SMA_50"] = ta.trend.SMAIndicator(
        close=close, window=50
    ).sma_indicator()
    df["SMA_200"] = ta.trend.SMAIndicator(
        close=close, window=200
    ).sma_indicator()
    df["EMA_20"] = ta.trend.EMAIndicator(
        close=close, window=20
    ).ema_indicator()

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

    _logger.debug(
        "Technical indicators calculated for DataFrame with %d rows", len(df)
    )
    return df
