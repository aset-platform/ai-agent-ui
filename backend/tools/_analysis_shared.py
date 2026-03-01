import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

# Module-level logger; mutable but required at module scope for pre-class logging.
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_RAW = _PROJECT_ROOT / "data" / "raw"
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"
_CHARTS_ANALYSIS = _PROJECT_ROOT / "charts" / "analysis"
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache"

_STOCK_REPO = None
_STOCK_REPO_INIT_ATTEMPTED = False


def _get_repo():
    """Return the :class:`~stocks.repository.StockRepository` singleton.

    Returns ``None`` silently when PyIceberg is unavailable.

    Returns:
        :class:`~stocks.repository.StockRepository` instance or ``None``.
    """
    import tools._analysis_shared as _self  # noqa: PLC0415 — module-attr access for monkeypatching
    if _self._STOCK_REPO_INIT_ATTEMPTED:
        return _self._STOCK_REPO
    _self._STOCK_REPO_INIT_ATTEMPTED = True
    try:
        _root = str(_PROJECT_ROOT)
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from stocks.repository import StockRepository  # noqa: PLC0415
        _self._STOCK_REPO = StockRepository()
        _logger.debug("StockRepository initialised (price_analysis_tool)")
    except Exception as _e:
        _logger.warning("StockRepository unavailable (Iceberg write disabled): %s", _e)
    return _self._STOCK_REPO


def _load_cache(ticker: str, key: str) -> Optional[str]:
    """Return cached result text for today if it exists, otherwise ``None``.

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
    _logger.debug("Cache saved: %s", path)


# Fix #6: delegate to shared helpers module to eliminate duplication.
# Fix #5: TTL cache is implemented in _helpers._load_currency.
from tools._helpers import _currency_symbol, _load_currency  # noqa: F401


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
        _logger.warning("Parquet file not found for %s: %s", ticker, file_path)
        return None
    df = pd.read_parquet(file_path, engine="pyarrow")
    df.index = pd.to_datetime(df.index)
    return df