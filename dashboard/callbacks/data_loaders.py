"""Data-loading helpers for the AI Stock Analysis Dashboard callbacks.

Provides path constants and functions that read raw OHLCV parquet files,
forecast parquet files, the stock registry JSON, and calculate technical
indicators using the ``ta`` library.

Example::

    from dashboard.callbacks.data_loaders import _load_raw, _add_indicators
"""

import json
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
# Path constants (mirror backend tool constants)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Indicator cache — 5-min TTL to avoid recomputing on every overlay/range change
# ---------------------------------------------------------------------------
_INDICATOR_CACHE: dict = {}   # {ticker: (df_with_indicators, expiry_monotonic)}
_INDICATOR_TTL = 300           # seconds

_DATA_RAW = _PROJECT_ROOT / "data" / "raw"
_DATA_FORECASTS = _PROJECT_ROOT / "data" / "forecasts"
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"
_REGISTRY_PATH = _DATA_METADATA / "stock_registry.json"


def _load_reg_cb() -> dict:
    """Load the stock registry for use inside callbacks.

    Tries the Iceberg ``stocks.registry`` table first so the dashboard
    always reflects every ticker the backend has ever fetched.  Falls
    back to the flat JSON file when Iceberg is unavailable or empty.

    Returns:
        Registry dict keyed by ticker symbol; empty dict if all sources fail.
    """
    # ── 1. Iceberg primary source ────────────────────────────────────────
    try:
        from pyiceberg.catalog import load_catalog

        cat = load_catalog("local")
        tbl = cat.load_table("stocks.registry")
        # Fix #19: project only the columns we need to reduce I/O
        df = tbl.scan(selected_fields=(
            "ticker", "last_fetch_date", "total_rows",
            "date_range_start", "date_range_end",
        )).to_pandas()
        if not df.empty:
            registry: dict = {}
            # Fix #5: replace iterrows() with faster .values array iteration
            _proj = [c for c in (
                "ticker", "last_fetch_date", "total_rows",
                "date_range_start", "date_range_end",
            ) if c in df.columns]
            _ci = {c: i for i, c in enumerate(_proj)}
            _has_lfd   = "last_fetch_date"   in _ci
            _has_tr    = "total_rows"         in _ci
            _has_range = ("date_range_start" in _ci and "date_range_end" in _ci)
            for row in df[_proj].values:
                ticker = str(row[0]) if row[0] else ""
                if not ticker:
                    continue
                entry: dict = {"ticker": ticker}
                if _has_lfd and row[_ci["last_fetch_date"]]:
                    entry["last_fetch_date"] = str(row[_ci["last_fetch_date"]])[:10]
                if _has_tr and row[_ci["total_rows"]] is not None:
                    entry["total_rows"] = int(row[_ci["total_rows"]])
                if _has_range:
                    start = row[_ci["date_range_start"]]
                    end   = row[_ci["date_range_end"]]
                    if start and end:
                        entry["date_range"] = {
                            "start": str(start)[:10],
                            "end":   str(end)[:10],
                        }
                entry["file_path"] = str(_DATA_RAW / f"{ticker}_raw.parquet")
                registry[ticker] = entry
            if registry:
                _logger.debug(
                    "Registry loaded from Iceberg: %d tickers.", len(registry)
                )
                return registry
    except Exception as exc:
        _logger.debug("Iceberg registry unavailable (%s); falling back to JSON.", exc)

    # ── 2. JSON fallback ─────────────────────────────────────────────────
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        with open(_REGISTRY_PATH) as fh:
            data = json.load(fh)
        _logger.debug("Registry loaded from JSON: %d tickers.", len(data))
        return data
    except Exception as exc:
        _logger.warning("registry JSON load failed: %s", exc)
        return {}


def _load_raw(ticker: str) -> Optional[pd.DataFrame]:
    """Load the raw OHLCV parquet file for a ticker.

    Args:
        ticker: Uppercase ticker symbol (e.g. ``"AAPL"``).

    Returns:
        DataFrame with DatetimeIndex, or ``None`` if the file is absent.
    """
    path = _DATA_RAW / f"{ticker}_raw.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, engine="pyarrow")
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as exc:
        _logger.error("Error loading %s: %s", path, exc)
        return None


def _load_forecast(ticker: str, horizon_months: int) -> Optional[pd.DataFrame]:
    """Find and load the best-matching forecast parquet for a ticker.

    Prefers an exact match for *horizon_months*; falls back to longer
    horizons (9m → 6m → 3m) so that a 9-month forecast can satisfy a
    6-month request.

    Args:
        ticker: Uppercase ticker symbol.
        horizon_months: Requested forecast horizon in months.

    Returns:
        DataFrame with ``ds``, ``yhat``, ``yhat_lower``, ``yhat_upper``
        columns, or ``None`` if no forecast file is found.
    """
    for h in [horizon_months, 9, 6, 3]:
        if h < horizon_months:
            continue
        path = _DATA_FORECASTS / f"{ticker}_{h}m_forecast.parquet"
        if path.exists():
            try:
                df = pd.read_parquet(path, engine="pyarrow")
                df["ds"] = pd.to_datetime(df["ds"])
                return df
            except Exception as exc:
                _logger.error("Error loading forecast %s: %s", path, exc)
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
    df["SMA_50"]      = ta.trend.SMAIndicator(close=close, window=50).sma_indicator()
    df["SMA_200"]     = ta.trend.SMAIndicator(close=close, window=200).sma_indicator()
    df["EMA_20"]      = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
    df["RSI_14"]      = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    macd              = ta.trend.MACD(close=close)
    df["MACD"]        = macd.macd()
    df["MACD_Signal"] = macd.macd_signal()
    df["MACD_Hist"]   = macd.macd_diff()
    bb                = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    df["BB_Upper"]    = bb.bollinger_hband()
    df["BB_Middle"]   = bb.bollinger_mavg()
    df["BB_Lower"]    = bb.bollinger_lband()
    df["ATR_14"]      = ta.volatility.AverageTrueRange(
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