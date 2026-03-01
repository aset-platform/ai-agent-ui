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
        df = tbl.scan().to_pandas()
        if not df.empty:
            registry: dict = {}
            for _, row in df.iterrows():
                ticker = row.get("ticker", "")
                if not ticker:
                    continue
                entry: dict = {"ticker": ticker}
                if "last_fetch_date" in df.columns and row.get("last_fetch_date"):
                    entry["last_fetch_date"] = str(row["last_fetch_date"])[:10]
                if "total_rows" in df.columns and row.get("total_rows") is not None:
                    entry["total_rows"] = int(row["total_rows"])
                start = row.get("date_range_start")
                end   = row.get("date_range_end")
                if start and end:
                    entry["date_range"] = {
                        "start": str(start)[:10],
                        "end":   str(end)[:10],
                    }
                raw_path = _DATA_RAW / f"{ticker}_raw.parquet"
                entry["file_path"] = str(raw_path)
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