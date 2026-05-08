"""Load daily OHLCV bars from ``stocks.ohlcv`` (Iceberg) into
in-memory dicts keyed by ticker → list[BarData].

Single bulk DuckDB query (CLAUDE.md §4.1 #1); ``period_end``
clamped to today UTC to enforce no-look-ahead at the data-source
layer (the evaluator further enforces T+1-fill semantics).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from backend.algo.backtest.types import BarData
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)


class BackedFutureBarError(ValueError):
    """Raised when the caller asks for a period ending in the future."""


def load_ohlcv_window(
    *,
    tickers: list[str],
    period_start: date,
    period_end: date,
) -> dict[str, list[BarData]]:
    """Bulk-load OHLCV for *tickers* over the closed interval.

    Returns ``{ticker: [BarData, ...]}`` sorted by date ascending.
    Tickers with no rows in the period are absent from the dict.

    Raises:
        BackedFutureBarError: if ``period_end`` is past today UTC.
        ValueError: if ``period_start`` > ``period_end`` or
                    *tickers* contains an obviously invalid name.
    """
    today = datetime.now(timezone.utc).date()
    if period_end > today:
        raise BackedFutureBarError(
            f"period_end {period_end.isoformat()} is past today "
            f"{today.isoformat()} — backtest can't peek at the future."
        )
    if period_start > period_end:
        raise ValueError(
            f"period_start {period_start.isoformat()} is after "
            f"period_end {period_end.isoformat()}."
        )
    if not tickers:
        return {}

    placeholders = ",".join(f"'{t}'" for t in tickers)
    sql = (
        "SELECT ticker, date, open, high, low, close, volume "
        "FROM ohlcv "
        f"WHERE ticker IN ({placeholders}) "
        "  AND date BETWEEN ? AND ? "
        "ORDER BY ticker, date"
    )

    rows = query_iceberg_table(
        "stocks.ohlcv", sql, [period_start, period_end],
    )

    grouped: dict[str, list[BarData]] = {}
    for r in rows:
        ticker = str(r["ticker"])
        raw_date = r["date"]
        bar = BarData(
            ticker=ticker,
            date=(
                raw_date if isinstance(raw_date, date)
                else date.fromisoformat(str(raw_date))
            ),
            open=Decimal(str(r["open"])),
            high=Decimal(str(r["high"])),
            low=Decimal(str(r["low"])),
            close=Decimal(str(r["close"])),
            volume=int(r["volume"] or 0),
        )
        grouped.setdefault(ticker, []).append(bar)
    return grouped
