"""Technical indicator calculations for price analysis.

Functions
---------
- :func:`_calculate_technical_indicators` — SMA/EMA/RSI/MACD/BB/ATR.
- :func:`compute_emv_14` — 14-day SMA of Ease-of-Movement; used
  on-demand by the Sprint 9 ``/v1/advanced-analytics/`` endpoints
  (no Iceberg persistence — see AA-1 deferral note).
"""

import logging

import numpy as np
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
    df["RSI_2"] = ta.momentum.RSIIndicator(close=close, window=2).rsi()

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


# ----------------------------------------------------------------------
# Sprint 9 Advanced Analytics — Ease-of-Movement
# ----------------------------------------------------------------------


def compute_emv_14(
    df: pd.DataFrame,
    *,
    window: int = 14,
) -> pd.Series:
    """Return the SMA-smoothed Ease-of-Movement series.

    Implements the standard EMV indicator used by AA-7's
    ``/v1/advanced-analytics/`` endpoints (and surfaced as
    the ``avg_emv_score`` / ``avg_14d_emv`` column on the
    Current Day Upmove + Previous Day Breakout reports).
    No Iceberg persistence — value is recomputed per-tab
    request and cached at the endpoint level (TTL_STABLE).

    Formula (per CLAUDE.md AA-5 plan and Wikipedia):

    .. code-block:: text

        EMV_t  = ((H_t + L_t)/2 − (H_{t-1} + L_{t-1})/2)
                 / (V_t / (H_t − L_t))
        emv_14 = SMA(EMV, 14)

    Implementation delegates to ``ta.volume.EaseOfMovement
    Indicator`` for the SMA-smoothed series, with explicit
    NaN handling on zero-range candles (``H_t == L_t``)
    and missing inputs (per CLAUDE.md §6.1).

    Args:
        df: OHLCV DataFrame with at least ``High``,
            ``Low``, ``Volume`` columns.  Index is
            preserved on the output.
        window: Smoothing window (default 14 — matches
            the report column name).

    Returns:
        ``pd.Series`` of EMV-14 values aligned to *df*'s
        index.  ``NaN`` for the warmup window and any row
        where the underlying inputs are zero-range or
        non-numeric.
    """
    if df.empty:
        return pd.Series([], dtype="float64", index=df.index)

    required = {"High", "Low", "Volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"compute_emv_14: missing columns {sorted(missing)}",
        )

    high = pd.to_numeric(df["High"], errors="coerce")
    low = pd.to_numeric(df["Low"], errors="coerce")
    volume = pd.to_numeric(df["Volume"], errors="coerce")

    # Zero-range candle → div-by-zero in EMV's box-ratio.
    # ``ta`` lib raises a RuntimeWarning and yields ±inf
    # for those rows; we coerce to NaN so the SMA window
    # ignores them naturally rather than poisoning the
    # average (§6.1 NaN propagation guard).
    safe_high = high.where(high != low)
    safe_low = low.where(high != low)

    emv = ta.volume.EaseOfMovementIndicator(
        high=safe_high,
        low=safe_low,
        volume=volume,
        window=window,
        fillna=False,
    ).sma_ease_of_movement()

    # ``ta`` returns inf when volume is 0 or boxes overlap;
    # collapse those to NaN for downstream-consumer safety.
    emv = emv.replace([np.inf, -np.inf], np.nan)
    emv.name = f"EMV_{window}"
    return emv
