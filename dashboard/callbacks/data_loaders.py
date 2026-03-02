"""Data-loading helpers for the AI Stock Analysis Dashboard callbacks.

Provides functions that read raw OHLCV data and forecasts from Iceberg,
the stock registry from Iceberg, and calculate technical indicators
using the ``ta`` library.  All data reads go through Iceberg — flat
parquet files are no longer used as a data source.

Example::

    from dashboard.callbacks.data_loaders import _load_raw, _add_indicators
"""

import logging
import sys
import time as _time
from pathlib import Path
from typing import Optional

import pandas as pd
import ta

# ---------------------------------------------------------------------------
# Module-level logger — prefixed with underscore to signal module-private use;
# kept at module level because there is no enclosing class.
# ---------------------------------------------------------------------------
_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so backend modules are importable
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Indicator cache — 5-min TTL to avoid recomputing on every overlay/range change
# ---------------------------------------------------------------------------
_INDICATOR_CACHE: dict = {}  # {ticker: (df_with_indicators, expiry_monotonic)}
_INDICATOR_TTL = 300  # seconds


def _load_reg_cb() -> dict:
    """Load the stock registry from Iceberg for use inside callbacks.

    Reads the ``stocks.registry`` Iceberg table as the single source of truth
    for ticker metadata.

    Returns:
        Registry dict keyed by ticker symbol; empty dict on failure.
    """
    try:
        from dashboard.callbacks.iceberg import _get_iceberg_repo

        repo = _get_iceberg_repo()
        if repo is not None:
            registry = repo.get_all_registry()
            _logger.debug("Registry loaded from Iceberg: %d tickers.", len(registry))
            return registry
    except Exception as exc:
        _logger.warning("Iceberg registry load failed: %s", exc)

    return {}


def _load_raw(ticker: str) -> Optional[pd.DataFrame]:
    """Load OHLCV data for a ticker from Iceberg.

    Args:
        ticker: Uppercase ticker symbol (e.g. ``"AAPL"``).

    Returns:
        DataFrame with DatetimeIndex and columns ``Open``, ``High``, ``Low``,
        ``Close``, ``Adj Close``, ``Volume``, or ``None`` if no data exists.
    """
    try:
        from dashboard.callbacks.iceberg import _get_iceberg_repo, _get_ohlcv_cached

        repo = _get_iceberg_repo()
        if repo is not None:
            return _get_ohlcv_cached(repo, ticker)
    except Exception as exc:
        _logger.error("Error loading OHLCV from Iceberg for %s: %s", ticker, exc)
    return None


def _load_forecast(ticker: str, horizon_months: int) -> Optional[pd.DataFrame]:
    """Load the latest forecast series for a ticker from Iceberg.

    Prefers an exact match for *horizon_months*; falls back to longer
    horizons (9m → 6m → 3m) so that a 9-month forecast can satisfy a
    6-month request.

    Args:
        ticker: Uppercase ticker symbol.
        horizon_months: Requested forecast horizon in months.

    Returns:
        DataFrame with ``ds``, ``yhat``, ``yhat_lower``, ``yhat_upper``
        columns, or ``None`` if no forecast exists.
    """
    try:
        from dashboard.callbacks.iceberg import _get_forecast_cached, _get_iceberg_repo

        repo = _get_iceberg_repo()
        if repo is None:
            return None
        for h in [horizon_months, 9, 6, 3]:
            if h < horizon_months:
                continue
            result = _get_forecast_cached(repo, ticker, h)
            if result is not None:
                return result
    except Exception as exc:
        _logger.error("Error loading forecast from Iceberg for %s: %s", ticker, exc)
    return None


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate technical indicators and return an enriched DataFrame copy.

    Adds SMA_50, SMA_200, EMA_20, RSI_14, MACD, MACD_Signal, MACD_Hist,
    BB_Upper, BB_Middle, BB_Lower, and ATR_14 columns.

    Args:
        df: OHLCV DataFrame with ``Open``, ``High``, ``Low``, ``Close``
            columns and a DatetimeIndex.

    Returns:
        Copy of *df* with all indicator columns appended.
    """
    df = df.copy()
    close = df["Close"]
    df["SMA_50"] = ta.trend.SMAIndicator(close=close, window=50).sma_indicator()
    df["SMA_200"] = ta.trend.SMAIndicator(close=close, window=200).sma_indicator()
    df["EMA_20"] = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
    df["RSI_14"] = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    macd = ta.trend.MACD(close=close)
    df["MACD"] = macd.macd()
    df["MACD_Signal"] = macd.macd_signal()
    df["MACD_Hist"] = macd.macd_diff()
    bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    df["BB_Upper"] = bb.bollinger_hband()
    df["BB_Middle"] = bb.bollinger_mavg()
    df["BB_Lower"] = bb.bollinger_lband()
    df["ATR_14"] = ta.volatility.AverageTrueRange(
        high=df["High"], low=df["Low"], close=close, window=14
    ).average_true_range()
    return df


def _add_indicators_cached(ticker: str, df: pd.DataFrame) -> pd.DataFrame:
    """Return a cached, indicator-enriched copy of *df* for *ticker*.

    Results are cached per ticker for ``_INDICATOR_TTL`` seconds (5 min) so
    that toggling overlays or changing the date-range slider does not
    recompute all indicators on every callback invocation.

    Args:
        ticker: Uppercase ticker symbol used as the cache key.
        df: Raw OHLCV DataFrame (without indicator columns).

    Returns:
        Copy of *df* with all indicator columns appended.
    """
    now = _time.monotonic()
    entry = _INDICATOR_CACHE.get(ticker)
    if entry and entry[1] > now:
        return entry[0]
    result = _add_indicators(df)
    _INDICATOR_CACHE[ticker] = (result, now + _INDICATOR_TTL)
    return result
