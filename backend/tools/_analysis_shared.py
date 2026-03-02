import logging
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
from tools._stock_shared import _get_repo  # noqa: F401 — re-exported
from tools._stock_shared import _require_repo

# Module-level logger; mutable but required at module scope for pre-class logging.
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_CHARTS_ANALYSIS = _PROJECT_ROOT / "charts" / "analysis"
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache"


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
    """Load OHLCV data for a ticker from Iceberg.

    Returns a DataFrame with a DatetimeIndex and columns ``Open``, ``High``,
    ``Low``, ``Close``, ``Adj Close``, ``Volume`` — the same shape as the
    legacy ``pd.read_parquet(data/raw/{TICKER}_raw.parquet)`` output.

    Args:
        ticker: Stock ticker symbol (already uppercased).

    Returns:
        A :class:`pandas.DataFrame` with a DatetimeIndex, or ``None`` if no
        OHLCV data exists in Iceberg for this ticker.
    """
    try:
        repo = _require_repo()
        df = repo.get_ohlcv(ticker)
        if df.empty:
            _logger.warning("No OHLCV data in Iceberg for %s", ticker)
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        result = pd.DataFrame(
            {
                "Open": df["open"],
                "High": df["high"],
                "Low": df["low"],
                "Close": df["close"],
                "Adj Close": df["adj_close"]
                if "adj_close" in df.columns
                else df["close"],
                "Volume": df["volume"],
            }
        )
        result.index.name = "Date"
        result.index = pd.to_datetime(result.index)
        return result
    except Exception as exc:
        _logger.warning("Iceberg OHLCV read failed for %s: %s", ticker, exc)
        return None
