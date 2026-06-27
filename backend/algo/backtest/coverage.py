"""Intraday coverage probe for stocks.intraday_bars.

Single source of truth for the finest execution-clock resolution
available per ticker over a window. Read-only, batched, zero Kite —
used by the backtest two-clock engine and (later) the transparency UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from backend.db.duckdb_engine import query_iceberg_table

_INTRADAY_TABLE = "stocks.intraday_bars"
_BIG = 10**9


@dataclass(frozen=True)
class TickerCoverage:
    ticker: str
    finest_interval_sec: int | None
    covered_start: date | None
    covered_end: date | None
    trading_days: int


def intraday_coverage(
    *,
    tickers: list[str],
    period_start: date,
    period_end: date,
) -> dict[str, TickerCoverage]:
    """Return per-ticker finest available intraday grain in-window."""
    if not tickers:
        return {}
    placeholders = ",".join(f"'{t}'" for t in tickers)
    sql = (
        "SELECT ticker, interval_sec, "
        "MIN(bar_date) AS min_d, MAX(bar_date) AS max_d, "
        "COUNT(DISTINCT bar_date) AS days "
        "FROM intraday_bars "
        f"WHERE ticker IN ({placeholders}) "
        "AND year_month BETWEEN ? AND ? "
        "AND bar_date BETWEEN ? AND ? "
        "GROUP BY ticker, interval_sec"
    )
    rows = query_iceberg_table(
        _INTRADAY_TABLE,
        sql,
        [
            period_start.isoformat()[:7],
            period_end.isoformat()[:7],
            period_start.isoformat(),
            period_end.isoformat(),
        ],
    )
    best: dict[str, TickerCoverage] = {}
    for r in rows:
        t = r["ticker"]
        isec = int(r["interval_sec"])
        cur = best.get(t)
        if cur is None or isec < (cur.finest_interval_sec or _BIG):
            best[t] = TickerCoverage(
                ticker=t,
                finest_interval_sec=isec,
                covered_start=r["min_d"],
                covered_end=r["max_d"],
                trading_days=int(r["days"]),
            )
    for t in tickers:
        best.setdefault(
            t, TickerCoverage(t, None, None, None, 0)
        )
    return best
