"""Point-in-time universe resolver (REGIME-7).

Reads ``stocks.universe_snapshot`` and returns the cohort active
as-of ``bar_date``. Used by backtests + walk-forward to eliminate
survivorship bias.
"""
from __future__ import annotations

import logging
from datetime import date

from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)


def _query_snapshot_rows(bar_date: date) -> list[dict]:
    """Fetch all snapshot rows ``<= bar_date`` flagged
    ``included_in_top_200=True``. Caller picks the latest cohort."""
    return query_iceberg_table(
        "stocks.universe_snapshot",
        "SELECT rebalance_date, ticker FROM universe_snapshot "
        "WHERE rebalance_date <= ? "
        "  AND included_in_top_200 = TRUE",
        [bar_date],
    )


def resolve_pit_universe(bar_date: date) -> list[str]:
    """Tickers in the top-200 snapshot active as-of ``bar_date``.

    Picks the most recent ``rebalance_date`` ``<=`` ``bar_date`` and
    returns its ticker list (sorted, deduplicated). Returns an empty
    list if no snapshot exists yet — callers should fall back to the
    legacy non-PIT universe in that case.
    """
    rows = _query_snapshot_rows(bar_date)
    if not rows:
        return []
    latest = max(r["rebalance_date"] for r in rows)
    return sorted({
        r["ticker"] for r in rows
        if r["rebalance_date"] == latest
    })
