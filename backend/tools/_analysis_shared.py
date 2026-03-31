import logging
from datetime import date, timedelta

import pandas as pd
from tools._stock_shared import (  # noqa: F401 — re-exported
    _get_repo,
    _require_repo,
)

# Module-level logger; required at module scope.
_logger = logging.getLogger(__name__)


# Fix #6: delegate to shared helpers module to eliminate duplication.
# Fix #5: TTL cache is implemented in _helpers._load_currency.
from tools._helpers import _currency_symbol, _load_currency  # noqa: F401,E402


def _is_ohlcv_stale(df: pd.DataFrame) -> bool:
    """Return True if OHLCV data is more than 2 calendar days old.

    Allows for weekends/holidays — a 2-day gap is normal.
    Anything older triggers a refresh from yfinance.

    Args:
        df: OHLCV DataFrame with a ``date`` column.

    Returns:
        ``True`` when data needs refreshing.
    """
    if df.empty:
        return True
    latest = pd.to_datetime(df["date"]).max().date()
    gap = (date.today() - latest).days
    return gap > 2


def _auto_fetch(ticker: str) -> None:
    """Trigger a yfinance fetch to fill missing/stale Iceberg data.

    Imports ``fetch_stock_data`` lazily to avoid circular imports.

    Args:
        ticker: Stock ticker symbol (already uppercased).
    """
    try:
        from tools.stock_data_tool import (
            fetch_stock_data,
        )

        _logger.info(
            "Auto-fetching stale/missing OHLCV for %s",
            ticker,
        )
        fetch_stock_data.invoke({"ticker": ticker})
    except Exception as exc:
        _logger.warning(
            "Auto-fetch failed for %s: %s", ticker, exc
        )


def _load_ohlcv(ticker: str) -> pd.DataFrame | None:
    """Load OHLCV data for a ticker from Iceberg.

    If Iceberg has no data or the data is stale (>2 days old),
    automatically triggers a yfinance fetch before reading.

    Returns a DataFrame with a DatetimeIndex and columns
    ``Open``, ``High``, ``Low``, ``Close``, ``Adj Close``,
    ``Volume``.

    Args:
        ticker: Stock ticker symbol (already uppercased).

    Returns:
        A :class:`pandas.DataFrame` with a DatetimeIndex, or
        ``None`` if data is unavailable even after fetch.
    """
    try:
        repo = _require_repo()
        df = repo.get_ohlcv(ticker)

        if df.empty or _is_ohlcv_stale(df):
            _auto_fetch(ticker)
            df = repo.get_ohlcv(ticker)
            if df.empty:
                _logger.warning(
                    "No OHLCV data for %s after auto-fetch",
                    ticker,
                )
                return None

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        # Use adj_close only when it has meaningful coverage
        # (>50 %); otherwise fall back to close.
        use_adj = (
            "adj_close" in df.columns
            and df["adj_close"].notna().mean() > 0.5
        )
        adj_col = df["adj_close"] if use_adj else df["close"]
        result = pd.DataFrame(
            {
                "Open": df["open"],
                "High": df["high"],
                "Low": df["low"],
                "Close": df["close"],
                "Adj Close": adj_col,
                "Volume": df["volume"],
            }
        )
        result.index.name = "Date"
        result.index = pd.to_datetime(result.index)
        return result
    except Exception as exc:
        _logger.warning(
            "Iceberg OHLCV read failed for %s: %s",
            ticker,
            exc,
        )
        return None
