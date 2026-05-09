"""Load daily OHLCV bars from ``stocks.ohlcv`` (Iceberg) into
in-memory dicts keyed by ticker → list[BarData].

Single bulk DuckDB query (CLAUDE.md §4.1 #1); ``period_end``
clamped to today UTC to enforce no-look-ahead at the data-source
layer (the evaluator further enforces T+1-fill semantics).
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.algo.backtest.types import BarData
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)

# Per CLAUDE.md §6.1: yfinance + jugaad-data leak NaN/None
# OHLCV cells. ``Decimal("None")`` raises ConversionSyntax;
# ``Decimal("nan")`` parses but propagates as a poison value
# downstream. Reject both at parse time.
_DECIMAL_SENTINELS = frozenset({
    "", "none", "null", "nan", "n/a", "na", "nat",
})


def _safe_decimal(val: Any) -> Decimal | None:
    """Return ``Decimal(val)`` or None for NaN/sentinel values."""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    if not s or s.lower() in _DECIMAL_SENTINELS:
        return None
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if d.is_nan():
        return None
    return d


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
    skipped = 0
    for r in rows:
        ticker = str(r["ticker"])
        open_d = _safe_decimal(r["open"])
        high_d = _safe_decimal(r["high"])
        low_d = _safe_decimal(r["low"])
        close_d = _safe_decimal(r["close"])
        if (
            open_d is None or high_d is None
            or low_d is None or close_d is None
        ):
            # Pre-market flat candles, dividend-only days, and
            # yfinance gaps surface as NaN closes. Skip — the
            # bar would corrupt every downstream calc.
            skipped += 1
            continue
        raw_date = r["date"]
        bar = BarData(
            ticker=ticker,
            date=(
                raw_date if isinstance(raw_date, date)
                else date.fromisoformat(str(raw_date))
            ),
            open=open_d,
            high=high_d,
            low=low_d,
            close=close_d,
            volume=int(r["volume"] or 0),
        )
        grouped.setdefault(ticker, []).append(bar)
    if skipped:
        _logger.info(
            "load_ohlcv_window: skipped %d bars with NaN/None "
            "OHLCV cells (CLAUDE.md §6.1)", skipped,
        )
    return grouped
