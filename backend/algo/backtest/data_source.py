"""Backtest data-source readers — daily ``stocks.ohlcv`` and
intraday ``stocks.intraday_bars``.

Both loaders use single bulk DuckDB queries (CLAUDE.md §4.1 #1);
``period_end`` is clamped to today UTC to enforce no-look-ahead at
the data-source layer (the evaluator further enforces T+1-fill
semantics).
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.algo.backtest.types import BarData
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)

# Per CLAUDE.md §6.1: yfinance + jugaad-data leak NaN/None
# OHLCV cells. ``Decimal("None")`` raises ConversionSyntax;
# ``Decimal("nan")`` parses but propagates as a poison value
# downstream. Reject both at parse time.
_DECIMAL_SENTINELS = frozenset(
    {
        "",
        "none",
        "null",
        "nan",
        "n/a",
        "na",
        "nat",
    }
)


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
    warmup_days: int = 0,
) -> dict[str, list[BarData]]:
    """Bulk-load OHLCV for *tickers* over the closed interval.

    Returns ``{ticker: [BarData, ...]}`` sorted by date ascending.
    Tickers with no rows in the period are absent from the dict.

    ``warmup_days`` widens the read window backward by N calendar
    days so callers (the indicator engine) have history for
    rolling features (SMA200 needs ~200 prior bars). The look-ahead
    guard still applies to ``period_end``.

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
    if warmup_days < 0:
        raise ValueError("warmup_days must be non-negative.")
    effective_start = (
        period_start - timedelta(days=warmup_days)
        if warmup_days > 0
        else period_start
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
        "stocks.ohlcv",
        sql,
        [effective_start, period_end],
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
            open_d is None
            or high_d is None
            or low_d is None
            or close_d is None
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
                raw_date
                if isinstance(raw_date, date)
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
            "OHLCV cells (CLAUDE.md §6.1)",
            skipped,
        )
    return grouped


# Mirrors ``KiteClient._INTRADAY_INTERVAL_MAP`` + the slice 1a
# ``_KITE_WINDOW_DAYS`` keys. Duplicated here so the loader can
# reject bad ``interval_sec`` before any catalog read.
_SUPPORTED_INTRADAY_SECONDS = frozenset({60, 300, 900})


def load_intraday_bars_window(
    *,
    tickers: list[str],
    interval_sec: int,
    period_start: date,
    period_end: date,
    warmup_days: int = 0,
) -> dict[str, list[BarData]]:
    """Bulk-load intraday bars for *tickers* at ``interval_sec``
    over the closed interval ``[period_start, period_end]``.

    ASETPLTFRM-400 slice 2 — the intraday-cadence sibling of
    :func:`load_ohlcv_window`. Reads from ``stocks.intraday_bars``
    (slice 1b) which is partitioned ``(ticker, year_month)``; the
    SQL filters on both ``year_month`` (range) and ``bar_date``
    (range) so DuckDB's iceberg_scan can prune to ~12 partitions
    per ticker per year instead of opening the whole table.

    Returns ``{ticker: [BarData, ...]}`` sorted by
    ``bar_open_ts_ns`` ascending. Each ``BarData`` carries both
    ``date`` (the IST trading day the bar opened in) and
    ``bar_open_ts_ns`` (the ns-since-epoch UTC stamp at bar
    open) — downstream runner consumers key on the ns-level
    timestamp.

    Parameters
    ----------
    tickers : list[str]
        Ticker symbols (e.g. ``["ITC.NS", "RELIANCE.NS"]``).
    interval_sec : int
        Bar cadence — must be 60 (1m), 300 (5m), or 900 (15m).
    period_start, period_end : date
        Inclusive IST trading-day window bounds.
    warmup_days : int
        Calendar days to extend backward. ``RSI(14)`` on 15 m
        needs ~3 prior trading days; ``SMA(200)`` on 15 m needs
        ~8 prior trading days. Caller picks the appropriate value
        per indicator.

    Raises
    ------
    BackedFutureBarError
        ``period_end`` is past today UTC.
    ValueError
        ``period_start > period_end``, ``warmup_days < 0``, or
        ``interval_sec`` not in {60, 300, 900}.
    """
    if interval_sec not in _SUPPORTED_INTRADAY_SECONDS:
        raise ValueError(
            f"interval_sec={interval_sec} not supported; "
            f"use one of {sorted(_SUPPORTED_INTRADAY_SECONDS)}.",
        )
    today = datetime.now(timezone.utc).date()
    if period_end > today:
        raise BackedFutureBarError(
            f"period_end {period_end.isoformat()} is past today "
            f"{today.isoformat()} — backtest can't peek at the future.",
        )
    if period_start > period_end:
        raise ValueError(
            f"period_start {period_start.isoformat()} is after "
            f"period_end {period_end.isoformat()}.",
        )
    if warmup_days < 0:
        raise ValueError("warmup_days must be non-negative.")
    effective_start = (
        period_start - timedelta(days=warmup_days)
        if warmup_days > 0
        else period_start
    )
    if not tickers:
        return {}

    placeholders = ",".join(f"'{t}'" for t in tickers)
    start_iso = effective_start.isoformat()
    end_iso = period_end.isoformat()
    # ``year_month`` and ``bar_date`` are both fixed-width
    # ISO strings, so lexicographic BETWEEN matches chronological.
    start_ym = start_iso[:7]
    end_ym = end_iso[:7]
    sql = (
        "SELECT ticker, bar_date, bar_open_ts_ns, open, high, "
        "  low, close, volume "
        "FROM intraday_bars "
        f"WHERE ticker IN ({placeholders}) "
        "  AND interval_sec = ? "
        "  AND year_month BETWEEN ? AND ? "
        "  AND bar_date BETWEEN ? AND ? "
        "ORDER BY ticker, bar_open_ts_ns"
    )

    rows = query_iceberg_table(
        "stocks.intraday_bars",
        sql,
        [interval_sec, start_ym, end_ym, start_iso, end_iso],
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
            open_d is None
            or high_d is None
            or low_d is None
            or close_d is None
        ):
            # The new ``stocks.intraday_bars`` has ``required=True``
            # on every OHLC col so NaN should never reach Iceberg.
            # Belt-and-braces: skip if it does.
            skipped += 1
            continue
        ts_ns = r.get("bar_open_ts_ns")
        if ts_ns is None:
            skipped += 1
            continue
        raw_date = r["bar_date"]
        if isinstance(raw_date, date):
            bar_date = raw_date
        else:
            try:
                bar_date = date.fromisoformat(str(raw_date)[:10])
            except ValueError:
                skipped += 1
                continue
        bar = BarData(
            ticker=ticker,
            date=bar_date,
            open=open_d,
            high=high_d,
            low=low_d,
            close=close_d,
            volume=int(r["volume"] or 0),
            bar_open_ts_ns=int(ts_ns),
        )
        grouped.setdefault(ticker, []).append(bar)
    if skipped:
        _logger.info(
            "load_intraday_bars_window: skipped %d bars with "
            "NaN/None cells or missing bar_open_ts_ns",
            skipped,
        )
    return grouped
