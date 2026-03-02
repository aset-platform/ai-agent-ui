"""Shared constants, lazy Iceberg repository, and data helpers for forecasting.

Attributes
----------
_PROJECT_ROOT : pathlib.Path
    Absolute path to the repository root.
_DATA_FORECASTS : pathlib.Path
    Directory where forecast parquet files are saved.
_CHARTS_FORECASTS : pathlib.Path
    Directory where forecast HTML charts are saved.
_CACHE_DIR : pathlib.Path
    Directory used for same-day file caching.
"""

import logging
from datetime import date
from pathlib import Path
from typing import Optional

import holidays as holidays_lib
import pandas as pd
from tools._stock_shared import _get_repo, _require_repo  # noqa: F401 — re-exported

# Module-level logger; mutable but required at module scope for use before any class is instantiated.
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_FORECASTS = _PROJECT_ROOT / "data" / "forecasts"
_CHARTS_FORECASTS = _PROJECT_ROOT / "charts" / "forecasts"
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache"


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


def _build_holidays_df(years: range) -> pd.DataFrame:
    """Build a Prophet-compatible holidays DataFrame for US federal holidays.

    Args:
        years: Range of calendar years to include.

    Returns:
        DataFrame with columns ``holiday`` (str) and ``ds``
        (:class:`pandas.Timestamp`), ready to pass to :class:`Prophet`.
    """
    us_hols = holidays_lib.country_holidays("US", years=list(years))
    rows = [{"holiday": name, "ds": pd.Timestamp(dt)} for dt, name in us_hols.items()]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["holiday", "ds"])
