"""TTL-cached wrapper around :class:`~stocks.repository.StockRepository`.

Caches read-heavy methods (freshness checks, currency lookups) to
avoid redundant Iceberg scans.  Write methods pass through and
invalidate the relevant cache entries.

Usage::

    from stocks.cached_repository import CachedRepository
    from stocks.repository import StockRepository

    repo = CachedRepository(StockRepository())
    currency = repo.get_currency("AAPL")  # cached for 5 min
"""

import logging
from datetime import date
from typing import Any, Dict

from cachetools import TTLCache

_logger = logging.getLogger(__name__)

_DEFAULT_TTL = 300  # 5 minutes
_DEFAULT_MAXSIZE = 256


class CachedRepository:
    """TTL-cached proxy for :class:`~stocks.repository.StockRepository`.

    Only read-heavy lookup methods are cached.  All other attribute
    access is delegated transparently to the wrapped repository.

    Args:
        repo: The underlying :class:`StockRepository` instance.
        ttl: Cache time-to-live in seconds.
        maxsize: Maximum number of cached entries.
    """

    def __init__(
        self,
        repo: Any,
        ttl: int = _DEFAULT_TTL,
        maxsize: int = _DEFAULT_MAXSIZE,
    ) -> None:
        self._repo = repo
        self._cache: TTLCache = TTLCache(
            maxsize=maxsize,
            ttl=ttl,
        )

    # ----------------------------------------------------------
    # Cached read methods
    # ----------------------------------------------------------

    def get_currency(self, ticker: str) -> str:
        """Return cached ISO currency code for *ticker*.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            ISO currency code string.
        """
        key = f"currency:{ticker.upper()}"
        if key in self._cache:
            return self._cache[key]
        result = self._repo.get_currency(ticker)
        self._cache[key] = result
        return result

    def get_latest_ohlcv_date(
        self,
        ticker: str,
    ) -> date | None:
        """Return cached latest OHLCV date for *ticker*.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            :class:`datetime.date` or ``None``.
        """
        key = f"ohlcv_date:{ticker.upper()}"
        if key in self._cache:
            return self._cache[key]
        result = self._repo.get_latest_ohlcv_date(ticker)
        self._cache[key] = result
        return result

    def get_latest_analysis_summary(
        self,
        ticker: str,
    ) -> Dict[str, Any] | None:
        """Return cached latest analysis summary for *ticker*.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Dict of analysis fields, or ``None``.
        """
        key = f"analysis:{ticker.upper()}"
        if key in self._cache:
            return self._cache[key]
        result = self._repo.get_latest_analysis_summary(
            ticker,
        )
        self._cache[key] = result
        return result

    def get_latest_forecast_run(
        self,
        ticker: str,
        horizon_months: int,
    ) -> Dict[str, Any] | None:
        """Return cached latest forecast run.

        Args:
            ticker: Stock ticker symbol.
            horizon_months: Forecast horizon (3, 6, or 9).

        Returns:
            Dict of forecast run fields, or ``None``.
        """
        key = f"forecast:{ticker.upper()}:{horizon_months}"
        if key in self._cache:
            return self._cache[key]
        result = self._repo.get_latest_forecast_run(
            ticker,
            horizon_months,
        )
        self._cache[key] = result
        return result

    # ----------------------------------------------------------
    # Cache invalidation on writes
    # ----------------------------------------------------------

    def insert_ohlcv(self, *args: Any, **kw: Any) -> Any:
        """Pass through and invalidate OHLCV date cache."""
        result = self._repo.insert_ohlcv(*args, **kw)
        self._invalidate_prefix("ohlcv_date:")
        return result

    def insert_analysis_summary(
        self,
        *args: Any,
        **kw: Any,
    ) -> Any:
        """Pass through and invalidate analysis cache."""
        result = self._repo.insert_analysis_summary(
            *args,
            **kw,
        )
        self._invalidate_prefix("analysis:")
        return result

    def insert_forecast_run(
        self,
        *args: Any,
        **kw: Any,
    ) -> Any:
        """Pass through and invalidate forecast cache."""
        result = self._repo.insert_forecast_run(
            *args,
            **kw,
        )
        self._invalidate_prefix("forecast:")
        return result

    # ----------------------------------------------------------
    # Transparent delegation
    # ----------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Delegate uncached attribute access to the repo.

        Args:
            name: Attribute name.

        Returns:
            The attribute from the underlying repository.
        """
        return getattr(self._repo, name)

    # ----------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------

    def _invalidate_prefix(self, prefix: str) -> None:
        """Remove all cache entries starting with *prefix*.

        Args:
            prefix: Key prefix to match.
        """
        keys = [k for k in self._cache if k.startswith(prefix)]
        for k in keys:
            del self._cache[k]
        if keys:
            _logger.debug(
                "Cache invalidated: %d entries (%s*)",
                len(keys),
                prefix,
            )
