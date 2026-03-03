"""Iceberg-backed repository for all stock market data tables.

This module provides :class:`StockRepository`, the single point of access
for reading and writing to the 8 ``stocks`` Iceberg tables.  No code
outside this module should interact with the tables directly.

Write semantics
---------------
- **registry** — upsert (copy-on-write): read full table, update or append
  the row for ``ticker``, overwrite.
- **company_info** — append-only snapshots; never updated or deleted.
- **ohlcv** — append new rows; deduplication on ``(ticker, date)`` at
  application level (existing rows are never re-inserted).
- **dividends** — same as ohlcv: append, deduplicate on ``(ticker, ex_date)``.
- **technical_indicators** — upsert per ``(ticker, date)`` (copy-on-write for
  the ticker partition; acceptable for typical dataset sizes < 5 000 rows/ticker).
- **analysis_summary** — append-only snapshots.
- **forecast_runs** — append-only per ``(ticker, horizon_months, run_date)``.
- **forecasts** — append per ``(ticker, horizon_months, run_date)``; existing
  series for the same run are dropped before re-inserting.

PyIceberg quirks
----------------
- ``table.append()`` requires a ``pa.Table`` (not a ``RecordBatch``).
- ``TimestampType`` maps to ``pa.timestamp("us")`` — pass naive UTC datetimes.
- Overwrite uses ``table.overwrite(df)`` which replaces *all* data; for
  partitioned tables use ``table.dynamic_partition_overwrite(df)`` to replace
  only the affected partition.

Usage::

    from stocks.repository import StockRepository
    from datetime import date

    repo = StockRepository()
    repo.upsert_registry("AAPL", date.today(), 2500, date(2015,1,2), date(2026,2,28), "us")
    df = repo.get_ohlcv("AAPL")
"""

import logging
import math
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pyarrow as pa

_logger = logging.getLogger(__name__)

_NAMESPACE = "stocks"
_REGISTRY = f"{_NAMESPACE}.registry"
_COMPANY_INFO = f"{_NAMESPACE}.company_info"
_OHLCV = f"{_NAMESPACE}.ohlcv"
_DIVIDENDS = f"{_NAMESPACE}.dividends"
_TECHNICAL_INDICATORS = f"{_NAMESPACE}.technical_indicators"
_ANALYSIS_SUMMARY = f"{_NAMESPACE}.analysis_summary"
_FORECAST_RUNS = f"{_NAMESPACE}.forecast_runs"
_FORECASTS = f"{_NAMESPACE}.forecasts"


def _now_utc() -> datetime:
    """Return current UTC time as a naive datetime (PyIceberg TimestampType compat).

    Returns:
        Naive :class:`datetime.datetime` in UTC.
    """
    return datetime.utcnow()


def _to_date(value: Any) -> Optional[date]:
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


def _safe_float(value: Any) -> Optional[float]:
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


