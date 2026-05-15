"""Universe-level breadth: pct_above_{50,200}sma + midcap/largecap.

These are scope-wide values per date — the same value attaches to
every ticker's row in ``stocks.daily_factors`` so any strategy can
read the breadth context via the cached factor row.
"""
from __future__ import annotations

from datetime import date, timedelta

from backend.db.duckdb_engine import query_iceberg_table


def _fetch_breadth_pct(d: date, window: int) -> float:
    start = d - timedelta(days=window * 2)
    rows = query_iceberg_table(
        "stocks.ohlcv",
        f"WITH w AS ("
        f"  SELECT ticker, date AS bar_date, close, "
        f"         AVG(close) OVER ("
        f"             PARTITION BY ticker ORDER BY date "
        f"             ROWS BETWEEN {window - 1} PRECEDING "
        f"             AND CURRENT ROW"
        f"         ) AS sma "
        f"  FROM ohlcv WHERE date BETWEEN ? AND ? "
        f") "
        f"SELECT COUNT(*) FILTER (WHERE close > sma) AS above, "
        f"       COUNT(*) AS total "
        f"FROM w WHERE bar_date = ?",
        [start, d, d],
    )
    if not rows or not rows[0].get("total"):
        return float("nan")
    r = rows[0]
    return float(r["above"]) / float(r["total"])


def _fetch_midcap_largecap_ratio(d: date) -> float:
    # Yahoo's symbol for Nifty Midcap 150 is NIFTYMIDCAP150.NS
    # (NOT ^NIFMDCP150 — that variant returns silently-empty
    # downloads from Yahoo). Verified 2026-05-15: 7 years of
    # daily OHLCV from 2019-01-14.
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT ticker, close FROM ohlcv "
        "WHERE ticker IN ('NIFTYMIDCAP150.NS', '^NSEI') "
        "  AND date = ?",
        [d],
    )
    if not rows:
        return float("nan")
    by_t = {r["ticker"]: r["close"] for r in rows}
    mid = by_t.get("NIFTYMIDCAP150.NS")
    large = by_t.get("^NSEI")
    if mid is None or large is None or large == 0:
        return float("nan")
    return float(mid) / float(large)


def compute_breadth_for_date(d: date) -> dict[str, float]:
    return {
        "pct_above_50sma": _fetch_breadth_pct(d, 50),
        "pct_above_200sma": _fetch_breadth_pct(d, 200),
        "midcap_largecap_ratio": _fetch_midcap_largecap_ratio(d),
    }
