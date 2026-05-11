"""Daily factor compute orchestrator. Runs at 23:00 IST.

Loads OHLCV (with ~2yr warmup so SMA200 + 252d windows are
populated), NIFTY series, and per-sector index series once. Per
ticker, computes all 7 factor families and writes one row per
``(ticker, bar_date)`` to ``stocks.daily_factors`` via the
NaN-replaceable upsert in the repo.

Universe-wide breadth is fetched once per date and joined into
every ticker's row for that date so any strategy reading the
cached factor row gets the breadth context for free.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from backend.algo.factors.breadth import (
    compute_breadth_for_date as _compute_breadth_for_date,
)
from backend.algo.factors.lowvol import compute_lowvol
from backend.algo.factors.momentum import compute_momentum
from backend.algo.factors.quality import compute_quality
from backend.algo.factors.relative_strength import (
    compute_relative_strength,
)
from backend.algo.factors.repo import FactorRow, upsert_factors
from backend.algo.factors.trend import compute_trend
from backend.algo.factors.volume import compute_volume
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)

# 2 calendar years gives ~252 trading days for SMA200 / mom_12_1.
WARMUP_DAYS = 730
NIFTY_TICKER = "^NSEI"
SECTOR_INDEX_MAP = {
    "IT": "^CNXIT",
    "Banks": "^NSEBANK",
    "Banking": "^NSEBANK",
    "Auto": "^CNXAUTO",
    "Pharma": "^CNXPHARMA",
    "Pharmaceutical": "^CNXPHARMA",
    "FMCG": "^CNXFMCG",
    "Consumer Goods": "^CNXFMCG",
    "Metals": "^CNXMETAL",
    "Metal": "^CNXMETAL",
    "Energy": "^CNXENERGY",
    "Realty": "^CNXREALTY",
    "Real Estate": "^CNXREALTY",
    "Financial Services": "^CNXFINANCE",
}


def _get_universe() -> list[str]:
    """Distinct tickers from stocks.ohlcv with recent activity.
    Excludes index/futures/macro tickers."""
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT DISTINCT ticker FROM ohlcv "
        "WHERE date >= ? AND ticker NOT LIKE '^%' "
        "AND ticker NOT LIKE '%=F' AND ticker NOT LIKE 'DX-%' "
        "ORDER BY ticker",
        [date.today() - timedelta(days=30)],
    )
    return [r["ticker"] for r in rows]


def _load_ohlcv_for_ticker(
    ticker: str, start: date, end: date,
) -> pd.DataFrame:
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT date AS bar_date, open, high, low, close, volume "
        "FROM ohlcv WHERE ticker = ? AND date BETWEEN ? AND ? "
        "ORDER BY date ASC",
        [ticker, start, end],
    )
    return pd.DataFrame(rows)


def _load_ohlcv_bulk(
    tickers: list[str], start: date, end: date,
) -> dict[str, pd.DataFrame]:
    """Single DuckDB read for the whole universe — splits the
    result into a per-ticker dict in memory.

    Per CLAUDE.md §4.1 #1: 'Batch reads — single DuckDB
    WHERE ticker IN (...) → pre-load dict. Never N individual
    Iceberg reads.' The original
    ``_load_ohlcv_for_ticker(t)`` loop did N=810 individual
    reads per backfill day, dominating wall time.
    """
    if not tickers:
        return {}
    placeholders = ",".join(["?"] * len(tickers))
    rows = query_iceberg_table(
        "stocks.ohlcv",
        f"SELECT ticker, date AS bar_date, open, high, low, "
        f"close, volume FROM ohlcv "
        f"WHERE ticker IN ({placeholders}) "
        f"AND date BETWEEN ? AND ? "
        f"ORDER BY ticker ASC, date ASC",
        [*tickers, start, end],
    )
    if not rows:
        return {t: pd.DataFrame() for t in tickers}
    df = pd.DataFrame(rows)
    return {
        t: g.drop(columns=["ticker"]).reset_index(drop=True)
        for t, g in df.groupby("ticker", sort=False)
    }


def _load_nifty_history(start: date, end: date) -> pd.DataFrame:
    return _load_ohlcv_for_ticker(NIFTY_TICKER, start, end)


def _load_sector_indices_history(
    start: date, end: date,
) -> dict[str, pd.DataFrame]:
    indices = sorted(set(SECTOR_INDEX_MAP.values()))
    by_idx: dict[str, pd.DataFrame] = {}
    for idx in indices:
        df = _load_ohlcv_for_ticker(idx, start, end)
        if not df.empty:
            by_idx[idx] = df
    sector_to_df: dict[str, pd.DataFrame] = {}
    for sector, idx in SECTOR_INDEX_MAP.items():
        if idx in by_idx:
            sector_to_df[sector] = by_idx[idx]
    return sector_to_df


def _lookup_sector(tickers: list[str]) -> dict[str, str | None]:
    if not tickers:
        return {}
    placeholders = ",".join(["?"] * len(tickers))
    rows = query_iceberg_table(
        "stocks.piotroski_scores",
        f"SELECT ticker, sector FROM piotroski_scores "
        f"WHERE ticker IN ({placeholders}) "
        f"ORDER BY ticker, score_date DESC",
        list(tickers),
    )
    out: dict[str, str | None] = {t: None for t in tickers}
    for r in rows:
        if out.get(r["ticker"]) is None:
            out[r["ticker"]] = r.get("sector")
    return out


def run_compute_job(
    as_of: date | None = None, days: int = 1,
) -> int:
    if as_of is None:
        as_of = date.today()
    period_start = as_of - timedelta(days=days - 1)
    load_start = as_of - timedelta(days=WARMUP_DAYS)

    universe = _get_universe()
    if not universe:
        _logger.warning("Empty universe — skipping factor compute")
        return 0
    nifty = _load_nifty_history(load_start, as_of)
    sector_indices = _load_sector_indices_history(load_start, as_of)
    sector_lookup = _lookup_sector(universe)

    breadth_by_date: dict[date, dict[str, float]] = {
        d: _compute_breadth_for_date(d)
        for d in (
            period_start + timedelta(days=i)
            for i in range((as_of - period_start).days + 1)
        )
    }

    # Per CLAUDE.md §4.1 #1 + #2 — bulk read + bulk commit.
    # Old: 810 single-ticker DuckDB reads per backfill day +
    # 810 Iceberg commits per day. Now: 1 bulk DuckDB read for
    # the full ticker × date matrix + 1 Iceberg commit at the
    # end. Backfill ETA dropped from ~27 hours (per-ticker
    # commit) → ~10 hours (bulk-commit) → ~10–30 minutes
    # (bulk-read + bulk-commit on a 180-day window).
    _logger.info(
        "compute_daily_factors: bulk-loading OHLCV for %d "
        "tickers (%s → %s)…",
        len(universe), load_start, as_of,
    )
    history_by_ticker = _load_ohlcv_bulk(
        universe, load_start, as_of,
    )

    all_rows: list[FactorRow] = []
    for ticker in universe:
        history = history_by_ticker.get(ticker, pd.DataFrame())
        if history.empty or len(history) < 30:
            continue
        sector = sector_lookup.get(ticker)
        per_date: dict[date, dict[str, float]] = {}

        def _merge(src: dict[date, dict[str, float]]) -> None:
            for d, vals in src.items():
                per_date.setdefault(d, {}).update(vals)

        _merge(compute_momentum(history))
        _merge(compute_lowvol(history, nifty))
        _merge(compute_trend(history))
        _merge(compute_volume(history))
        _merge(compute_relative_strength(
            history, nifty, sector=sector,
            sector_indices=sector_indices,
        ))
        _merge(compute_quality(ticker, period_start, as_of))

        for d, vals in per_date.items():
            if d < period_start or d > as_of:
                continue
            merged = {**vals, **breadth_by_date.get(d, {})}
            all_rows.append(FactorRow(
                ticker=ticker, bar_date=d,
                values=merged, sector=sector,
            ))

    written = upsert_factors(all_rows) if all_rows else 0
    _logger.info(
        "compute_daily_factors: wrote %d rows for %d tickers "
        "(as_of=%s, days=%d) in 1 Iceberg commit",
        written, len(universe), as_of, days,
    )
    return written
