"""Quality factor — Piotroski f_score forward-filled to daily.

ROIC + accruals require quarterly fundamentals not yet present in
``stocks.quarterly_results`` in usable form. Deferred to a future
slice — emit NaN for now (the cached row simply lacks those keys
so any strategy reading them gets None and short-circuits).
"""
from __future__ import annotations

from datetime import date, timedelta

from backend.db.duckdb_engine import query_iceberg_table


def _load_piotroski(
    ticker: str, start: date, end: date,
) -> list[dict]:
    """Pull last ~400 calendar days of Piotroski rows so the
    forward-fill always has a starting value within the window."""
    return query_iceberg_table(
        "stocks.piotroski_scores",
        "SELECT score_date, total_score "
        "FROM piotroski_scores "
        "WHERE ticker = ? AND score_date BETWEEN ? AND ? "
        "ORDER BY score_date ASC",
        [ticker, start - timedelta(days=400), end],
    )


def compute_quality(
    ticker: str, start: date, end: date,
) -> dict[date, dict[str, float]]:
    scores = _load_piotroski(ticker, start, end)
    out: dict[date, dict[str, float]] = {}
    if not scores:
        cur = start
        while cur <= end:
            out[cur] = {"f_score": float("nan")}
            cur += timedelta(days=1)
        return out

    cur = start
    last_seen: float = float("nan")
    si = 0
    while cur <= end:
        while si < len(scores) and scores[si]["score_date"] <= cur:
            last_seen = float(scores[si]["total_score"])
            si += 1
        out[cur] = {"f_score": last_seen}
        cur += timedelta(days=1)
    return out
