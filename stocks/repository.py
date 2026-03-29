"""Iceberg-backed repository for all stock market data tables.

This module provides :class:`StockRepository`, the single point of access
for reading and writing to the 9 ``stocks`` Iceberg tables.  No code
outside this module should interact with the tables directly.

Write semantics
---------------
- **registry** — upsert (copy-on-write): read full table, update
  or append the row for ``ticker``, overwrite.  Acceptable for
  this small (~30 row) table.
- **company_info** — append-only snapshots; never updated/deleted.
- **ohlcv** — append new rows; deduplication on ``(ticker, date)``
  at application level.  ``update_ohlcv_adj_close`` uses scoped
  delete-and-append (only the target ticker's rows are touched).
- **dividends** — append, deduplicate on ``(ticker, ex_date)``.
- **technical_indicators** — scoped delete-and-append per ticker.
- **analysis_summary** — append-only snapshots.
- **forecast_runs** — append-only per
  ``(ticker, horizon_months, run_date)``.
- **forecasts** — scoped delete-and-append per
  ``(ticker, horizon_months, run_date)``.
- **quarterly_results** — scoped delete-and-append per ticker.

PyIceberg quirks
----------------
- ``table.append()`` requires a ``pa.Table`` (not a
  ``RecordBatch``).
- ``TimestampType`` maps to ``pa.timestamp("us")`` — pass naive
  UTC datetimes.
- ``table.delete(delete_filter=expr)`` rewrites only affected
  data files, leaving other rows untouched.
- ``table.overwrite(df)`` replaces *all* data — only used for
  the small registry table.

Usage::

    from stocks.repository import StockRepository
    from datetime import date

    repo = StockRepository()
    repo.upsert_registry(
        "AAPL", date.today(), 2500,
        date(2015,1,2), date(2026,2,28), "us",
    )
    df = repo.get_ohlcv("AAPL")
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
from pyiceberg.exceptions import CommitFailedException

_logger = logging.getLogger(__name__)

_NAMESPACE = "stocks"
_COMPANY_INFO = f"{_NAMESPACE}.company_info"
_OHLCV = f"{_NAMESPACE}.ohlcv"
_DIVIDENDS = f"{_NAMESPACE}.dividends"
_TECHNICAL_INDICATORS = f"{_NAMESPACE}.technical_indicators"
_ANALYSIS_SUMMARY = f"{_NAMESPACE}.analysis_summary"
_FORECAST_RUNS = f"{_NAMESPACE}.forecast_runs"
_FORECASTS = f"{_NAMESPACE}.forecasts"
_QUARTERLY_RESULTS = f"{_NAMESPACE}.quarterly_results"
_CHAT_AUDIT_LOG = f"{_NAMESPACE}.chat_audit_log"
_PORTFOLIO = f"{_NAMESPACE}.portfolio_transactions"


def _now_utc() -> datetime:
    """Return current UTC time as a naive datetime.

    PyIceberg ``TimestampType`` requires naive datetimes, so
    ``tzinfo`` is stripped after construction.

    Returns:
        Naive :class:`datetime.datetime` in UTC.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_date(value: Any) -> date | None:
    """Coerce a value to a :class:`datetime.date`, or return ``None``.

    Args:
        value: A ``date``, ``datetime``, ISO string, or ``None``.

    Returns:
        A :class:`datetime.date` or ``None`` if conversion fails.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    """Convert *value* to float, returning ``None`` on failure or NaN/inf.

    Args:
        value: Any numeric-like value.

    Returns:
        Float or ``None``.
    """
    try:
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    """Convert *value* to int, returning ``None`` on failure.

    Args:
        value: Any numeric-like value.

    Returns:
        int or ``None``.
    """
    try:
        return int(value)
    except Exception:
        return None


class StockRepository:
    """Repository for all 8 ``stocks`` Iceberg tables.

    Instantiate once and reuse; the catalog is loaded lazily on first access.

    Example:
        >>> repo = StockRepository()  # doctest: +SKIP
        >>> repo.upsert_registry("AAPL", ...)  # doctest: +SKIP
    """

    def __init__(self) -> None:
        """Initialise the repository without loading the catalog yet."""
        self._catalog = None
        self._dirty_tables: set[str] = set()

    # ------------------------------------------------------------------
    # Catalog access
    # ------------------------------------------------------------------

    def _get_catalog(self):
        """Return (and cache) the Iceberg SqlCatalog.

        Returns:
            The loaded :class:`pyiceberg.catalog.sql.SqlCatalog` instance.
        """
        if self._catalog is None:
            from pyiceberg.catalog import load_catalog

            self._catalog = load_catalog("local")
        return self._catalog

    def _load_table(self, identifier: str):
        """Load an Iceberg table by its fully-qualified identifier.

        Args:
            identifier: e.g. ``"stocks.ohlcv"``.

        Returns:
            The loaded Iceberg table object.
        """
        return self._get_catalog().load_table(identifier)

    def _table_to_df(self, identifier: str) -> pd.DataFrame:
        """Read an entire Iceberg table into a pandas DataFrame.

        Args:
            identifier: Fully-qualified table name.

        Returns:
            pandas DataFrame with all rows, or an empty DataFrame on error.
        """
        try:
            tbl = self._load_table(identifier)
            if identifier in self._dirty_tables:
                tbl.refresh()
                self._dirty_tables.discard(identifier)
            return tbl.scan().to_pandas()
        except Exception as exc:
            _logger.warning("Could not read table %s: %s", identifier, exc)
            return pd.DataFrame()

    def _scan_ticker(
        self,
        identifier: str,
        ticker: str,
        selected_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """Scan a table filtered to a single ticker using predicate push-down.

        Attempts a server-side ``EqualTo("ticker", ticker)`` predicate first;
        falls back to a full table scan with Python-level filtering on failure.

        Args:
            identifier: Fully-qualified table name
                (e.g. ``"stocks.ohlcv"``).
            ticker: Stock ticker symbol (already uppercased).
            selected_fields: Optional list of column names to
                project.  ``None`` selects all columns.

        Returns:
            DataFrame containing only rows for *ticker*,
            or an empty DataFrame.
        """
        try:
            from pyiceberg.expressions import EqualTo

            tbl = self._load_table(identifier)
            if identifier in self._dirty_tables:
                tbl.refresh()
                self._dirty_tables.discard(identifier)
            scan_kwargs = {"row_filter": EqualTo("ticker", ticker)}
            if selected_fields:
                scan_kwargs["selected_fields"] = selected_fields
            scan = tbl.scan(**scan_kwargs)
            return scan.to_pandas()
        except Exception as exc:
            _logger.warning(
                "Predicate push-down failed for %s"
                " ticker=%s (%s); falling back.",
                identifier,
                ticker,
                exc,
            )
            df = self._table_to_df(identifier)
            if df.empty:
                return df
            filtered = df[df["ticker"] == ticker].copy()
            if selected_fields:
                cols = [c for c in selected_fields if c in filtered.columns]
                return filtered[cols]
            return filtered

    def _scan_tickers(
        self,
        identifier: str,
        tickers: list[str],
        selected_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """Scan a table for multiple tickers in one query.

        Uses ``In("ticker", tickers)`` predicate for
        Iceberg-level partition pruning.  Falls back to
        full scan with pandas ``isin()`` on error.

        Args:
            identifier: Fully-qualified table name.
            tickers: List of ticker symbols.
            selected_fields: Optional column projection.

        Returns:
            DataFrame with rows for all requested
            tickers, or empty DataFrame.
        """
        if not tickers:
            return pd.DataFrame()
        try:
            from pyiceberg.expressions import In

            tbl = self._load_table(identifier)
            if identifier in self._dirty_tables:
                tbl.refresh()
                self._dirty_tables.discard(identifier)
            scan_kwargs: dict[str, Any] = {
                "row_filter": In(
                    "ticker",
                    tickers,
                ),
            }
            if selected_fields:
                scan_kwargs["selected_fields"] = selected_fields
            return tbl.scan(**scan_kwargs).to_pandas()
        except Exception as exc:
            _logger.warning(
                "Batch scan failed for %s (%s);" " falling back to full scan.",
                identifier,
                exc,
            )
            df = self._table_to_df(identifier)
            if df.empty:
                return df
            filtered = df[df["ticker"].isin(tickers)].copy()
            if selected_fields:
                cols = [c for c in selected_fields if c in filtered.columns]
                return filtered[cols]
            return filtered

    def get_ohlcv_batch(
        self,
        tickers: list[str],
    ) -> pd.DataFrame:
        """Return OHLCV data for multiple tickers.

        Single Iceberg scan with ``In`` predicate
        instead of N individual scans.

        Args:
            tickers: List of ticker symbols.

        Returns:
            DataFrame sorted by ticker, date.
        """
        df = self._scan_tickers(
            _OHLCV,
            [t.upper() for t in tickers],
        )
        if df.empty:
            return df
        return df.sort_values(["ticker", "date"]).reset_index(drop=True)

    def get_technical_indicators_batch(
        self,
        tickers: list[str],
    ) -> pd.DataFrame:
        """Return technical indicators for tickers.

        Single Iceberg scan with ``In`` predicate.

        Args:
            tickers: List of ticker symbols.

        Returns:
            DataFrame sorted by ticker, date.
        """
        df = self._scan_tickers(
            _TECHNICAL_INDICATORS,
            [t.upper() for t in tickers],
        )
        if df.empty:
            return df
        return df.sort_values(["ticker", "date"]).reset_index(drop=True)

    def get_company_info_batch(
        self,
        tickers: list[str],
    ) -> pd.DataFrame:
        """Latest company info for multiple tickers.

        Single Iceberg scan, then dedup to latest
        ``fetched_at`` per ticker.

        Args:
            tickers: List of ticker symbols.

        Returns:
            DataFrame with one row per ticker.
        """
        df = self._scan_tickers(
            _COMPANY_INFO,
            [t.upper() for t in tickers],
        )
        if df.empty:
            return df
        if "fetched_at" in df.columns:
            df = df.sort_values("fetched_at")
        return df.drop_duplicates(
            subset=["ticker"],
            keep="last",
        ).reset_index(drop=True)

    def get_analysis_summary_batch(
        self,
        tickers: list[str],
    ) -> pd.DataFrame:
        """Latest analysis summary for tickers.

        Single Iceberg scan, then dedup to latest
        ``analysis_date`` per ticker.

        Args:
            tickers: List of ticker symbols.

        Returns:
            DataFrame with one row per ticker.
        """
        df = self._scan_tickers(
            _ANALYSIS_SUMMARY,
            [t.upper() for t in tickers],
        )
        if df.empty:
            return df
        if "analysis_date" in df.columns:
            df = df.sort_values("analysis_date")
        return df.drop_duplicates(
            subset=["ticker"],
            keep="last",
        ).reset_index(drop=True)

    def _scan_two_filters(
        self,
        identifier: str,
        col1: str,
        val1: Any,
        col2: str,
        val2: Any,
    ) -> pd.DataFrame:
        """Scan a table with two ``EqualTo`` predicates combined via ``And``.

        Falls back to full scan with Python filter on any predicate failure.

        Args:
            identifier: Fully-qualified table name.
            col1: First filter column name.
            val1: Value for first filter.
            col2: Second filter column name.
            val2: Value for second filter.

        Returns:
            Filtered DataFrame or an empty DataFrame.
        """
        try:
            from pyiceberg.expressions import And, EqualTo

            tbl = self._load_table(identifier)
            if identifier in self._dirty_tables:
                tbl.refresh()
                self._dirty_tables.discard(identifier)
            return tbl.scan(
                row_filter=And(EqualTo(col1, val1), EqualTo(col2, val2))
            ).to_pandas()
        except Exception as exc:
            _logger.warning(
                "Compound predicate failed for %s"
                " (%s); falling back to full scan.",
                identifier,
                exc,
            )
            df = self._table_to_df(identifier)
            if df.empty:
                return df
            return df[(df[col1] == val1) & (df[col2] == val2)].copy()

    def _load_table_and_scan(
        self, identifier: str
    ) -> tuple[Any, pd.DataFrame]:
        """Load a table and materialise its contents, returning both.

        Avoids the double load that occurs when code calls ``_table_to_df()``
        followed by ``_load_table()`` on the same identifier.

        Args:
            identifier: Fully-qualified table name.

        Returns:
            Tuple of ``(table_object, dataframe)``.  The DataFrame is empty
            on read failure; the table object is always returned.
        """
        tbl = self._load_table(identifier)
        if identifier in self._dirty_tables:
            tbl.refresh()
            self._dirty_tables.discard(identifier)
        try:
            df = tbl.scan().to_pandas()
        except Exception as exc:
            _logger.warning("Could not read table %s: %s", identifier, exc)
            df = pd.DataFrame()
        return tbl, df

    def _scan_ticker_date_range(
        self,
        identifier: str,
        ticker: str,
        date_col: str = "date",
        start: date | None = None,
        end: date | None = None,
        selected_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """Scan with ticker + date range predicates at Iceberg level.

        Pushes both ticker equality and date range bounds into the
        Iceberg scan so partition pruning and file-level min/max
        statistics can skip irrelevant data files.

        Falls back to ``_scan_ticker`` + pandas filtering on error.

        Args:
            identifier: Fully-qualified table name.
            ticker: Stock ticker symbol (already uppercased).
            date_col: Name of the date column to filter on.
            start: Inclusive start date (``None`` = no lower bound).
            end: Inclusive end date (``None`` = no upper bound).
            selected_fields: Optional column projection list.

        Returns:
            Filtered DataFrame sorted by *date_col* ascending.
        """
        try:
            from pyiceberg.expressions import (
                And,
                EqualTo,
                GreaterThanOrEqual,
                LessThanOrEqual,
            )

            tbl = self._load_table(identifier)
            if identifier in self._dirty_tables:
                tbl.refresh()
                self._dirty_tables.discard(identifier)

            row_filter = EqualTo("ticker", ticker)
            if start is not None:
                row_filter = And(
                    row_filter,
                    GreaterThanOrEqual(date_col, start),
                )
            if end is not None:
                row_filter = And(
                    row_filter,
                    LessThanOrEqual(date_col, end),
                )

            scan_kwargs: dict[str, Any] = {
                "row_filter": row_filter,
            }
            if selected_fields:
                scan_kwargs["selected_fields"] = selected_fields
            return tbl.scan(**scan_kwargs).to_pandas()
        except Exception as exc:
            _logger.warning(
                "Iceberg date-range scan failed for %s "
                "ticker=%s (%s); falling back.",
                identifier,
                ticker,
                exc,
            )
            df = self._scan_ticker(
                identifier,
                ticker,
                selected_fields,
            )
            if df.empty:
                return df
            if start is not None:
                df = df[pd.to_datetime(df[date_col]).dt.date >= start]
            if end is not None:
                df = df[pd.to_datetime(df[date_col]).dt.date <= end]
            return df

    def _scan_date_range(
        self,
        identifier: str,
        date_col: str,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Scan with date range predicates only (no ticker).

        Pushes date bounds into the Iceberg scan for
        partition pruning on date-partitioned tables
        like ``llm_usage``.

        Falls back to full scan + pandas filtering on
        error.

        Args:
            identifier: Fully-qualified table name.
            date_col: Date column name.
            start: Inclusive start date.
            end: Inclusive end date.

        Returns:
            Filtered DataFrame.
        """
        try:
            from pyiceberg.expressions import (
                And,
                GreaterThanOrEqual,
                LessThanOrEqual,
            )

            tbl = self._load_table(identifier)
            if identifier in self._dirty_tables:
                tbl.refresh()
                self._dirty_tables.discard(identifier)

            filters = []
            if start is not None:
                filters.append(GreaterThanOrEqual(date_col, start))
            if end is not None:
                filters.append(LessThanOrEqual(date_col, end))

            if len(filters) == 2:
                row_filter = And(filters[0], filters[1])
            elif len(filters) == 1:
                row_filter = filters[0]
            else:
                return tbl.scan().to_pandas()

            return tbl.scan(
                row_filter=row_filter,
            ).to_pandas()
        except Exception as exc:
            _logger.warning(
                "Iceberg date-range scan failed for " "%s (%s); falling back.",
                identifier,
                exc,
            )
            df = self._table_to_df(identifier)
            if df.empty:
                return df
            if start is not None:
                df = df[df[date_col] >= start]
            if end is not None:
                df = df[df[date_col] <= end]
            return df

    # ------------------------------------------------------------------
    # Retry helpers for Iceberg OCC
    # ------------------------------------------------------------------

    _MAX_RETRIES = 3
    _BACKOFF_SECONDS = (0.5, 1.0, 2.0)

    def _retry_commit(self, identifier, operation, *args, **kwargs):
        """Retry an Iceberg write on CommitFailedException.

        Reloads the table object on each retry so the
        snapshot is fresh.

        Args:
            identifier: Fully-qualified table name.
            operation: ``"append"``, ``"overwrite"``,
                or ``"delete"``.
            *args: Positional arguments forwarded to
                the table method.
            **kwargs: Keyword arguments forwarded to
                the table method (e.g.
                ``delete_filter``).

        Raises:
            CommitFailedException: If all retries are
                exhausted.
        """
        last_exc = None
        for attempt in range(self._MAX_RETRIES + 1):
            tbl = self._load_table(identifier)
            try:
                getattr(tbl, operation)(*args, **kwargs)
                self._dirty_tables.add(identifier)
                self._invalidate_cache(identifier)
                return
            except CommitFailedException as exc:
                last_exc = exc
                if attempt < self._MAX_RETRIES:
                    delay = self._BACKOFF_SECONDS[attempt]
                    _logger.warning(
                        "Iceberg commit conflict on %s "
                        "(%s), retry %d/%d in %.1fs",
                        identifier,
                        operation,
                        attempt + 1,
                        self._MAX_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    # Table → cache key patterns that must be
    # invalidated when the table is written to.
    _CACHE_INVALIDATION_MAP: dict[str, list[str]] = {
        "stocks.ohlcv": [
            "cache:chart:ohlcv:*",
            "cache:dash:watchlist:*",
            "cache:dash:home:*",
            "cache:dash:compare:*",
            "cache:insights:screener:*",
            "cache:insights:correlation:*",
        ],
        "stocks.technical_indicators": [
            "cache:chart:indicators:*",
            "cache:dash:analysis:*",
            "cache:dash:home:*",
            "cache:insights:screener:*",
        ],
        "stocks.analysis_summary": [
            "cache:dash:analysis:*",
            "cache:dash:home:*",
            "cache:insights:screener:*",
            "cache:insights:risk:*",
            "cache:insights:sectors:*",
        ],
        "stocks.forecast_runs": [
            "cache:dash:forecasts:*",
            "cache:dash:home:*",
            "cache:chart:forecast:*",
            "cache:insights:targets:*",
        ],
        "stocks.forecasts": [
            "cache:chart:forecast:*",
        ],
        "stocks.company_info": [
            "cache:dash:watchlist:*",
            "cache:dash:registry",
            "cache:insights:screener:*",
            "cache:insights:targets:*",
            "cache:insights:dividends:*",
            "cache:insights:risk:*",
            "cache:insights:sectors:*",
            "cache:insights:quarterly:*",
        ],
        "stocks.dividends": [
            "cache:insights:dividends:*",
        ],
        "stocks.quarterly_results": [
            "cache:insights:quarterly:*",
        ],
        "stocks.llm_usage": [
            "cache:dash:llm-usage:*",
            "cache:dash:home:*",
            "cache:admin:metrics",
        ],
        "stocks.registry": [
            "cache:dash:registry",
        ],
    }

    def _invalidate_cache(
        self,
        identifier: str,
    ) -> None:
        """Invalidate Redis cache keys after write.

        Uses :data:`_CACHE_INVALIDATION_MAP` to find
        which cache patterns correspond to the given
        Iceberg table identifier.

        Args:
            identifier: Fully-qualified table name
                (e.g. ``"stocks.ohlcv"``).
        """
        try:
            from cache import get_cache
        except ImportError:
            return
        cache = get_cache()
        patterns = self._CACHE_INVALIDATION_MAP.get(
            identifier,
            [],
        )
        for pattern in patterns:
            if "*" in pattern:
                cache.invalidate(pattern)
            else:
                cache.invalidate_exact(pattern)

    def _append_rows(self, identifier: str, arrow_table: pa.Table) -> None:
        """Append a PyArrow table to an Iceberg table.

        Retries automatically on concurrent commit
        conflicts.

        Args:
            identifier: Fully-qualified table name.
            arrow_table: Rows to append (must match
                the table schema).
        """
        self._retry_commit(identifier, "append", arrow_table)

    def _overwrite_table(self, identifier: str, arrow_table: pa.Table) -> None:
        """Overwrite an Iceberg table with retry.

        Retries automatically on concurrent commit
        conflicts.

        Args:
            identifier: Fully-qualified table name.
            arrow_table: Full replacement data.
        """
        self._retry_commit(identifier, "overwrite", arrow_table)

    def _delete_rows(self, identifier: str, delete_filter) -> None:
        """Delete rows matching a filter expression.

        Uses PyIceberg's row-level delete which rewrites
        only affected data files, leaving other tickers'
        data untouched.

        Args:
            identifier: Fully-qualified table name.
            delete_filter: A PyIceberg expression
                (e.g. ``EqualTo("ticker", "AAPL")``).
        """
        self._retry_commit(
            identifier,
            "delete",
            delete_filter=delete_filter,
        )

    # ------------------------------------------------------------------
    # Public wrappers for retention / admin callers
    # ------------------------------------------------------------------

    def load_table(self, identifier: str):
        """Load an Iceberg table (public API).

        Args:
            identifier: e.g. ``"stocks.ohlcv"``.

        Returns:
            The loaded Iceberg table object.
        """
        return self._load_table(identifier)

    def delete_rows(
        self,
        identifier: str,
        delete_filter,
    ) -> None:
        """Delete rows matching *delete_filter* (public API).

        Args:
            identifier: Fully-qualified table name.
            delete_filter: PyIceberg expression.
        """
        self._delete_rows(identifier, delete_filter)

    def get_latest_company_info_if_fresh(
        self, ticker: str, as_of_date: date
    ) -> dict[str, Any] | None:
        """Return the latest company info snapshot if fetched on *as_of_date*.

        Used as a cache check: callers can skip a Yahoo Finance call when the
        most recent snapshot was already fetched today.

        Args:
            ticker: Stock ticker symbol.
            as_of_date: Date to check freshness
                against (typically ``date.today()``).

        Returns:
            Dict of company info fields if the latest snapshot's ``fetched_at``
            date matches *as_of_date*, otherwise ``None``.
        """
        df = self._scan_ticker(_COMPANY_INFO, ticker.upper())
        if df.empty:
            return None
        latest = df.sort_values("fetched_at", ascending=False).iloc[0]
        fetched_at = latest.get("fetched_at")
        if fetched_at is None:
            return None
        fetched_date = _to_date(fetched_at)
        if fetched_date != as_of_date:
            return None
        return latest.to_dict()

    def get_currency(self, ticker: str) -> str:
        """Return the ISO currency code for *ticker*
        from the latest company info.

        Falls back to ``"USD"`` if no company info snapshot
        exists.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            ISO currency code string, e.g. ``"USD"``
            or ``"INR"``.
        """
        info = self.get_latest_company_info(ticker)
        if info is None:
            return "USD"
        return str(info.get("currency") or "USD")

    # ------------------------------------------------------------------
    # Company info
    # ------------------------------------------------------------------

    def insert_company_info(self, ticker: str, info: dict[str, Any]) -> None:
        """Append a company metadata snapshot for *ticker*.

        Args:
            ticker: Stock ticker symbol (already uppercased).
            info: Dict from ``yf.Ticker(ticker).info``
                plus optional extra fields.
        """
        row = pa.table(
            {
                "info_id": pa.array([str(uuid.uuid4())], pa.string()),
                "ticker": pa.array([ticker], pa.string()),
                "company_name": pa.array(
                    [
                        str(
                            info.get("company_name")
                            or info.get("longName")
                            or ""
                        )
                    ],
                    pa.string(),
                ),
                "sector": pa.array([info.get("sector")], pa.string()),
                "industry": pa.array([info.get("industry")], pa.string()),
                "market_cap": pa.array(
                    [
                        _safe_int(
                            info.get("market_cap") or info.get("marketCap")
                        )
                    ],
                    pa.int64(),
                ),
                "pe_ratio": pa.array(
                    [
                        _safe_float(
                            info.get("pe_ratio") or info.get("trailingPE")
                        )
                    ],
                    pa.float64(),
                ),
                "week_52_high": pa.array(
                    [
                        _safe_float(
                            info.get("52w_high")
                            or info.get("fiftyTwoWeekHigh")
                        )
                    ],
                    pa.float64(),
                ),
                "week_52_low": pa.array(
                    [
                        _safe_float(
                            info.get("52w_low") or info.get("fiftyTwoWeekLow")
                        )
                    ],
                    pa.float64(),
                ),
                "current_price": pa.array(
                    [
                        _safe_float(
                            info.get("current_price")
                            or info.get("currentPrice")
                        )
                    ],
                    pa.float64(),
                ),
                "currency": pa.array(
                    [str(info.get("currency") or "USD")], pa.string()
                ),
                "fetched_at": pa.array([_now_utc()], pa.timestamp("us")),
                "exchange": pa.array([info.get("exchange")], pa.string()),
                "country": pa.array([info.get("country")], pa.string()),
                "employees": pa.array(
                    [_safe_int(info.get("fullTimeEmployees"))], pa.int64()
                ),
                "dividend_yield": pa.array(
                    [_safe_float(info.get("dividendYield"))], pa.float64()
                ),
                "beta": pa.array(
                    [_safe_float(info.get("beta"))], pa.float64()
                ),
                "book_value": pa.array(
                    [_safe_float(info.get("bookValue"))], pa.float64()
                ),
                "price_to_book": pa.array(
                    [_safe_float(info.get("priceToBook"))], pa.float64()
                ),
                "earnings_growth": pa.array(
                    [_safe_float(info.get("earningsGrowth"))], pa.float64()
                ),
                "revenue_growth": pa.array(
                    [_safe_float(info.get("revenueGrowth"))], pa.float64()
                ),
                "profit_margins": pa.array(
                    [_safe_float(info.get("profitMargins"))], pa.float64()
                ),
                "avg_volume": pa.array(
                    [_safe_int(info.get("averageVolume"))], pa.int64()
                ),
                "float_shares": pa.array(
                    [_safe_int(info.get("floatShares"))], pa.int64()
                ),
                "short_ratio": pa.array(
                    [_safe_float(info.get("shortRatio"))], pa.float64()
                ),
                "analyst_target": pa.array(
                    [_safe_float(info.get("targetMeanPrice"))], pa.float64()
                ),
                "recommendation": pa.array(
                    [_safe_float(info.get("recommendationMean"))], pa.float64()
                ),
            }
        )
        self._append_rows(_COMPANY_INFO, row)
        _logger.debug("company_info snapshot appended for %s", ticker)

    def get_latest_company_info(self, ticker: str) -> dict[str, Any] | None:
        """Return the most recent company metadata snapshot for *ticker*.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Dict of company info fields, or ``None`` if no record exists.
        """
        df = self._scan_ticker(_COMPANY_INFO, ticker.upper())
        if df.empty:
            return None
        latest = df.sort_values("fetched_at", ascending=False).iloc[0]
        return latest.to_dict()

    def get_all_latest_company_info(
        self,
        limit: int | None = None,
        offset: int = 0,
    ) -> pd.DataFrame:
        """Return the most recent snapshot for every ticker.

        Args:
            limit: Maximum number of rows to return after grouping.
                   ``None`` returns all rows.
            offset: Number of rows to skip (for pagination).

        Returns:
            DataFrame with one row per ticker (latest ``fetched_at``).
        """
        df = self._table_to_df(_COMPANY_INFO)
        if df.empty:
            return df
        result = (
            df.sort_values("fetched_at", ascending=False)
            .groupby("ticker", as_index=False)
            .first()
        )
        if offset:
            result = result.iloc[offset:]
        if limit is not None:
            result = result.iloc[:limit]
        return result

    # ------------------------------------------------------------------
    # OHLCV
    # ------------------------------------------------------------------

    def insert_ohlcv(self, ticker: str, df: pd.DataFrame) -> int:
        """Append new OHLCV rows for *ticker*,
        skipping existing (ticker, date) pairs.

        Uses predicate push-down to fetch only existing dates for this ticker,
        avoiding a full table scan.  Deduplication uses :class:`datetime.date`
        objects (not string conversion) for correctness and speed.

        Args:
            ticker: Stock ticker symbol (already uppercased).
            df: DataFrame with DatetimeIndex and
                columns Open, High, Low, Close,
                Adj Close (optional), Volume as returned by yfinance.

        Returns:
            Number of new rows actually inserted.
        """
        if df.empty:
            return 0

        # Normalise index to date objects
        # (Fix #10: use date objects not strings)
        all_dates = pd.to_datetime(
            df.index
        ).date  # numpy array of date objects

        # Fix #1 + #2: load table once; predicate push-down for existing dates
        try:
            from pyiceberg.expressions import EqualTo

            tbl = self._load_table(_OHLCV)
            existing_arrow = tbl.scan(
                row_filter=EqualTo("ticker", ticker),
                selected_fields=("date",),
            ).to_arrow()
            if len(existing_arrow) > 0:
                existing_dates: set = {
                    _to_date(d) for d in existing_arrow["date"].to_pylist()
                }
            else:
                existing_dates = set()
        except Exception as exc:
            _logger.warning(
                "OHLCV predicate scan failed for %s (%s); falling back.",
                ticker,
                exc,
            )
            tbl = self._load_table(_OHLCV)
            full_df = tbl.scan().to_pandas()
            if not full_df.empty:
                mask = full_df["ticker"] == ticker
                existing_dates = {
                    _to_date(d) for d in full_df.loc[mask, "date"]
                }
            else:
                existing_dates = set()

        # Fix #3: vectorised new-row selection using boolean mask
        keep = [d not in existing_dates for d in all_dates]
        if not any(keep):
            _logger.debug("No new OHLCV rows to insert for %s", ticker)
            return 0

        keep_indices = [i for i, k in enumerate(keep) if k]
        new_dates = [all_dates[i] for i in keep_indices]
        filtered = df.iloc[keep_indices]
        now = _now_utc()

        adj_col = "Adj Close" if "Adj Close" in filtered.columns else None

        arrow_tbl = pa.table(
            {
                "ticker": pa.array([ticker] * len(new_dates), pa.string()),
                "date": pa.array(new_dates, pa.date32()),
                "open": pa.array(
                    [_safe_float(v) for v in filtered["Open"]], pa.float64()
                ),
                "high": pa.array(
                    [_safe_float(v) for v in filtered["High"]], pa.float64()
                ),
                "low": pa.array(
                    [_safe_float(v) for v in filtered["Low"]], pa.float64()
                ),
                "close": pa.array(
                    [_safe_float(v) for v in filtered["Close"]], pa.float64()
                ),
                "adj_close": pa.array(
                    [
                        _safe_float(v)
                        for v in (
                            filtered[adj_col]
                            if adj_col
                            else [None] * len(filtered)
                        )
                    ],
                    pa.float64(),
                ),
                "volume": pa.array(
                    [_safe_int(v) for v in filtered["Volume"]], pa.int64()
                ),
                "fetched_at": pa.array(
                    [now] * len(new_dates), pa.timestamp("us")
                ),
            }
        )
        self._append_rows(_OHLCV, arrow_tbl)
        _logger.debug(
            "Inserted %d new OHLCV rows for %s", len(new_dates), ticker
        )
        return len(new_dates)

    def get_ohlcv(
        self,
        ticker: str,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Return OHLCV data for *ticker*, optionally filtered by date range.

        Uses Iceberg-level date predicates for partition pruning
        and file-level statistics filtering when *start*/*end* are
        provided.

        Args:
            ticker: Stock ticker symbol.
            start: Inclusive start date (``None`` = no lower bound).
            end: Inclusive end date (``None`` = no upper bound).

        Returns:
            DataFrame sorted by date ascending with columns:
            ticker, date, open, high, low, close, adj_close, volume.
        """
        if start or end:
            df = self._scan_ticker_date_range(
                _OHLCV,
                ticker.upper(),
                date_col="date",
                start=start,
                end=end,
            )
        else:
            df = self._scan_ticker(_OHLCV, ticker.upper())
        if df.empty:
            return df
        return df.sort_values("date").reset_index(drop=True)

    def get_latest_ohlcv_date(self, ticker: str) -> date | None:
        """Return the most recent OHLCV date stored for *ticker*.

        Used by the delta fetch logic to determine how much new data to fetch.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            :class:`datetime.date` or ``None`` if no data exists.
        """
        df = self._scan_ticker(
            _OHLCV,
            ticker.upper(),
            selected_fields=["ticker", "date"],
        )
        if df.empty:
            return None
        latest = pd.to_datetime(df["date"]).max()
        return latest.date() if pd.notna(latest) else None

    def update_ohlcv_adj_close(self, ticker: str, adj_close_map: dict) -> int:
        """Update ``adj_close`` values for existing OHLCV rows.

        Scoped delete-and-append: reads only the target
        ticker's rows, modifies ``adj_close``, deletes the
        old rows for this ticker, and appends the updated
        rows.  Other tickers' data is never touched.

        Args:
            ticker: Uppercase ticker symbol.
            adj_close_map: Dict mapping :class:`datetime.date`
                objects to ``adj_close`` float values.

        Returns:
            Number of rows updated.
        """
        if not adj_close_map:
            return 0

        ticker = ticker.upper()
        ticker_df = self._scan_ticker(_OHLCV, ticker)
        if ticker_df.empty:
            _logger.warning(
                "No OHLCV rows for %s — nothing to update.",
                ticker,
            )
            return 0

        # Normalise date column for lookups
        ticker_df["_date_key"] = pd.to_datetime(ticker_df["date"]).dt.date

        updated = 0
        for idx in ticker_df.index:
            d = ticker_df.at[idx, "_date_key"]
            if d in adj_close_map:
                new_val = _safe_float(adj_close_map[d])
                if new_val is not None:
                    ticker_df.at[idx, "adj_close"] = new_val
                    updated += 1

        ticker_df.drop(columns=["_date_key"], inplace=True)

        if updated == 0:
            _logger.debug("No adj_close updates needed for %s", ticker)
            return 0

        # Rebuild Arrow table for this ticker only
        now = _now_utc()
        arrow_tbl = pa.table(
            {
                "ticker": pa.array(
                    ticker_df["ticker"].tolist(),
                    pa.string(),
                ),
                "date": pa.array(
                    pd.to_datetime(ticker_df["date"]).dt.date.tolist(),
                    pa.date32(),
                ),
                "open": pa.array(
                    [_safe_float(v) for v in ticker_df["open"]],
                    pa.float64(),
                ),
                "high": pa.array(
                    [_safe_float(v) for v in ticker_df["high"]],
                    pa.float64(),
                ),
                "low": pa.array(
                    [_safe_float(v) for v in ticker_df["low"]],
                    pa.float64(),
                ),
                "close": pa.array(
                    [_safe_float(v) for v in ticker_df["close"]],
                    pa.float64(),
                ),
                "adj_close": pa.array(
                    [_safe_float(v) for v in ticker_df["adj_close"]],
                    pa.float64(),
                ),
                "volume": pa.array(
                    [_safe_int(v) for v in ticker_df["volume"]],
                    pa.int64(),
                ),
                "fetched_at": pa.array(
                    [now] * len(ticker_df),
                    pa.timestamp("us"),
                ),
            }
        )

        from pyiceberg.expressions import EqualTo

        self._delete_rows(_OHLCV, EqualTo("ticker", ticker))
        self._append_rows(_OHLCV, arrow_tbl)
        _logger.info(
            "Updated %d adj_close rows for %s",
            updated,
            ticker,
        )
        return updated

    # ------------------------------------------------------------------
    # Dividends
    # ------------------------------------------------------------------

    def insert_dividends(
        self, ticker: str, df: pd.DataFrame, currency: str = "USD"
    ) -> int:
        """Append dividend rows for *ticker*,
        skipping existing (ticker, ex_date) pairs.

        Uses predicate push-down for the existing-date check.  Deduplication
        uses :class:`datetime.date` objects (not string conversion).

        Args:
            ticker: Stock ticker symbol.
            df: DataFrame with columns ``date``
                and ``dividend`` (from yfinance).
            currency: ISO currency code for this ticker, e.g. ``"INR"``.
                Defaults to ``"USD"``.

        Returns:
            Number of new rows inserted.
        """
        if df.empty:
            return 0

        # Fix #1 + #2: load table once;
        # predicate push-down for existing ex_dates
        try:
            from pyiceberg.expressions import EqualTo

            tbl = self._load_table(_DIVIDENDS)
            existing_arrow = tbl.scan(
                row_filter=EqualTo("ticker", ticker),
                selected_fields=("ex_date",),
            ).to_arrow()
            if len(existing_arrow) > 0:
                existing_dates: set = {
                    _to_date(d) for d in existing_arrow["ex_date"].to_pylist()
                }
            else:
                existing_dates = set()
        except Exception as exc:
            _logger.warning(
                "Dividends predicate scan failed for %s (%s); falling back.",
                ticker,
                exc,
            )
            tbl = self._load_table(_DIVIDENDS)
            full_df = tbl.scan().to_pandas()
            if not full_df.empty:
                mask = full_df["ticker"] == ticker
                existing_dates = {
                    _to_date(d) for d in full_df.loc[mask, "ex_date"]
                }
            else:
                existing_dates = set()

        now = _now_utc()
        # Fix #3: build lists directly without iterrows materialising full rows
        tickers_out: list[str] = []
        ex_dates_out: list[date] = []
        amounts_out: list[float | None] = []
        currencies_out: list[str] = []
        fetched_at_out: list[datetime] = []

        for idx, row in df.iterrows():
            ex_dt = _to_date(row.get("date", idx))
            if ex_dt is None or ex_dt in existing_dates:
                continue
            tickers_out.append(ticker)
            ex_dates_out.append(ex_dt)
            amounts_out.append(_safe_float(row.get("dividend", row.iloc[0])))
            currencies_out.append(currency)
            fetched_at_out.append(now)

        if not tickers_out:
            return 0

        arrow_tbl = pa.table(
            {
                "ticker": pa.array(tickers_out, pa.string()),
                "ex_date": pa.array(ex_dates_out, pa.date32()),
                "dividend_amount": pa.array(amounts_out, pa.float64()),
                "currency": pa.array(currencies_out, pa.string()),
                "fetched_at": pa.array(fetched_at_out, pa.timestamp("us")),
            }
        )
        self._append_rows(_DIVIDENDS, arrow_tbl)
        _logger.debug(
            "Inserted %d new dividend rows for %s", len(tickers_out), ticker
        )
        return len(tickers_out)

    def get_dividends(self, ticker: str) -> pd.DataFrame:
        """Return dividend history for *ticker* sorted by ex_date ascending.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            DataFrame with columns: ticker, ex_date, dividend_amount, currency.
        """
        df = self._scan_ticker(_DIVIDENDS, ticker.upper())
        if df.empty:
            return df
        return df.sort_values("ex_date").reset_index(drop=True)

    # ------------------------------------------------------------------
    # Technical indicators
    # ------------------------------------------------------------------

    def upsert_technical_indicators(
        self, ticker: str, df: pd.DataFrame
    ) -> None:
        """Insert or update technical indicator rows for *ticker*.

        Replaces all existing rows for this ticker (partition overwrite).

        Args:
            ticker: Stock ticker symbol.
            df: DataFrame with DatetimeIndex and indicator columns:
                sma_50, sma_200, ema_20, rsi_14, macd, macd_signal, macd_hist,
                bb_upper, bb_middle, bb_lower, atr_14. May also contain
                a ``daily_return`` column.
        """
        if df.empty:
            return

        now = _now_utc()
        dates = pd.to_datetime(df.index).date

        # Fix #9: pre-compute column set once (avoid repeated O(cols) lookup)
        col_set = set(df.columns)

        def _get(canonical: str, alt: str) -> list[float | None]:
            """Extract a column by canonical or alternate name."""
            col = (
                canonical
                if canonical in col_set
                else (alt if alt in col_set else None)
            )
            if col is None:
                return [None] * len(df)
            return [_safe_float(v) for v in df[col]]

        # Fix #9: remove unused inner _col function; build arrays column-wise
        arrow_tbl = pa.table(
            {
                "ticker": pa.array([ticker] * len(dates), pa.string()),
                "date": pa.array(list(dates), pa.date32()),
                "sma_50": pa.array(_get("SMA_50", "sma_50"), pa.float64()),
                "sma_200": pa.array(_get("SMA_200", "sma_200"), pa.float64()),
                "ema_20": pa.array(_get("EMA_20", "ema_20"), pa.float64()),
                "rsi_14": pa.array(_get("RSI_14", "rsi_14"), pa.float64()),
                "macd": pa.array(_get("MACD", "macd"), pa.float64()),
                "macd_signal": pa.array(
                    _get("MACD_Signal", "macd_signal"), pa.float64()
                ),
                "macd_hist": pa.array(
                    _get("MACD_Hist", "macd_hist"), pa.float64()
                ),
                "bb_upper": pa.array(
                    _get("BB_Upper", "bb_upper"), pa.float64()
                ),
                "bb_middle": pa.array(
                    _get("BB_Middle", "bb_middle"), pa.float64()
                ),
                "bb_lower": pa.array(
                    _get("BB_Lower", "bb_lower"), pa.float64()
                ),
                "atr_14": pa.array(_get("ATR_14", "atr_14"), pa.float64()),
                "daily_return": pa.array(
                    (
                        [_safe_float(v) for v in df["daily_return"]]
                        if "daily_return" in col_set
                        else [None] * len(df)
                    ),
                    pa.float64(),
                ),
                "computed_at": pa.array(
                    [now] * len(dates), pa.timestamp("us")
                ),
            }
        )

        # Scoped delete-and-append: remove only this ticker's
        # rows, then append fresh indicators.
        from pyiceberg.expressions import EqualTo

        try:
            self._delete_rows(
                _TECHNICAL_INDICATORS,
                EqualTo("ticker", ticker),
            )
        except Exception:
            _logger.debug(
                "Delete before upsert failed for " "technical_indicators/%s",
                ticker,
                exc_info=True,
            )
        self._append_rows(_TECHNICAL_INDICATORS, arrow_tbl)

        _logger.debug(
            "Technical indicators upserted for %s (%d rows)",
            ticker,
            len(dates),
        )

    def get_technical_indicators(
        self,
        ticker: str,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Return technical indicator rows for *ticker*.

        Uses Iceberg-level date predicates for efficient
        partition pruning when date bounds are provided.

        Args:
            ticker: Stock ticker symbol.
            start: Inclusive start date.
            end: Inclusive end date.

        Returns:
            DataFrame sorted by date ascending.
        """
        if start or end:
            df = self._scan_ticker_date_range(
                _TECHNICAL_INDICATORS,
                ticker.upper(),
                date_col="date",
                start=start,
                end=end,
            )
        else:
            df = self._scan_ticker(
                _TECHNICAL_INDICATORS,
                ticker.upper(),
            )
        if df.empty:
            return df
        return df.sort_values("date").reset_index(drop=True)

    # ------------------------------------------------------------------
    # Analysis summary
    # ------------------------------------------------------------------

    def insert_analysis_summary(
        self, ticker: str, summary: dict[str, Any]
    ) -> None:
        """Append a daily analysis summary snapshot for *ticker*.

        Args:
            ticker: Stock ticker symbol.
            summary: Dict with keys matching the
                ``stocks.analysis_summary`` schema.
                     ``analysis_date`` defaults to today if not provided.
        """
        today = summary.get("analysis_date") or date.today()
        row = pa.table(
            {
                "summary_id": pa.array([str(uuid.uuid4())], pa.string()),
                "ticker": pa.array([ticker], pa.string()),
                "analysis_date": pa.array([_to_date(today)], pa.date32()),
                "bull_phase_pct": pa.array(
                    [_safe_float(summary.get("bull_phase_pct"))], pa.float64()
                ),
                "bear_phase_pct": pa.array(
                    [_safe_float(summary.get("bear_phase_pct"))], pa.float64()
                ),
                "max_drawdown_pct": pa.array(
                    [_safe_float(summary.get("max_drawdown_pct"))],
                    pa.float64(),
                ),
                "max_drawdown_duration_days": pa.array(
                    [_safe_int(summary.get("max_drawdown_duration_days"))],
                    pa.int64(),
                ),
                "annualized_volatility_pct": pa.array(
                    [_safe_float(summary.get("annualized_volatility_pct"))],
                    pa.float64(),
                ),
                "annualized_return_pct": pa.array(
                    [_safe_float(summary.get("annualized_return_pct"))],
                    pa.float64(),
                ),
                "sharpe_ratio": pa.array(
                    [_safe_float(summary.get("sharpe_ratio"))], pa.float64()
                ),
                "all_time_high": pa.array(
                    [_safe_float(summary.get("all_time_high"))], pa.float64()
                ),
                "all_time_high_date": pa.array(
                    [_to_date(summary.get("all_time_high_date"))], pa.date32()
                ),
                "all_time_low": pa.array(
                    [_safe_float(summary.get("all_time_low"))], pa.float64()
                ),
                "all_time_low_date": pa.array(
                    [_to_date(summary.get("all_time_low_date"))], pa.date32()
                ),
                "support_levels": pa.array(
                    [summary.get("support_levels")], pa.string()
                ),
                "resistance_levels": pa.array(
                    [summary.get("resistance_levels")], pa.string()
                ),
                "sma_50_signal": pa.array(
                    [summary.get("sma_50_signal")], pa.string()
                ),
                "sma_200_signal": pa.array(
                    [summary.get("sma_200_signal")], pa.string()
                ),
                "rsi_signal": pa.array(
                    [summary.get("rsi_signal")], pa.string()
                ),
                "macd_signal_text": pa.array(
                    [summary.get("macd_signal_text")], pa.string()
                ),
                "best_month": pa.array(
                    [summary.get("best_month")], pa.string()
                ),
                "worst_month": pa.array(
                    [summary.get("worst_month")], pa.string()
                ),
                "best_year": pa.array([summary.get("best_year")], pa.string()),
                "worst_year": pa.array(
                    [summary.get("worst_year")], pa.string()
                ),
                "computed_at": pa.array([_now_utc()], pa.timestamp("us")),
            }
        )
        self._append_rows(_ANALYSIS_SUMMARY, row)
        _logger.debug("analysis_summary appended for %s", ticker)

    def get_latest_analysis_summary(
        self, ticker: str
    ) -> dict[str, Any] | None:
        """Return the most recent analysis summary for *ticker*.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Dict of analysis fields, or ``None`` if no record exists.
        """
        df = self._scan_ticker(
            _ANALYSIS_SUMMARY,
            ticker.upper(),
        )
        if df.empty:
            return None
        return (
            df.sort_values(
                "analysis_date",
                ascending=False,
            )
            .iloc[0]
            .to_dict()
        )

    def get_all_latest_analysis_summary(
        self,
        limit: int | None = None,
        offset: int = 0,
    ) -> pd.DataFrame:
        """Return the most recent analysis summary snapshot for every ticker.

        Args:
            limit: Maximum number of rows to return after grouping.
                   ``None`` returns all rows.
            offset: Number of rows to skip (for pagination).

        Returns:
            DataFrame with one row per ticker (latest ``analysis_date``),
            or an empty DataFrame when the table has no rows.
        """
        df = self._table_to_df(_ANALYSIS_SUMMARY)
        if df.empty:
            return df
        result = (
            df.sort_values("analysis_date", ascending=False)
            .groupby("ticker", as_index=False)
            .first()
        )
        if offset:
            result = result.iloc[offset:]
        if limit is not None:
            result = result.iloc[:limit]
        return result

    def get_analysis_history(self, ticker: str) -> pd.DataFrame:
        """Return all analysis summary rows for
        *ticker* sorted by date ascending.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            DataFrame sorted by analysis_date.
        """
        df = self._scan_ticker(_ANALYSIS_SUMMARY, ticker.upper())
        if df.empty:
            return df
        return df.sort_values("analysis_date").reset_index(drop=True)

    # ------------------------------------------------------------------
    # Forecast runs
    # ------------------------------------------------------------------

    def insert_forecast_run(
        self,
        ticker: str,
        horizon_months: int,
        run_dict: dict[str, Any],
    ) -> None:
        """Append a forecast run metadata row.

        Args:
            ticker: Stock ticker symbol.
            horizon_months: Prophet forecast horizon (3, 6, or 9).
            run_dict: Dict with keys matching ``stocks.forecast_runs`` schema.
        """
        today = run_dict.get("run_date") or date.today()
        row = pa.table(
            {
                "run_id": pa.array([str(uuid.uuid4())], pa.string()),
                "ticker": pa.array([ticker], pa.string()),
                "horizon_months": pa.array([int(horizon_months)], pa.int32()),
                "run_date": pa.array([_to_date(today)], pa.date32()),
                "sentiment": pa.array(
                    [run_dict.get("sentiment")], pa.string()
                ),
                "current_price_at_run": pa.array(
                    [_safe_float(run_dict.get("current_price_at_run"))],
                    pa.float64(),
                ),
                "target_3m_date": pa.array(
                    [_to_date(run_dict.get("target_3m_date"))], pa.date32()
                ),
                "target_3m_price": pa.array(
                    [_safe_float(run_dict.get("target_3m_price"))],
                    pa.float64(),
                ),
                "target_3m_pct_change": pa.array(
                    [_safe_float(run_dict.get("target_3m_pct_change"))],
                    pa.float64(),
                ),
                "target_3m_lower": pa.array(
                    [_safe_float(run_dict.get("target_3m_lower"))],
                    pa.float64(),
                ),
                "target_3m_upper": pa.array(
                    [_safe_float(run_dict.get("target_3m_upper"))],
                    pa.float64(),
                ),
                "target_6m_date": pa.array(
                    [_to_date(run_dict.get("target_6m_date"))], pa.date32()
                ),
                "target_6m_price": pa.array(
                    [_safe_float(run_dict.get("target_6m_price"))],
                    pa.float64(),
                ),
                "target_6m_pct_change": pa.array(
                    [_safe_float(run_dict.get("target_6m_pct_change"))],
                    pa.float64(),
                ),
                "target_6m_lower": pa.array(
                    [_safe_float(run_dict.get("target_6m_lower"))],
                    pa.float64(),
                ),
                "target_6m_upper": pa.array(
                    [_safe_float(run_dict.get("target_6m_upper"))],
                    pa.float64(),
                ),
                "target_9m_date": pa.array(
                    [_to_date(run_dict.get("target_9m_date"))], pa.date32()
                ),
                "target_9m_price": pa.array(
                    [_safe_float(run_dict.get("target_9m_price"))],
                    pa.float64(),
                ),
                "target_9m_pct_change": pa.array(
                    [_safe_float(run_dict.get("target_9m_pct_change"))],
                    pa.float64(),
                ),
                "target_9m_lower": pa.array(
                    [_safe_float(run_dict.get("target_9m_lower"))],
                    pa.float64(),
                ),
                "target_9m_upper": pa.array(
                    [_safe_float(run_dict.get("target_9m_upper"))],
                    pa.float64(),
                ),
                "mae": pa.array(
                    [_safe_float(run_dict.get("mae"))], pa.float64()
                ),
                "rmse": pa.array(
                    [_safe_float(run_dict.get("rmse"))], pa.float64()
                ),
                "mape": pa.array(
                    [_safe_float(run_dict.get("mape"))], pa.float64()
                ),
                "computed_at": pa.array([_now_utc()], pa.timestamp("us")),
            }
        )
        self._append_rows(_FORECAST_RUNS, row)
        _logger.debug(
            "forecast_run appended for %s %dm", ticker, horizon_months
        )

    def get_latest_forecast_run(
        self, ticker: str, horizon_months: int
    ) -> dict[str, Any] | None:
        """Return the most recent forecast run for
        *ticker* and *horizon_months*.

        Args:
            ticker: Stock ticker symbol.
            horizon_months: Forecast horizon (3, 6, or 9).

        Returns:
            Dict of forecast run fields, or ``None`` if no record exists.
        """
        df = self._scan_two_filters(
            _FORECAST_RUNS,
            "ticker",
            ticker.upper(),
            "horizon_months",
            int(horizon_months),
        )
        if df.empty:
            return None
        return df.sort_values("run_date", ascending=False).iloc[0].to_dict()

    def get_all_latest_forecast_runs(
        self, horizon_months: int
    ) -> pd.DataFrame:
        """Return the most recent forecast run per ticker.

        Reads the ``forecast_runs`` table once, filters by
        *horizon_months*, and keeps only the row with the
        latest ``run_date`` for each ticker.  Pattern matches
        :meth:`get_all_latest_company_info`.

        Args:
            horizon_months: Forecast horizon (3, 6, or 9).

        Returns:
            DataFrame with one row per ticker (latest
            ``run_date``), or an empty DataFrame.
        """
        df = self._table_to_df(_FORECAST_RUNS)
        if df.empty:
            return df
        filtered = df[df["horizon_months"] == int(horizon_months)]
        if filtered.empty:
            return pd.DataFrame()
        return (
            filtered.sort_values("run_date", ascending=False)
            .groupby("ticker", as_index=False)
            .first()
        )

    # ------------------------------------------------------------------
    # Forecast series
    # ------------------------------------------------------------------

    def insert_forecast_series(
        self,
        ticker: str,
        horizon_months: int,
        run_date: date,
        forecast_df: pd.DataFrame,
    ) -> None:
        """Append the full Prophet output series for a forecast run.

        Drops any existing rows for the same
        ``(ticker, horizon_months, run_date)``
        before inserting to keep the table clean on re-runs.  Loads the table
        object only once to avoid a second catalog round-trip.

        Args:
            ticker: Stock ticker symbol.
            horizon_months: Forecast horizon (3, 6, or 9).
            run_date: The date this forecast was run.
            forecast_df: DataFrame with columns
                ``ds``, ``yhat``, ``yhat_lower``,
                ``yhat_upper`` as returned by Prophet.
        """
        if forecast_df.empty:
            return

        run_date = _to_date(run_date)

        new_rows = {
            "ticker": [ticker] * len(forecast_df),
            "horizon_months": [int(horizon_months)] * len(forecast_df),
            "run_date": [run_date] * len(forecast_df),
            "forecast_date": [_to_date(d) for d in forecast_df["ds"]],
            "predicted_price": [_safe_float(v) for v in forecast_df["yhat"]],
            "lower_bound": [_safe_float(v) for v in forecast_df["yhat_lower"]],
            "upper_bound": [_safe_float(v) for v in forecast_df["yhat_upper"]],
        }

        arrow_new = pa.table(
            {
                "ticker": pa.array(new_rows["ticker"], pa.string()),
                "horizon_months": pa.array(
                    new_rows["horizon_months"], pa.int32()
                ),
                "run_date": pa.array(new_rows["run_date"], pa.date32()),
                "forecast_date": pa.array(
                    new_rows["forecast_date"], pa.date32()
                ),
                "predicted_price": pa.array(
                    new_rows["predicted_price"],
                    pa.float64(),
                ),
                "lower_bound": pa.array(new_rows["lower_bound"], pa.float64()),
                "upper_bound": pa.array(new_rows["upper_bound"], pa.float64()),
            }
        )

        # Scoped delete-and-append: remove only matching
        # (ticker, horizon, run_date) rows, then append.
        from pyiceberg.expressions import And, EqualTo

        try:
            self._delete_rows(
                _FORECASTS,
                And(
                    EqualTo("ticker", ticker),
                    And(
                        EqualTo(
                            "horizon_months",
                            int(horizon_months),
                        ),
                        EqualTo("run_date", run_date),
                    ),
                ),
            )
        except Exception:
            _logger.debug(
                "Delete before upsert failed for " "forecasts/%s",
                ticker,
                exc_info=True,
            )
        self._append_rows(_FORECASTS, arrow_new)

        _logger.debug(
            "forecast_series inserted for %s %dm run %s (%d rows)",
            ticker,
            horizon_months,
            run_date,
            len(forecast_df),
        )

    def get_latest_forecast_series(
        self, ticker: str, horizon_months: int
    ) -> pd.DataFrame:
        """Return the forecast series from the most recent run for *ticker*.

        Args:
            ticker: Stock ticker symbol.
            horizon_months: Forecast horizon (3, 6, or 9).

        Returns:
            DataFrame with columns: forecast_date, predicted_price,
            lower_bound, upper_bound — sorted by forecast_date.
        """
        df = self._scan_two_filters(
            _FORECASTS,
            "ticker",
            ticker.upper(),
            "horizon_months",
            int(horizon_months),
        )
        if df.empty:
            return df
        latest_run = df["run_date"].max()
        return (
            df[df["run_date"] == latest_run]
            .sort_values("forecast_date")
            .reset_index(drop=True)
        )

    # ------------------------------------------------------------------
    # Quarterly Results
    # ------------------------------------------------------------------

    def insert_quarterly_results(self, ticker: str, df: pd.DataFrame) -> None:
        """Upsert quarterly results for *ticker* (copy-on-write).

        Deduplicates on ``(ticker, quarter_end, statement_type)``
        keeping the newest rows from *df*.

        Args:
            ticker: Stock ticker symbol.
            df: DataFrame with quarterly result columns matching
                the ``stocks.quarterly_results`` schema.
        """
        ticker = ticker.upper()
        # Scoped read: only this ticker's existing rows
        existing_ticker = self._scan_ticker(_QUARTERLY_RESULTS, ticker)
        if not existing_ticker.empty:
            # Keep rows not being replaced
            keys = set(
                zip(
                    df["quarter_end"].astype(str),
                    df["statement_type"],
                )
            )
            mask = ~existing_ticker.apply(
                lambda r: (
                    str(r["quarter_end"]),
                    r["statement_type"],
                )
                in keys,
                axis=1,
            )
            kept = existing_ticker[mask]
            combined = pd.concat([kept, df], ignore_index=True)
        else:
            combined = df.copy()

        # Build Arrow table matching schema
        now = _now_utc()
        arrow = pa.table(
            {
                "ticker": pa.array(
                    combined["ticker"].tolist(),
                    pa.string(),
                ),
                "quarter_end": pa.array(
                    [_to_date(d) for d in combined["quarter_end"]],
                    pa.date32(),
                ),
                "fiscal_year": pa.array(
                    [_safe_int(v) for v in combined["fiscal_year"]],
                    pa.int32(),
                ),
                "fiscal_quarter": pa.array(
                    combined["fiscal_quarter"].tolist(),
                    pa.string(),
                ),
                "statement_type": pa.array(
                    combined["statement_type"].tolist(),
                    pa.string(),
                ),
                "revenue": pa.array(
                    [_safe_float(v) for v in combined["revenue"]],
                    pa.float64(),
                ),
                "net_income": pa.array(
                    [_safe_float(v) for v in combined["net_income"]],
                    pa.float64(),
                ),
                "gross_profit": pa.array(
                    [_safe_float(v) for v in combined["gross_profit"]],
                    pa.float64(),
                ),
                "operating_income": pa.array(
                    [_safe_float(v) for v in combined["operating_income"]],
                    pa.float64(),
                ),
                "ebitda": pa.array(
                    [_safe_float(v) for v in combined["ebitda"]],
                    pa.float64(),
                ),
                "eps_basic": pa.array(
                    [_safe_float(v) for v in combined["eps_basic"]],
                    pa.float64(),
                ),
                "eps_diluted": pa.array(
                    [_safe_float(v) for v in combined["eps_diluted"]],
                    pa.float64(),
                ),
                "total_assets": pa.array(
                    [_safe_float(v) for v in combined["total_assets"]],
                    pa.float64(),
                ),
                "total_liabilities": pa.array(
                    [_safe_float(v) for v in combined["total_liabilities"]],
                    pa.float64(),
                ),
                "total_equity": pa.array(
                    [_safe_float(v) for v in combined["total_equity"]],
                    pa.float64(),
                ),
                "total_debt": pa.array(
                    [_safe_float(v) for v in combined["total_debt"]],
                    pa.float64(),
                ),
                "cash_and_equivalents": pa.array(
                    [_safe_float(v) for v in combined["cash_and_equivalents"]],
                    pa.float64(),
                ),
                "operating_cashflow": pa.array(
                    [_safe_float(v) for v in combined["operating_cashflow"]],
                    pa.float64(),
                ),
                "capex": pa.array(
                    [_safe_float(v) for v in combined["capex"]],
                    pa.float64(),
                ),
                "free_cashflow": pa.array(
                    [_safe_float(v) for v in combined["free_cashflow"]],
                    pa.float64(),
                ),
                "updated_at": pa.array(
                    [now] * len(combined),
                    pa.timestamp("us"),
                ),
            }
        )
        # Scoped delete-and-append: remove only this
        # ticker's rows, then append the combined result.
        from pyiceberg.expressions import EqualTo

        try:
            self._delete_rows(
                _QUARTERLY_RESULTS,
                EqualTo("ticker", ticker),
            )
        except Exception:
            _logger.debug(
                "Delete before upsert failed for " "quarterly_results/%s",
                ticker,
                exc_info=True,
            )
        self._append_rows(_QUARTERLY_RESULTS, arrow)
        _logger.info(
            "quarterly_results upserted %d rows for %s",
            len(df),
            ticker,
        )

    def get_quarterly_results(self, ticker: str) -> pd.DataFrame:
        """Get all quarterly results for *ticker*.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            DataFrame sorted by quarter_end descending.
        """
        df = self._scan_ticker(_QUARTERLY_RESULTS, ticker.upper())
        if df.empty:
            return df
        return df.sort_values("quarter_end", ascending=False).reset_index(
            drop=True
        )

    def get_all_quarterly_results(
        self,
    ) -> pd.DataFrame:
        """Get quarterly results for all tickers.

        Returns:
            DataFrame with all quarterly result rows.
        """
        return self._table_to_df(_QUARTERLY_RESULTS)

    def get_quarterly_results_if_fresh(
        self, ticker: str, days: int = 7
    ) -> pd.DataFrame | None:
        """Return cached quarterly data if updated within *days*.

        Args:
            ticker: Stock ticker symbol.
            days: Freshness threshold in days.

        Returns:
            DataFrame if fresh, ``None`` if stale or missing.
        """
        df = self._scan_ticker(_QUARTERLY_RESULTS, ticker.upper())
        if df.empty:
            return None
        if "updated_at" not in df.columns:
            return None
        latest = pd.to_datetime(df["updated_at"]).max()
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=days
        )
        if latest >= cutoff:
            return df
        return None

    # ── Bulk delete ──────────────────────────────────────

    _ALL_TICKER_TABLES = (
        "stocks.registry",
        "stocks.company_info",
        "stocks.ohlcv",
        "stocks.dividends",
        "stocks.technical_indicators",
        "stocks.analysis_summary",
        "stocks.forecast_runs",
        "stocks.forecasts",
        "stocks.quarterly_results",
    )

    def delete_ticker_data(self, ticker: str) -> dict[str, int]:
        """Remove all rows for *ticker* from every table.

        Uses scoped row-level delete via PyIceberg's
        ``table.delete(delete_filter=...)``.  Only the
        target ticker's rows are affected.

        Args:
            ticker: Uppercase ticker symbol.

        Returns:
            Dict mapping table name to rows deleted.
        """
        from pyiceberg.expressions import EqualTo

        ticker = ticker.upper()
        deleted: dict[str, int] = {}
        for table_id in self._ALL_TICKER_TABLES:
            try:
                # Count rows before delete
                before = len(self._scan_ticker(table_id, ticker))
                if before == 0:
                    deleted[table_id] = 0
                    continue
                self._delete_rows(
                    table_id,
                    EqualTo("ticker", ticker),
                )
                _logger.info(
                    "Deleted %d rows for %s from %s",
                    before,
                    ticker,
                    table_id,
                )
                deleted[table_id] = before
            except Exception as exc:
                _logger.warning(
                    "Failed to delete %s from %s: %s",
                    ticker,
                    table_id,
                    exc,
                )
                deleted[table_id] = 0
        return deleted

    # ------------------------------------------------------------------
    # LLM Pricing
    # ------------------------------------------------------------------

    _LLM_PRICING = "stocks.llm_pricing"

    def get_current_pricing(self) -> pd.DataFrame:
        """Return all current LLM pricing rates.

        Current rates have ``effective_to IS NULL``.

        Returns:
            DataFrame of active pricing rows.
        """
        df = self._table_to_df(self._LLM_PRICING)
        if df.empty:
            return df
        return df[df["effective_to"].isna()].reset_index(
            drop=True,
        )

    def get_all_pricing(self) -> pd.DataFrame:
        """Return full pricing history (all rows).

        Returns:
            DataFrame of all pricing rows.
        """
        return self._table_to_df(self._LLM_PRICING)

    def add_pricing(
        self,
        provider: str,
        model: str,
        input_cost: float,
        output_cost: float,
        effective_from: date,
        updated_by: str | None = None,
    ) -> str:
        """Add a new pricing rate row.

        Does NOT close existing rates — call
        :meth:`update_pricing` for rate changes.

        Args:
            provider: ``"groq"`` or ``"anthropic"``.
            model: Full model name.
            input_cost: $/1M input tokens.
            output_cost: $/1M output tokens.
            effective_from: Start date of rate.
            updated_by: Admin user ID (optional).

        Returns:
            The generated ``pricing_id``.
        """
        pid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(
            tzinfo=None,
        )
        tbl = pa.table(
            {
                "pricing_id": pa.array([pid], type=pa.string()),
                "provider": pa.array([provider], type=pa.string()),
                "model": pa.array([model], type=pa.string()),
                "input_cost_per_1m": pa.array([input_cost], type=pa.float64()),
                "output_cost_per_1m": pa.array(
                    [output_cost], type=pa.float64()
                ),
                "effective_from": pa.array([effective_from], type=pa.date32()),
                "effective_to": pa.array([None], type=pa.date32()),
                "currency": pa.array(["USD"], type=pa.string()),
                "updated_by": pa.array([updated_by], type=pa.string()),
                "created_at": pa.array([now], type=pa.timestamp("us")),
            }
        )
        self._append_rows(self._LLM_PRICING, tbl)
        return pid

    def update_pricing(
        self,
        provider: str,
        model: str,
        input_cost: float,
        output_cost: float,
        effective_from: date,
        updated_by: str | None = None,
    ) -> str:
        """Update pricing by closing the current rate and
        adding a new one.

        Sets ``effective_to`` on the existing current rate
        to ``effective_from - 1 day``, then appends a new
        row.

        Args:
            provider: ``"groq"`` or ``"anthropic"``.
            model: Full model name.
            input_cost: New $/1M input tokens.
            output_cost: New $/1M output tokens.
            effective_from: Start date of new rate.
            updated_by: Admin user ID (optional).

        Returns:
            The new ``pricing_id``.
        """
        # Close existing current rate via
        # copy-on-write (small table).
        df = self._table_to_df(self._LLM_PRICING)
        if not df.empty:
            mask = (
                (df["provider"] == provider)
                & (df["model"] == model)
                & (df["effective_to"].isna())
            )
            close_date = effective_from - timedelta(
                days=1,
            )
            df.loc[mask, "effective_to"] = close_date
            tbl = pa.Table.from_pandas(df, preserve_index=False)
            self._overwrite_table(self._LLM_PRICING, tbl)
        return self.add_pricing(
            provider,
            model,
            input_cost,
            output_cost,
            effective_from,
            updated_by,
        )

    # ------------------------------------------------------------------
    # LLM Usage
    # ------------------------------------------------------------------

    _LLM_USAGE = "stocks.llm_usage"

    def append_llm_usage(self, events: list[dict]) -> None:
        """Append LLM usage event rows.

        Args:
            events: List of dicts matching the
                ``llm_usage`` schema fields.
        """
        if not events:
            return
        arrays = {
            "usage_id": pa.array(
                [e.get("usage_id", str(uuid.uuid4())) for e in events],
                type=pa.string(),
            ),
            "request_date": pa.array(
                [e["request_date"] for e in events],
                type=pa.date32(),
            ),
            "timestamp": pa.array(
                [e["timestamp"] for e in events],
                type=pa.timestamp("us"),
            ),
            "user_id": pa.array(
                [e.get("user_id") for e in events],
                type=pa.string(),
            ),
            "agent_id": pa.array(
                [e["agent_id"] for e in events],
                type=pa.string(),
            ),
            "model": pa.array(
                [e["model"] for e in events],
                type=pa.string(),
            ),
            "provider": pa.array(
                [e["provider"] for e in events],
                type=pa.string(),
            ),
            "tier_index": pa.array(
                [e.get("tier_index", 0) for e in events],
                type=pa.int32(),
            ),
            "event_type": pa.array(
                [e["event_type"] for e in events],
                type=pa.string(),
            ),
            "cascade_reason": pa.array(
                [e.get("cascade_reason") for e in events],
                type=pa.string(),
            ),
            "cascade_from_model": pa.array(
                [e.get("cascade_from_model") for e in events],
                type=pa.string(),
            ),
            "prompt_tokens": pa.array(
                [e.get("prompt_tokens") for e in events],
                type=pa.int32(),
            ),
            "completion_tokens": pa.array(
                [e.get("completion_tokens") for e in events],
                type=pa.int32(),
            ),
            "total_tokens": pa.array(
                [e.get("total_tokens") for e in events],
                type=pa.int32(),
            ),
            "input_cost_per_1m": pa.array(
                [e.get("input_cost_per_1m") for e in events],
                type=pa.float64(),
            ),
            "output_cost_per_1m": pa.array(
                [e.get("output_cost_per_1m") for e in events],
                type=pa.float64(),
            ),
            "estimated_cost_usd": pa.array(
                [e.get("estimated_cost_usd") for e in events],
                type=pa.float64(),
            ),
            "latency_ms": pa.array(
                [e.get("latency_ms") for e in events],
                type=pa.int32(),
            ),
            "success": pa.array(
                [e.get("success", True) for e in events],
                type=pa.bool_(),
            ),
            "error_code": pa.array(
                [e.get("error_code") for e in events],
                type=pa.string(),
            ),
        }
        self._append_rows(self._LLM_USAGE, pa.table(arrays))

    def get_usage_totals(self) -> dict:
        """Return lifetime aggregate totals from
        ``llm_usage``.

        Returns:
            Dict with ``requests_total``,
            ``cascade_count``, ``compression_count``.
        """
        df = self._table_to_df(self._LLM_USAGE)
        if df.empty:
            return {
                "requests_total": 0,
                "cascade_count": 0,
                "compression_count": 0,
            }
        return {
            "requests_total": int((df["event_type"] == "request").sum()),
            "cascade_count": int((df["event_type"] == "cascade").sum()),
            "compression_count": int(
                (df["event_type"] == "compression").sum()
            ),
        }

    def get_usage_by_date_range(
        self,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """Return usage rows within a date range.

        Uses Iceberg-level date predicates on the
        ``request_date`` partition column for efficient
        partition pruning.

        Args:
            start: Start date (inclusive).
            end: End date (inclusive).

        Returns:
            DataFrame of matching usage rows.
        """
        df = self._scan_date_range(
            self._LLM_USAGE,
            date_col="request_date",
            start=start,
            end=end,
        )
        return df.reset_index(drop=True) if not df.empty else df

    # ------------------------------------------------------------------
    # Dashboard helpers
    # ------------------------------------------------------------------

    def get_dashboard_ohlcv(
        self,
        ticker: str,
        limit: int = 30,
    ) -> pd.DataFrame:
        """Get last N OHLCV rows for sparkline charts.

        Args:
            ticker: Stock ticker symbol.
            limit: Maximum rows to return (most recent).

        Returns:
            DataFrame sorted by date ascending with at
            most *limit* rows.
        """
        try:
            df = self._scan_ticker(_OHLCV, ticker)
            if df.empty:
                return df
            df = df.sort_values("date", ascending=False)
            df = df.head(limit)
            return df.sort_values("date", ascending=True).reset_index(
                drop=True
            )
        except Exception as exc:
            _logger.warning(
                "get_dashboard_ohlcv failed for %s: %s",
                ticker,
                exc,
            )
            return pd.DataFrame()

    def get_dashboard_company_info(
        self,
        ticker: str,
    ) -> dict | None:
        """Get the most recent company_info snapshot.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Dict of company info fields, or ``None``
            if no data exists.
        """
        try:
            df = self._scan_ticker(_COMPANY_INFO, ticker)
            if df.empty:
                return None
            df = df.sort_values(
                "fetched_at",
                ascending=False,
            )
            return df.iloc[0].to_dict()
        except Exception as exc:
            _logger.warning(
                "get_dashboard_company_info failed" " for %s: %s",
                ticker,
                exc,
            )
            return None

    def get_dashboard_forecast_runs(
        self,
        tickers: list[str],
    ) -> pd.DataFrame:
        """Get latest forecast_runs per ticker.

        Uses predicate push-down via ``_scan_tickers``
        instead of loading the full table.

        Args:
            tickers: List of ticker symbols to include.

        Returns:
            DataFrame with one row per ticker (latest
            run), or empty DataFrame.
        """
        try:
            df = self._scan_tickers(
                _FORECAST_RUNS,
                tickers,
            )
            if df.empty:
                return df
            idx = df.groupby("ticker")["run_date"].idxmax()
            return df.loc[idx].reset_index(drop=True)
        except Exception as exc:
            _logger.warning(
                "get_dashboard_forecast_runs" " failed: %s",
                exc,
            )
            return pd.DataFrame()

    def get_dashboard_analysis(
        self,
        tickers: list[str],
    ) -> pd.DataFrame:
        """Get latest analysis_summary per ticker.

        Uses predicate push-down via ``_scan_tickers``
        instead of loading the full table.

        Args:
            tickers: List of ticker symbols to include.

        Returns:
            DataFrame with one row per ticker (latest
            analysis), or empty DataFrame.
        """
        try:
            df = self._scan_tickers(
                _ANALYSIS_SUMMARY,
                tickers,
            )
            if df.empty:
                return df
            idx = df.groupby("ticker")["analysis_date"].idxmax()
            return df.loc[idx].reset_index(drop=True)
        except Exception as exc:
            _logger.warning(
                "get_dashboard_analysis failed: %s",
                exc,
            )
            return pd.DataFrame()

    def get_dashboard_llm_usage(
        self,
        user_id: str | None = None,
        days: int = 30,
    ) -> dict:
        """Aggregate LLM usage statistics for dashboard.

        Reads the ``llm_usage`` table and computes
        summary metrics including totals, per-model
        breakdown, and daily trend.

        Args:
            user_id: Optional user filter.  ``None``
                returns usage across all users.
            days: Number of trailing days for the daily
                trend series.

        Returns:
            Dict with keys ``total_requests``,
            ``total_cost``, ``avg_latency_ms``,
            ``per_model``, and ``daily_trend``.
        """
        empty: dict = {
            "total_requests": 0,
            "total_cost": 0.0,
            "avg_latency_ms": 0.0,
            "per_model": {},
            "daily_trend": [],
        }
        try:
            from pyiceberg.expressions import (
                EqualTo,
                GreaterThanOrEqual,
            )

            cutoff_date = (
                datetime.now(tz=timezone.utc) - timedelta(days=days)
            ).date()
            row_filter: Any = GreaterThanOrEqual(
                "request_date",
                cutoff_date,
            )
            if user_id is not None:
                from pyiceberg.expressions import And

                row_filter = And(
                    row_filter,
                    EqualTo("user_id", user_id),
                )
            try:
                tbl = self._load_table(
                    self._LLM_USAGE,
                )
                if self._LLM_USAGE in (self._dirty_tables):
                    tbl.refresh()
                    self._dirty_tables.discard(self._LLM_USAGE)
                df = tbl.scan(
                    row_filter=row_filter,
                ).to_pandas()
            except Exception:
                df = self._table_to_df(
                    self._LLM_USAGE,
                )
                if not df.empty and (user_id is not None):
                    df = df[df["user_id"] == user_id]
            if df.empty:
                return empty

            total_requests = len(df)
            total_cost = float(df["estimated_cost_usd"].sum(skipna=True))
            avg_latency = float(df["latency_ms"].mean(skipna=True))
            if math.isnan(avg_latency):
                avg_latency = 0.0

            # Per-model breakdown (include provider).
            per_model: dict = {}
            has_provider = "provider" in df.columns
            if "model" in df.columns:
                for model, grp in df.groupby("model"):
                    prov = ""
                    if has_provider:
                        prov = (
                            grp["provider"]
                            .mode()
                            .iloc[0]
                            if not grp["provider"]
                            .dropna()
                            .empty
                            else ""
                        )
                    per_model[str(model)] = {
                        "requests": len(grp),
                        "cost": float(
                            grp[
                                "estimated_cost_usd"
                            ].sum(skipna=True)
                        ),
                        "provider": str(prov),
                    }

            # Daily trend (last N days)
            cutoff = (
                datetime.now(tz=timezone.utc) - timedelta(days=days)
            ).date()
            daily_trend: list[dict] = []
            if "request_date" in df.columns:
                recent = df[df["request_date"] >= cutoff]
                if not recent.empty:
                    agg = (
                        recent.groupby("request_date")
                        .agg(
                            requests=("usage_id", "count"),
                            cost=(
                                "estimated_cost_usd",
                                "sum",
                            ),
                        )
                        .reset_index()
                        .sort_values("request_date")
                    )
                    for _, row in agg.iterrows():
                        daily_trend.append(
                            {
                                "date": str(row["request_date"]),
                                "requests": int(row["requests"]),
                                "cost": float(row["cost"]),
                            }
                        )

            return {
                "total_requests": total_requests,
                "total_cost": total_cost,
                "avg_latency_ms": avg_latency,
                "per_model": per_model,
                "daily_trend": daily_trend,
            }
        except Exception as exc:
            _logger.warning(
                "get_dashboard_llm_usage failed: %s",
                exc,
            )
            return empty

    # ------------------------------------------------------------------
    # Portfolio transactions
    # ------------------------------------------------------------------

    def get_portfolio_holdings(
        self,
        user_id: str,
    ) -> pd.DataFrame:
        """Return current holdings for *user_id*.

        Reads all BUY transactions, groups by ticker,
        and computes total quantity and weighted avg
        price.

        Returns:
            DataFrame with columns: ticker, quantity,
            avg_price, currency, market, invested.
        """
        try:
            from pyiceberg.expressions import (
                And,
                EqualTo,
            )

            tbl = self._load_table(_PORTFOLIO)
            df = tbl.scan(
                row_filter=And(
                    EqualTo("user_id", user_id),
                    EqualTo("side", "BUY"),
                ),
            ).to_pandas()
        except Exception:
            df = self._table_to_df(_PORTFOLIO)
            if not df.empty:
                df = df[(df["user_id"] == user_id) & (df["side"] == "BUY")]

        if df.empty:
            return pd.DataFrame(
                columns=[
                    "ticker",
                    "quantity",
                    "avg_price",
                    "currency",
                    "market",
                    "invested",
                ],
            )

        # Weighted average price per ticker
        df["invested"] = df["quantity"] * df["price"]
        grouped = (
            df.groupby(["ticker", "currency", "market"])
            .agg(
                quantity=("quantity", "sum"),
                invested=("invested", "sum"),
            )
            .reset_index()
        )
        grouped["avg_price"] = grouped["invested"] / grouped["quantity"]
        return grouped[grouped["quantity"] > 0].reset_index(drop=True)

    def get_portfolio_transactions(
        self,
        user_id: str,
    ) -> pd.DataFrame:
        """Return all portfolio transactions for user.

        Returns:
            DataFrame with all transaction rows.
        """
        try:
            from pyiceberg.expressions import (
                EqualTo,
            )

            tbl = self._load_table(_PORTFOLIO)
            return tbl.scan(
                row_filter=EqualTo(
                    "user_id",
                    user_id,
                ),
            ).to_pandas()
        except Exception:
            df = self._table_to_df(_PORTFOLIO)
            if df.empty:
                return df
            return df[df["user_id"] == user_id].copy()

    def add_portfolio_transaction(
        self,
        txn: dict,
    ) -> None:
        """Append a portfolio transaction.

        Args:
            txn: Dict with keys: transaction_id,
                user_id, ticker, side, quantity,
                price, currency, market, trade_date,
                fees, notes.
        """
        row = pa.table(
            {
                "transaction_id": pa.array(
                    [txn["transaction_id"]],
                    pa.string(),
                ),
                "user_id": pa.array(
                    [txn["user_id"]],
                    pa.string(),
                ),
                "ticker": pa.array(
                    [txn["ticker"]],
                    pa.string(),
                ),
                "side": pa.array(
                    [txn["side"]],
                    pa.string(),
                ),
                "quantity": pa.array(
                    [float(txn["quantity"])],
                    pa.float64(),
                ),
                "price": pa.array(
                    [float(txn["price"])],
                    pa.float64(),
                ),
                "currency": pa.array(
                    [txn.get("currency", "USD")],
                    pa.string(),
                ),
                "market": pa.array(
                    [txn.get("market", "us")],
                    pa.string(),
                ),
                "trade_date": pa.array(
                    [txn.get("trade_date")],
                    pa.date32(),
                ),
                "fees": pa.array(
                    [float(txn.get("fees", 0))],
                    pa.float64(),
                ),
                "notes": pa.array(
                    [txn.get("notes", "")],
                    pa.string(),
                ),
                "created_at": pa.array(
                    [_now_utc()],
                    pa.timestamp("us"),
                ),
            }
        )
        self._append_rows(_PORTFOLIO, row)

    def update_portfolio_transaction(
        self,
        transaction_id: str,
        user_id: str,
        updates: dict,
    ) -> bool:
        """Update price, quantity, or trade_date.

        Performs a copy-on-write: reads all rows,
        modifies the matching row, overwrites.

        Returns:
            True if the transaction was found and
            updated.
        """
        df = self._table_to_df(_PORTFOLIO)
        if df.empty:
            return False
        mask = (df["transaction_id"] == transaction_id) & (
            df["user_id"] == user_id
        )
        if not mask.any():
            return False
        for key in ("price", "quantity"):
            if key in updates:
                df.loc[mask, key] = float(updates[key])
        if "trade_date" in updates:
            df.loc[mask, "trade_date"] = updates["trade_date"]
        arrow_tbl = pa.Table.from_pandas(
            df,
            preserve_index=False,
        )
        self._overwrite_table(
            _PORTFOLIO,
            arrow_tbl,
        )
        return True

    def delete_portfolio_transaction(
        self,
        transaction_id: str,
        user_id: str,
    ) -> bool:
        """Delete a portfolio transaction.

        Returns:
            True if the transaction was found and
            removed.
        """
        df = self._table_to_df(_PORTFOLIO)
        if df.empty:
            return False
        mask = (df["transaction_id"] == transaction_id) & (
            df["user_id"] == user_id
        )
        if not mask.any():
            return False
        df = df[~mask]
        arrow_tbl = pa.Table.from_pandas(
            df,
            preserve_index=False,
        )
        self._overwrite_table(
            _PORTFOLIO,
            arrow_tbl,
        )
        return True

    # ------------------------------------------------------------------
    # Chat audit log
    # ------------------------------------------------------------------

    def _ensure_chat_audit_table(self) -> None:
        """Create the chat_audit_log table if absent.

        Schema:
            session_id (string), user_id (string),
            started_at (timestamp), ended_at (timestamp),
            message_count (int32), messages_json (string),
            agent_ids_used (string), created_at (timestamp).

        Partitioned by identity on ``user_id``.
        """
        try:
            from pyiceberg.partitioning import (
                PartitionField,
                PartitionSpec,
            )
            from pyiceberg.schema import Schema
            from pyiceberg.transforms import (
                IdentityTransform,
            )
            from pyiceberg.types import (
                IntegerType,
                NestedField,
                StringType,
                TimestampType,
            )

            catalog = self._get_catalog()
            try:
                catalog.load_table(_CHAT_AUDIT_LOG)
                return  # already exists
            except Exception:
                _logger.debug(
                    "chat_audit_log table not found" " — will create",
                )

            schema = Schema(
                NestedField(
                    1,
                    "session_id",
                    StringType(),
                    required=True,
                ),
                NestedField(
                    2,
                    "user_id",
                    StringType(),
                    required=True,
                ),
                NestedField(
                    3,
                    "started_at",
                    TimestampType(),
                ),
                NestedField(
                    4,
                    "ended_at",
                    TimestampType(),
                ),
                NestedField(
                    5,
                    "message_count",
                    IntegerType(),
                ),
                NestedField(
                    6,
                    "messages_json",
                    StringType(),
                ),
                NestedField(
                    7,
                    "agent_ids_used",
                    StringType(),
                ),
                NestedField(
                    8,
                    "created_at",
                    TimestampType(),
                ),
            )
            partition_spec = PartitionSpec(
                PartitionField(
                    source_id=2,
                    field_id=1000,
                    transform=IdentityTransform(),
                    name="user_id",
                ),
            )
            catalog.create_table(
                _CHAT_AUDIT_LOG,
                schema=schema,
                partition_spec=partition_spec,
            )
            _logger.info(
                "Created table %s",
                _CHAT_AUDIT_LOG,
            )
        except Exception as exc:
            _logger.error(
                "Failed to create %s: %s",
                _CHAT_AUDIT_LOG,
                exc,
            )
            raise

    def save_chat_session(
        self,
        session: dict,
    ) -> None:
        """Append a chat transcript to the audit log.

        Ensures the table exists, then builds a
        single-row PyArrow table from *session* and
        appends it.

        Args:
            session: Dict with keys ``session_id``,
                ``user_id``, ``started_at``,
                ``ended_at``, ``message_count``,
                ``messages_json``, ``agent_ids_used``.
        """
        try:
            self._ensure_chat_audit_table()
            now = _now_utc()

            def _parse_ts(val):
                """Parse ISO string or return now."""
                if val is None or val == "":
                    return now
                if isinstance(val, str):
                    return pd.Timestamp(
                        val,
                    ).to_pydatetime()
                return val

            started = _parse_ts(
                session.get("started_at"),
            )
            ended = _parse_ts(
                session.get("ended_at"),
            )
            arrays = {
                "session_id": pa.array(
                    [session["session_id"]],
                    type=pa.string(),
                ),
                "user_id": pa.array(
                    [session["user_id"]],
                    type=pa.string(),
                ),
                "started_at": pa.array(
                    [started],
                    type=pa.timestamp("us"),
                ),
                "ended_at": pa.array(
                    [ended],
                    type=pa.timestamp("us"),
                ),
                "message_count": pa.array(
                    [session.get("message_count", 0)],
                    type=pa.int32(),
                ),
                "messages_json": pa.array(
                    [session.get("messages_json", "[]")],
                    type=pa.string(),
                ),
                "agent_ids_used": pa.array(
                    [session.get("agent_ids_used", "[]")],
                    type=pa.string(),
                ),
                "created_at": pa.array(
                    [now],
                    type=pa.timestamp("us"),
                ),
            }
            # session_id & user_id are required (non-
            # nullable) in the Iceberg schema — build
            # the Arrow table with a matching schema.
            _schema = pa.schema([
                pa.field(
                    "session_id", pa.string(),
                    nullable=False,
                ),
                pa.field(
                    "user_id", pa.string(),
                    nullable=False,
                ),
                pa.field(
                    "started_at",
                    pa.timestamp("us"),
                ),
                pa.field(
                    "ended_at",
                    pa.timestamp("us"),
                ),
                pa.field(
                    "message_count", pa.int32(),
                ),
                pa.field(
                    "messages_json", pa.string(),
                ),
                pa.field(
                    "agent_ids_used", pa.string(),
                ),
                pa.field(
                    "created_at",
                    pa.timestamp("us"),
                ),
            ])
            self._append_rows(
                _CHAT_AUDIT_LOG,
                pa.table(arrays, schema=_schema),
            )
        except Exception as exc:
            _logger.error(
                "save_chat_session failed: %s",
                exc,
            )
            raise

    def list_chat_sessions(
        self,
        user_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """Query chat sessions for a user.

        Args:
            user_id: Filter to this user's sessions.
            start_date: Optional ISO date lower bound
                on ``started_at``.
            end_date: Optional ISO date upper bound
                on ``started_at``.
            keyword: Optional keyword to match within
                ``messages_json`` (case-insensitive).
            limit: Max results to return.
            offset: Number of results to skip.

        Returns:
            List of dicts with ``session_id``,
            ``started_at``, ``ended_at``,
            ``message_count``, ``preview``,
            ``agent_ids_used``.
        """
        try:
            self._ensure_chat_audit_table()
            from pyiceberg.expressions import EqualTo

            tbl = self._load_table(_CHAT_AUDIT_LOG)
            if _CHAT_AUDIT_LOG in self._dirty_tables:
                tbl.refresh()
                self._dirty_tables.discard(
                    _CHAT_AUDIT_LOG,
                )
            df = tbl.scan(
                row_filter=EqualTo(
                    "user_id",
                    user_id,
                ),
            ).to_pandas()
        except Exception as exc:
            _logger.warning(
                "list_chat_sessions scan failed: %s",
                exc,
            )
            df = self._table_to_df(_CHAT_AUDIT_LOG)
            if not df.empty:
                df = df[df["user_id"] == user_id].copy()

        if df.empty:
            return []

        # Date filters
        if start_date is not None:
            ts = pd.Timestamp(start_date, tz="UTC")
            df = df[df["started_at"] >= ts]
        if end_date is not None:
            ts = pd.Timestamp(end_date, tz="UTC")
            df = df[df["started_at"] <= ts]

        # Keyword filter
        if keyword is not None:
            kw = keyword.lower()
            df = df[df["messages_json"].str.lower().str.contains(kw, na=False)]

        # Sort by most recent first
        df = df.sort_values(
            "started_at",
            ascending=False,
        )

        # Paginate
        df = df.iloc[offset : offset + limit]

        results: list[dict] = []
        for _, row in df.iterrows():
            msgs_raw = str(row.get("messages_json", ""))
            preview = ""
            try:
                parsed = json.loads(msgs_raw)
                if isinstance(parsed, list):
                    for m in parsed:
                        if (
                            isinstance(m, dict)
                            and m.get("role") == "user"
                            and m.get("content")
                        ):
                            preview = str(m["content"])[:200]
                            break
                if not preview and isinstance(parsed, list) and parsed:
                    first = parsed[0]
                    if isinstance(first, dict):
                        preview = str(first.get("content", ""))[:200]
            except (
                json.JSONDecodeError,
                TypeError,
            ):
                preview = msgs_raw[:200]
            results.append(
                {
                    "session_id": str(row["session_id"]),
                    "started_at": str(row.get("started_at", "")),
                    "ended_at": str(row.get("ended_at", "")),
                    "message_count": int(row.get("message_count", 0)),
                    "preview": preview,
                    "agent_ids_used": str(row.get("agent_ids_used", "[]")),
                }
            )
        return results

    def get_chat_session_detail(
        self,
        user_id: str,
        session_id: str,
    ) -> dict | None:
        """Fetch a single chat session with messages.

        Returns:
            Dict with all summary fields plus a
            ``messages`` list, or ``None`` if not found.
        """
        try:
            self._ensure_chat_audit_table()
            from pyiceberg.expressions import (
                And,
                EqualTo,
            )

            tbl = self._load_table(_CHAT_AUDIT_LOG)
            if _CHAT_AUDIT_LOG in self._dirty_tables:
                tbl.refresh()
                self._dirty_tables.discard(
                    _CHAT_AUDIT_LOG,
                )
            df = tbl.scan(
                row_filter=And(
                    EqualTo("user_id", user_id),
                    EqualTo("session_id", session_id),
                ),
            ).to_pandas()
        except Exception as exc:
            _logger.warning(
                "get_chat_session_detail scan " "failed: %s",
                exc,
            )
            df = self._table_to_df(
                _CHAT_AUDIT_LOG,
            )
            if not df.empty:
                df = df[
                    (df["user_id"] == user_id)
                    & (df["session_id"] == session_id)
                ].copy()

        if df.empty:
            return None

        row = df.iloc[0]
        msgs_raw = str(row.get("messages_json", "[]"))
        try:
            messages = json.loads(msgs_raw)
            if not isinstance(messages, list):
                messages = []
        except (json.JSONDecodeError, TypeError):
            messages = []

        agents_raw = str(row.get("agent_ids_used", "[]"))
        try:
            agent_ids = json.loads(agents_raw)
        except (json.JSONDecodeError, TypeError):
            agent_ids = []

        # Build preview from first user message
        preview = ""
        for m in messages:
            if (
                isinstance(m, dict)
                and m.get("role") == "user"
                and m.get("content")
            ):
                preview = str(m["content"])[:200]
                break

        return {
            "session_id": str(row["session_id"]),
            "started_at": str(row.get("started_at", "")),
            "ended_at": str(row.get("ended_at", "")),
            "message_count": int(row.get("message_count", 0)),
            "preview": preview,
            "agent_ids_used": agent_ids,
            "messages": messages,
        }

    # ---------------------------------------------------------------
    # Query log (question tracking)
    # ---------------------------------------------------------------

    _QUERY_LOG = "stocks.query_log"

    def insert_query_log(self, entry: dict) -> None:
        """Insert a query log entry.

        Args:
            entry: Dict with keys: timestamp, user_id,
                query_text, classified_intent,
                sub_agent_invoked, tools_used (JSON),
                data_sources_used (JSON),
                was_local_sufficient (bool),
                response_time_ms (int),
                gap_tickers (JSON).
        """
        import json
        import uuid
        from datetime import datetime, timezone

        row = {
            "id": str(uuid.uuid4()),
            "timestamp": entry.get(
                "timestamp",
                datetime.now(timezone.utc),
            ),
            "user_id": entry.get("user_id", ""),
            "query_text": entry.get(
                "query_text",
                "",
            ),
            "classified_intent": entry.get(
                "classified_intent",
                "",
            ),
            "sub_agent_invoked": entry.get(
                "sub_agent_invoked",
                "",
            ),
            "tools_used": json.dumps(
                entry.get("tools_used", []),
            ),
            "data_sources_used": json.dumps(
                entry.get("data_sources_used", []),
            ),
            "was_local_sufficient": entry.get(
                "was_local_sufficient",
                True,
            ),
            "response_time_ms": entry.get(
                "response_time_ms",
                0,
            ),
            "gap_tickers": json.dumps(
                entry.get("gap_tickers", []),
            ),
        }

        try:
            tbl = self._load_table(self._QUERY_LOG)
            schema = tbl.schema().as_arrow()
            at = pa.Table.from_pydict(
                {k: [v] for k, v in row.items()},
                schema=schema,
            )
            self._append_rows(self._QUERY_LOG, at)
        except Exception:
            _logger.debug(
                "query_log table not found — " "skipping (create table first)",
            )

    def get_query_logs(
        self,
        user_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """Get recent query logs for a user."""
        try:
            df = self._scan_ticker(
                self._QUERY_LOG,
                user_id,
            )
            if df.empty:
                return []
            df = df.sort_values(
                "timestamp",
                ascending=False,
            ).head(limit)
            return df.to_dict("records")
        except Exception:
            return []

    # ---------------------------------------------------------------
    # Data gaps (gap analysis)
    # ---------------------------------------------------------------

    _DATA_GAPS = "stocks.data_gaps"

    def insert_data_gap(
        self,
        ticker: str,
        data_type: str,
    ) -> None:
        """Insert or increment a data gap entry."""
        import uuid
        from datetime import datetime, timezone

        # Check if gap already exists
        try:
            tbl = self._load_table(self._DATA_GAPS)
            scan = tbl.scan()
            df = scan.to_pandas()
            existing = df[
                (df["ticker"] == ticker)
                & (df["data_type"] == data_type)
                & (df["resolved_at"].isna())
            ]
            if not existing.empty:
                # Increment count
                self.increment_gap_count(
                    ticker,
                    data_type,
                )
                return
        except Exception:
            _logger.debug(
                "data_gaps table not found — " "skipping",
            )
            return

        row = {
            "id": str(uuid.uuid4()),
            "detected_at": datetime.now(timezone.utc),
            "ticker": ticker,
            "data_type": data_type,
            "query_count": 1,
            "resolved_at": None,
            "resolution": None,
        }

        schema = tbl.schema().as_arrow()
        at = pa.Table.from_pydict(
            {k: [v] for k, v in row.items()},
            schema=schema,
        )
        self._append_rows(self._DATA_GAPS, at)

    def increment_gap_count(
        self,
        ticker: str,
        data_type: str,
    ) -> None:
        """Increment query_count for an existing gap."""
        try:
            tbl = self._load_table(self._DATA_GAPS)
            scan = tbl.scan()
            df = scan.to_pandas()
            mask = (
                (df["ticker"] == ticker)
                & (df["data_type"] == data_type)
                & (df["resolved_at"].isna())
            )
            if mask.any():
                df.loc[mask, "query_count"] += 1
                schema = tbl.schema().as_arrow()
                at = pa.Table.from_pandas(
                    df,
                    schema=schema,
                )
                self._overwrite_table(
                    self._DATA_GAPS,
                    at,
                )
        except Exception:
            _logger.warning(
                "Failed to increment gap count " "for %s/%s",
                ticker,
                data_type,
                exc_info=True,
            )

    def get_unfilled_data_gaps(
        self,
    ) -> list[dict]:
        """Get all unresolved data gaps."""
        try:
            tbl = self._load_table(self._DATA_GAPS)
            scan = tbl.scan()
            df = scan.to_pandas()
            unresolved = df[df["resolved_at"].isna()]
            return unresolved.to_dict("records")
        except Exception:
            return []

    def resolve_data_gap(
        self,
        gap_id: str,
        resolution: str,
    ) -> None:
        """Mark a data gap as resolved."""
        from datetime import datetime, timezone

        try:
            tbl = self._load_table(self._DATA_GAPS)
            scan = tbl.scan()
            df = scan.to_pandas()
            mask = df["id"] == gap_id
            if mask.any():
                df.loc[mask, "resolved_at"] = datetime.now(timezone.utc)
                df.loc[mask, "resolution"] = resolution
                schema = tbl.schema().as_arrow()
                at = pa.Table.from_pandas(
                    df,
                    schema=schema,
                )
                self._overwrite_table(
                    self._DATA_GAPS,
                    at,
                )
        except Exception:
            _logger.warning(
                "Failed to resolve data gap %s",
                gap_id,
                exc_info=True,
            )

    # ── Sentiment Scores ───────────────────────────────

    def insert_sentiment_score(
        self,
        ticker: str,
        score_date: date,
        avg_score: float,
        headline_count: int = 0,
        source: str = "llm",
    ) -> None:
        """Append a daily sentiment score row."""
        row = pa.table(
            {
                "ticker": pa.array(
                    [ticker.upper()],
                    pa.string(),
                ),
                "score_date": pa.array(
                    [score_date],
                    pa.date32(),
                ),
                "avg_score": pa.array(
                    [avg_score],
                    pa.float64(),
                ),
                "headline_count": pa.array(
                    [headline_count],
                    pa.int32(),
                ),
                "source": pa.array(
                    [source],
                    pa.string(),
                ),
                "scored_at": pa.array(
                    [_now_utc()],
                    pa.timestamp("us"),
                ),
            }
        )
        self._append_rows(
            "stocks.sentiment_scores",
            row,
        )

    def get_sentiment_series(
        self,
        ticker: str,
        start_date: date | None = None,
    ) -> pd.DataFrame:
        """Return sentiment score series for *ticker*."""
        df = self._scan_ticker(
            "stocks.sentiment_scores",
            ticker.upper(),
        )
        if df.empty:
            return df
        if start_date is not None:
            df = df[df["score_date"] >= start_date]
        return (
            df.sort_values(
                "score_date",
            )
            .drop_duplicates(
                subset=["score_date"],
                keep="last",
            )
            .reset_index(drop=True)
        )