def _safe_int(value: Any) -> Optional[int]:
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
            return tbl.scan().to_pandas()
        except Exception as exc:
            _logger.warning("Could not read table %s: %s", identifier, exc)
            return pd.DataFrame()

    def _scan_ticker(self, identifier: str, ticker: str) -> pd.DataFrame:
        """Scan a table filtered to a single ticker using predicate push-down.

        Attempts a server-side ``EqualTo("ticker", ticker)`` predicate first;
        falls back to a full table scan with Python-level filtering on failure.

        Args:
            identifier: Fully-qualified table name (e.g. ``"stocks.ohlcv"``).
            ticker: Stock ticker symbol (already uppercased).

        Returns:
            DataFrame containing only rows for *ticker*, or an empty DataFrame.
        """
        try:
            from pyiceberg.expressions import EqualTo

            tbl = self._load_table(identifier)
            return tbl.scan(row_filter=EqualTo("ticker", ticker)).to_pandas()
        except Exception as exc:
            _logger.warning(
                "Predicate push-down failed for %s ticker=%s (%s); falling back to full scan.",
                identifier,
                ticker,
                exc,
            )
            df = self._table_to_df(identifier)
            if df.empty:
                return df
            return df[df["ticker"] == ticker].copy()

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
            return tbl.scan(
                row_filter=And(EqualTo(col1, val1), EqualTo(col2, val2))
            ).to_pandas()
        except Exception as exc:
            _logger.warning(
                "Compound predicate failed for %s (%s); falling back to full scan.",
                identifier,
                exc,
            )
            df = self._table_to_df(identifier)
            if df.empty:
                return df
            return df[(df[col1] == val1) & (df[col2] == val2)].copy()

    def _load_table_and_scan(
        self, identifier: str
    ) -> Tuple[Any, pd.DataFrame]:
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
        try:
            df = tbl.scan().to_pandas()
        except Exception as exc:
            _logger.warning("Could not read table %s: %s", identifier, exc)
            df = pd.DataFrame()
        return tbl, df

    def _append_rows(self, identifier: str, arrow_table: pa.Table) -> None:
        """Append a PyArrow table to an Iceberg table.

        Args:
            identifier: Fully-qualified table name.
            arrow_table: Rows to append (must match the table schema).
        """
        tbl = self._load_table(identifier)
        tbl.append(arrow_table)

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def upsert_registry(
        self,
        ticker: str,
        last_fetch_date: date,
        total_rows: int,
        date_range_start: date,
        date_range_end: date,
        market: str,
    ) -> None:
        """Insert or update the registry row for *ticker*.

        Uses copy-on-write: reads full table, updates or appends the row,
        then overwrites.  Loads the table object only once to avoid a
        second catalog round-trip.

        Args:
            ticker: Stock ticker symbol (already uppercased).
            last_fetch_date: Date of the most recent successful Yahoo Finance pull.
            total_rows: Total OHLCV row count for this ticker.
            date_range_start: Earliest trading date in OHLCV.
            date_range_end: Most recent trading date in OHLCV.
            market: ``"india"`` for .NS/.BO tickers, ``"us"`` otherwise.
        """
        now = _now_utc()
        # Fix #2: load table once; scan from the same object
        tbl, df = self._load_table_and_scan(_REGISTRY)

        new_row = {
            "ticker": ticker,
            "last_fetch_date": last_fetch_date,
            "total_rows": int(total_rows),
            "date_range_start": date_range_start,
            "date_range_end": date_range_end,
            "market": market,
            "created_at": now,
            "updated_at": now,
        }

        if not df.empty and ticker in df["ticker"].values:
            created_at = df.loc[df["ticker"] == ticker, "created_at"].iloc[0]
            new_row["created_at"] = created_at
            df = df[df["ticker"] != ticker]

        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

        arrow_tbl = pa.Table.from_pandas(
            df,
            schema=pa.schema(
                [
                    pa.field("ticker", pa.string()),
                    pa.field("last_fetch_date", pa.date32()),
                    pa.field("total_rows", pa.int64()),
                    pa.field("date_range_start", pa.date32()),
                    pa.field("date_range_end", pa.date32()),
                    pa.field("market", pa.string()),
                    pa.field("created_at", pa.timestamp("us")),
                    pa.field("updated_at", pa.timestamp("us")),
                ]
            ),
            preserve_index=False,
        )

        tbl.overwrite(arrow_tbl)
        _logger.debug("Registry upserted for %s", ticker)

    def get_registry(self, ticker: Optional[str] = None) -> pd.DataFrame:
        """Return registry rows, optionally filtered to a single ticker.

        Args:
            ticker: If provided, return only the row for this ticker using
                    predicate push-down.  If ``None``, return all rows.

        Returns:
            pandas DataFrame with registry rows.
        """
        if ticker:
            return self._scan_ticker(_REGISTRY, ticker.upper())
        return self._table_to_df(_REGISTRY)

    def get_all_registry(self) -> Dict[str, Dict]:
        """Return the full registry as a dict keyed by ticker symbol.

        Mirrors the shape previously stored in ``stock_registry.json`` so that
        callers can switch from JSON reads to this method without changing
        their downstream logic.

        Returns:
            Dict mapping ticker symbols to metadata dicts with keys:
            ``ticker``, ``last_fetch_date``, ``total_rows``, ``date_range``
            (containing ``start`` and ``end``), and ``file_path``.
        """
        df = self._table_to_df(_REGISTRY)
        if df.empty:
            return {}
        result: Dict[str, Dict] = {}
        for row in df.to_dict("records"):
            ticker = str(row.get("ticker", ""))
            if not ticker:
                continue
            lfd = row.get("last_fetch_date")
            start = row.get("date_range_start")
            end = row.get("date_range_end")
            result[ticker] = {
                "ticker": ticker,
                "last_fetch_date": str(lfd)[:10] if lfd else "",
                "total_rows": (
                    int(row["total_rows"])
                    if row.get("total_rows") is not None
                    else 0
                ),
                "date_range": {
                    "start": str(start)[:10] if start else "",
                    "end": str(end)[:10] if end else "",
                },
                "market": str(row.get("market", "us")),
                "file_path": str(
                    Path(__file__).parent.parent
                    / "data"
                    / "raw"
                    / f"{ticker}_raw.parquet"
                ),
            }
        return result

    def check_existing_data(self, ticker: str) -> Optional[Dict]:
        """Look up a single ticker in the registry.

        Returns a dict matching the legacy JSON shape (with ``last_fetch_date``,
        ``total_rows``, ``date_range``, ``file_path``) or ``None`` if the
        ticker is not registered.

        Args:
            ticker: Stock ticker symbol (already uppercased).

        Returns:
            Registry entry dict, or ``None``.
        """
        df = self._scan_ticker(_REGISTRY, ticker.upper())
        if df.empty:
            return None
        row = df.iloc[0]
        lfd = row.get("last_fetch_date")
        start = row.get("date_range_start")
        end = row.get("date_range_end")
        return {
            "ticker": ticker,
            "last_fetch_date": str(lfd)[:10] if lfd else "",
            "total_rows": (
                int(row["total_rows"])
                if row.get("total_rows") is not None
                else 0
            ),
            "date_range": {
                "start": str(start)[:10] if start else "",
                "end": str(end)[:10] if end else "",
            },
            "file_path": str(
                Path(__file__).parent.parent
                / "data"
                / "raw"
                / f"{ticker}_raw.parquet"
            ),
        }

    def get_latest_company_info_if_fresh(
        self, ticker: str, as_of_date: date
    ) -> Optional[Dict[str, Any]]:
        """Return the latest company info snapshot if fetched on *as_of_date*.

        Used as a cache check: callers can skip a Yahoo Finance call when the
        most recent snapshot was already fetched today.

        Args:
            ticker: Stock ticker symbol.
            as_of_date: Date to check freshness against (typically ``date.today()``).

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
        """Return the ISO currency code for *ticker* from the latest company info.

        Falls back to ``"USD"`` if no company info snapshot exists.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            ISO currency code string, e.g. ``"USD"`` or ``"INR"``.
        """
        info = self.get_latest_company_info(ticker)
        if info is None:
            return "USD"
        return str(info.get("currency") or "USD")

    # ------------------------------------------------------------------
    # Company info
    # ------------------------------------------------------------------

    def insert_company_info(self, ticker: str, info: Dict[str, Any]) -> None:
        """Append a company metadata snapshot for *ticker*.

        Args:
            ticker: Stock ticker symbol (already uppercased).
            info: Dict from ``yf.Ticker(ticker).info`` plus optional extra fields.
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

    def get_latest_company_info(self, ticker: str) -> Optional[Dict[str, Any]]:
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
        limit: Optional[int] = None,
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
        """Append new OHLCV rows for *ticker*, skipping existing (ticker, date) pairs.

        Uses predicate push-down to fetch only existing dates for this ticker,
        avoiding a full table scan.  Deduplication uses :class:`datetime.date`
        objects (not string conversion) for correctness and speed.

        Args:
            ticker: Stock ticker symbol (already uppercased).
            df: DataFrame with DatetimeIndex and columns Open, High, Low, Close,
                Adj Close (optional), Volume as returned by yfinance.

        Returns:
            Number of new rows actually inserted.
        """
        if df.empty:
            return 0

        # Normalise index to date objects (Fix #10: use date objects not strings)
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
        tbl.append(arrow_tbl)
        _logger.debug(
            "Inserted %d new OHLCV rows for %s", len(new_dates), ticker
        )
        return len(new_dates)

    def get_ohlcv(
        self,
        ticker: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> pd.DataFrame:
        """Return OHLCV data for *ticker*, optionally filtered by date range.

        Args:
            ticker: Stock ticker symbol.
            start: Inclusive start date (``None`` = no lower bound).
            end: Inclusive end date (``None`` = no upper bound).

        Returns:
            DataFrame sorted by date ascending with columns:
            ticker, date, open, high, low, close, adj_close, volume.
        """
        df = self._scan_ticker(_OHLCV, ticker.upper())
        if df.empty:
            return df
        if start:
            df = df[pd.to_datetime(df["date"]).dt.date >= start]
        if end:
            df = df[pd.to_datetime(df["date"]).dt.date <= end]
        return df.sort_values("date").reset_index(drop=True)

    def get_latest_ohlcv_date(self, ticker: str) -> Optional[date]:
        """Return the most recent OHLCV date stored for *ticker*.

        Used by the delta fetch logic to determine how much new data to fetch.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            :class:`datetime.date` or ``None`` if no data exists.
        """
        df = self._scan_ticker(_OHLCV, ticker.upper())
        if df.empty:
            return None
        latest = pd.to_datetime(df["date"]).max()
        return latest.date() if pd.notna(latest) else None

    def update_ohlcv_adj_close(self, ticker: str, adj_close_map: dict) -> int:
        """Update ``adj_close`` values for existing OHLCV rows for *ticker*.

        Uses copy-on-write: reads the full OHLCV table, merges
        ``adj_close`` values from *adj_close_map* for the given ticker,
        then overwrites the table.  Rows for other tickers are untouched.

        Args:
            ticker: Uppercase ticker symbol.
            adj_close_map: Dict mapping :class:`datetime.date` objects
                to ``adj_close`` float values.

        Returns:
            Number of rows updated.
        """
        if not adj_close_map:
            return 0

        ticker = ticker.upper()
        tbl, full_df = self._load_table_and_scan(_OHLCV)
        if full_df.empty:
            _logger.warning("OHLCV table is empty — nothing to update.")
            return 0

        # Normalise date column so lookups use datetime.date objects
        full_df["_date_key"] = pd.to_datetime(full_df["date"]).dt.date

        mask = full_df["ticker"] == ticker
        updated = 0
        for idx in full_df.index[mask]:
            d = full_df.at[idx, "_date_key"]
            if d in adj_close_map:
                new_val = _safe_float(adj_close_map[d])
                if new_val is not None:
                    full_df.at[idx, "adj_close"] = new_val
                    updated += 1

        full_df.drop(columns=["_date_key"], inplace=True)

        if updated == 0:
            _logger.debug("No adj_close updates needed for %s", ticker)
            return 0

        # Rebuild Arrow table matching the OHLCV schema
        now = _now_utc()
        arrow_tbl = pa.table(
            {
                "ticker": pa.array(full_df["ticker"].tolist(), pa.string()),
                "date": pa.array(
                    pd.to_datetime(full_df["date"]).dt.date.tolist(),
                    pa.date32(),
                ),
                "open": pa.array(
                    [_safe_float(v) for v in full_df["open"]],
                    pa.float64(),
                ),
                "high": pa.array(
                    [_safe_float(v) for v in full_df["high"]],
                    pa.float64(),
                ),
                "low": pa.array(
                    [_safe_float(v) for v in full_df["low"]],
                    pa.float64(),
                ),
                "close": pa.array(
                    [_safe_float(v) for v in full_df["close"]],
                    pa.float64(),
                ),
                "adj_close": pa.array(
                    [_safe_float(v) for v in full_df["adj_close"]],
                    pa.float64(),
                ),
                "volume": pa.array(
                    [_safe_int(v) for v in full_df["volume"]],
                    pa.int64(),
                ),
                "fetched_at": pa.array(
                    [now] * len(full_df), pa.timestamp("us")
                ),
            }
        )
        tbl.overwrite(arrow_tbl)
        _logger.info("Updated %d adj_close rows for %s", updated, ticker)
        return updated

    # ------------------------------------------------------------------
    # Dividends
    # ------------------------------------------------------------------

    def insert_dividends(
        self, ticker: str, df: pd.DataFrame, currency: str = "USD"
    ) -> int:
        """Append dividend rows for *ticker*, skipping existing (ticker, ex_date) pairs.

        Uses predicate push-down for the existing-date check.  Deduplication
        uses :class:`datetime.date` objects (not string conversion).

        Args:
            ticker: Stock ticker symbol.
            df: DataFrame with columns ``date`` and ``dividend`` (from yfinance).
            currency: ISO currency code for this ticker, e.g. ``"INR"``.
                Defaults to ``"USD"``.

        Returns:
            Number of new rows inserted.
        """
        if df.empty:
            return 0

        # Fix #1 + #2: load table once; predicate push-down for existing ex_dates
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
        tickers_out: List[str] = []
        ex_dates_out: List[date] = []
        amounts_out: List[Optional[float]] = []
        currencies_out: List[str] = []
        fetched_at_out: List[datetime] = []

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
        tbl.append(arrow_tbl)
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

        def _get(canonical: str, alt: str) -> List[Optional[float]]:
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

        # Fix #2: load table once; remove existing ticker rows then overwrite
        tbl, existing = self._load_table_and_scan(_TECHNICAL_INDICATORS)
        if not existing.empty:
            existing = existing[existing["ticker"] != ticker]
            rebuilt = pa.Table.from_pandas(
                existing, schema=arrow_tbl.schema, preserve_index=False
            )
            combined = pa.concat_tables([rebuilt, arrow_tbl])
            tbl.overwrite(combined)
        else:
            tbl.append(arrow_tbl)

        _logger.debug(
            "Technical indicators upserted for %s (%d rows)",
            ticker,
            len(dates),
        )

    def get_technical_indicators(
        self,
        ticker: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> pd.DataFrame:
        """Return technical indicator rows for *ticker*.

        Args:
            ticker: Stock ticker symbol.
            start: Inclusive start date.
            end: Inclusive end date.

        Returns:
            DataFrame sorted by date ascending.
        """
        df = self._scan_ticker(_TECHNICAL_INDICATORS, ticker.upper())
        if df.empty:
            return df
        if start:
            df = df[pd.to_datetime(df["date"]).dt.date >= start]
        if end:
            df = df[pd.to_datetime(df["date"]).dt.date <= end]
        return df.sort_values("date").reset_index(drop=True)

    # ------------------------------------------------------------------
    # Analysis summary
    # ------------------------------------------------------------------

    def insert_analysis_summary(
        self, ticker: str, summary: Dict[str, Any]
    ) -> None:
        """Append a daily analysis summary snapshot for *ticker*.

        Args:
            ticker: Stock ticker symbol.
            summary: Dict with keys matching the ``stocks.analysis_summary`` schema.
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
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent analysis summary for *ticker*.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Dict of analysis fields, or ``None`` if no record exists.
        """
        df = self._scan_ticker(_ANALYSIS_SUMMARY, ticker.upper())
        if df.empty:
            return None
        return (
            df.sort_values("analysis_date", ascending=False).iloc[0].to_dict()
        )

    def get_all_latest_analysis_summary(
        self,
        limit: Optional[int] = None,
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
        """Return all analysis summary rows for *ticker* sorted by date ascending.

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
        run_dict: Dict[str, Any],
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
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent forecast run for *ticker* and *horizon_months*.

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

        Drops any existing rows for the same ``(ticker, horizon_months, run_date)``
        before inserting to keep the table clean on re-runs.  Loads the table
        object only once to avoid a second catalog round-trip.

        Args:
            ticker: Stock ticker symbol.
            horizon_months: Forecast horizon (3, 6, or 9).
            run_date: The date this forecast was run.
            forecast_df: DataFrame with columns ``ds``, ``yhat``, ``yhat_lower``,
                ``yhat_upper`` as returned by Prophet.
        """
        if forecast_df.empty:
            return

        run_date = _to_date(run_date)

        # Fix #2: load table once; filter existing in-memory
        tbl, existing = self._load_table_and_scan(_FORECASTS)

        if not existing.empty:
            # Fix #10: compare date objects directly
            run_date_vals = [_to_date(d) for d in existing["run_date"]]
            mask = [
                t == ticker and h == int(horizon_months) and d == run_date
                for t, h, d in zip(
                    existing["ticker"],
                    existing["horizon_months"],
                    run_date_vals,
                )
            ]
            existing = existing[[not m for m in mask]]

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
                    new_rows["predicted_price"], pa.float64()
                ),
                "lower_bound": pa.array(new_rows["lower_bound"], pa.float64()),
                "upper_bound": pa.array(new_rows["upper_bound"], pa.float64()),
            }
        )

        if not existing.empty:
            arrow_existing = pa.Table.from_pandas(
                existing, schema=arrow_new.schema, preserve_index=False
            )
            combined = pa.concat_tables([arrow_existing, arrow_new])
            tbl.overwrite(combined)
        else:
            tbl.append(arrow_new)

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
