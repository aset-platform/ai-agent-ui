"""Shared constants, lazy Iceberg repository, and data helpers for forecasting.

Attributes
----------
_PROJECT_ROOT : pathlib.Path
    Absolute path to the repository root.
_DATA_RAW : pathlib.Path
    Directory containing raw OHLCV parquet files.
_DATA_FORECASTS : pathlib.Path
    Directory where forecast parquet files are saved.
_DATA_METADATA : pathlib.Path
    Directory containing ticker JSON metadata.
_CHARTS_FORECASTS : pathlib.Path
    Directory where forecast HTML charts are saved.
_CACHE_DIR : pathlib.Path
    Directory used for same-day file caching.
_STOCK_REPO : object or None
    Lazy singleton :class:`~stocks.repository.StockRepository`.
_STOCK_REPO_INIT_ATTEMPTED : bool
    Guard flag so initialisation is only attempted once.
"""

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import holidays as holidays_lib
import pandas as pd

# Module-level logger; mutable but required at module scope for use before any class is instantiated.
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_RAW = _PROJECT_ROOT / "data" / "raw"
_DATA_FORECASTS = _PROJECT_ROOT / "data" / "forecasts"
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"
_CHARTS_FORECASTS = _PROJECT_ROOT / "charts" / "forecasts"
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache"

_STOCK_REPO = None
_STOCK_REPO_INIT_ATTEMPTED = False


def _get_repo():
    """Return the :class:`~stocks.repository.StockRepository` singleton.

    Returns ``None`` silently when PyIceberg is unavailable.

    Returns:
        :class:`~stocks.repository.StockRepository` instance or ``None``.
    """
    import tools._forecast_shared as _self  # noqa: PLC0415 — module-attr access for monkeypatching
    if _self._STOCK_REPO_INIT_ATTEMPTED:
        return _self._STOCK_REPO
    _self._STOCK_REPO_INIT_ATTEMPTED = True
    try:
        _root = str(_PROJECT_ROOT)
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from stocks.repository import StockRepository  # noqa: PLC0415
        _self._STOCK_REPO = StockRepository()
        _logger.debug("StockRepository initialised (forecasting_tool)")
    except Exception as _e:
        _logger.warning("StockRepository unavailable (Iceberg write disabled): %s", _e)
    return _self._STOCK_REPO


def _load_cache(ticker: str, key: str) -> Optional[str]:
    """Return cached result text for today if it exists, otherwise ``None``.

    Args:
        ticker: Stock ticker symbol (uppercased).
        key: Cache key string, e.g. ``"forecast_9m"``.

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
        key: Cache key string, e.g. ``"forecast_9m"``.
        result: The string result to cache.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{ticker}_{key}_{date.today()}.txt"
    path.write_text(result, encoding="utf-8")
    _logger.debug("Cache saved: %s", path)


# Fix #6: delegate to shared helpers module to eliminate duplication.
# Fix #5: TTL cache is implemented in _helpers._load_currency.
from tools._helpers import _currency_symbol, _load_currency  # noqa: F401


def _load_parquet(ticker: str) -> Optional[pd.DataFrame]:
    """Load the raw OHLCV parquet file for a ticker.

    Args:
        ticker: Stock ticker symbol (already uppercased).

    Returns:
        A :class:`pandas.DataFrame` with a DatetimeIndex, or ``None`` if
        the parquet file does not exist.
    """
    file_path = _DATA_RAW / f"{ticker}_raw.parquet"
    if not file_path.exists():
        _logger.warning("Parquet file not found for %s", ticker)
        return None
    df = pd.read_parquet(file_path, engine="pyarrow")
    df.index = pd.to_datetime(df.index)
    return df


def _build_holidays_df(years: range) -> pd.DataFrame:
    """Build a Prophet-compatible holidays DataFrame for US federal holidays.

    Args:
        years: Range of calendar years to include.

    Returns:
        DataFrame with columns ``holiday`` (str) and ``ds``
        (:class:`pandas.Timestamp`), ready to pass to :class:`Prophet`.
    """
    us_hols = holidays_lib.country_holidays("US", years=list(years))
    rows = [
        {"holiday": name, "ds": pd.Timestamp(dt)}
        for dt, name in us_hols.items()
    ]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["holiday", "ds"])