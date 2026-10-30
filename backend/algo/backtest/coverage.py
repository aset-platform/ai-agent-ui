"""Intraday coverage probe for stocks.intraday_bars.

Single source of truth for the finest execution-clock resolution
available per ticker over a window. Read-only, batched, zero Kite —
used by the backtest two-clock engine and (later) the transparency UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

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


@dataclass(frozen=True)
class GapReport:
    """Per-ticker missing trading days for one intraday interval.

    ``expected_days`` is the size of the reference trading-day set —
    the union of every date ANY requested ticker traded in-window, a
    self-contained proxy for "market was open" that needs no external
    holiday calendar. ``missing_dates`` are reference days the ticker
    has zero bars for."""

    ticker: str
    expected_days: int
    present_days: int
    missing_dates: tuple[date, ...]
    partial_dates: tuple[date, ...] = ()


@dataclass(frozen=True)
class CoverageGapSummary:
    """Monitoring view (PRE-5): the subset of a universe with real
    coverage gaps (>=1 missing full trading day), plus context. A
    24-of-25-bar day is NOT a gap, so partial-only tickers are
    excluded — ``gap_tickers`` flags genuine ingest dropouts (like a
    ticker that fell out of the ingest universe)."""

    as_of: date
    lookback_days: int
    reference_days: int
    total_tickers: int
    gap_tickers: tuple[GapReport, ...]


def _as_date(val: object) -> date | None:
    """Iceberg ``bar_date`` reads back as VARCHAR sometimes (see
    dataset.py) — coerce to ``date`` defensively."""
    if isinstance(val, date):
        return val
    if isinstance(val, str) and val:
        return date.fromisoformat(val[:10])
    return None


def intraday_coverage(
    *,
    tickers: list[str],
    period_start: date,
    period_end: date,
) -> dict[str, TickerCoverage]:
    """Return per-ticker finest available intraday grain in-window."""
    if not tickers:
        return {}
    placeholders = ",".join(["?"] * len(tickers))
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
            *tickers,
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


def coverage_gaps(
    *,
    tickers: list[str],
    period_start: date,
    period_end: date,
    interval_sec: int = 900,
    min_bars: int | None = None,
) -> dict[str, GapReport]:
    """Per-ticker missing trading days for ``interval_sec`` in-window.

    Read-only, batched (single ``ticker IN (...)`` scan), zero Kite.
    The reference trading-day set is derived from the union of every
    date any requested ticker traded — a ticker missing a day that a
    peer traded is a real coverage gap.

    When ``min_bars`` is set, a day present but with fewer than
    ``min_bars`` bars is reported in ``partial_dates`` (distinct from
    fully-absent ``missing_dates``). A full NSE 15m equity session is
    25 bars (09:15-15:30)."""
    if not tickers:
        return {}
    placeholders = ",".join(["?"] * len(tickers))
    sql = (
        "SELECT ticker, bar_date, COUNT(*) AS bars "
        "FROM intraday_bars "
        f"WHERE ticker IN ({placeholders}) "
        "AND interval_sec = ? "
        "AND year_month BETWEEN ? AND ? "
        "AND bar_date BETWEEN ? AND ? "
        "GROUP BY ticker, bar_date"
    )
    rows = query_iceberg_table(
        _INTRADAY_TABLE,
        sql,
        [
            *tickers,
            interval_sec,
            period_start.isoformat()[:7],
            period_end.isoformat()[:7],
            period_start.isoformat(),
            period_end.isoformat(),
        ],
    )
    counts: dict[str, dict[date, int]] = {t: {} for t in tickers}
    reference: set[date] = set()
    for r in rows:
        d = _as_date(r["bar_date"])
        if d is None:
            continue
        reference.add(d)
        counts.setdefault(r["ticker"], {})[d] = int(r["bars"])

    out: dict[str, GapReport] = {}
    for t in tickers:
        have = counts.get(t, {})
        missing = tuple(sorted(reference - have.keys()))
        if min_bars is None:
            partial: tuple[date, ...] = ()
        else:
            partial = tuple(
                sorted(d for d, n in have.items() if n < min_bars)
            )
        out[t] = GapReport(
            ticker=t,
            expected_days=len(reference),
            present_days=len(have),
            missing_dates=missing,
            partial_dates=partial,
        )
    return out


def sufficiently_covered(
    *,
    tickers: list[str],
    as_of: date,
    lookback_days: int = 90,
    min_pct: float = 95.0,
    interval_sec: int = 900,
) -> set[str]:
    """Tickers with >= ``min_pct`` of trading days covered in the
    trailing ``lookback_days`` window ending ``as_of``.

    The eligibility half of the dynamic universe gate (PRE-4): a
    ticker with insufficient 15m coverage can't be faithfully
    backtested on the intraday execution clock. Fail-open — if the
    coverage read is empty (no reference trading days), every input
    ticker is kept rather than nuking the universe, mirroring the
    ADTV-floor empty-snapshot degrade."""
    if not tickers:
        return set()
    gaps = coverage_gaps(
        tickers=tickers,
        period_start=as_of - timedelta(days=lookback_days),
        period_end=as_of,
        interval_sec=interval_sec,
    )
    return {
        t
        for t, g in gaps.items()
        if g.expected_days == 0
        or (g.present_days / g.expected_days) * 100.0 >= min_pct
    }


def coverage_gap_summary(
    *,
    tickers: list[str],
    as_of: date,
    lookback_days: int = 90,
    interval_sec: int = 900,
) -> CoverageGapSummary:
    """Report tickers with >=1 missing full trading day in the
    trailing ``lookback_days`` window ending ``as_of``.

    Sorted worst-first (fewest present days). Fully-covered tickers
    are omitted — the report is signal, not a full dump."""
    gaps = coverage_gaps(
        tickers=tickers,
        period_start=as_of - timedelta(days=lookback_days),
        period_end=as_of,
        interval_sec=interval_sec,
    )
    reference_days = max((g.expected_days for g in gaps.values()), default=0)
    flagged = sorted(
        (g for g in gaps.values() if g.missing_dates),
        key=lambda g: (g.present_days, g.ticker),
    )
    return CoverageGapSummary(
        as_of=as_of,
        lookback_days=lookback_days,
        reference_days=reference_days,
        total_tickers=len(tickers),
        gap_tickers=tuple(flagged),
    )
