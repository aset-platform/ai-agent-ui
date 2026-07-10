"""Current stocks.universe_snapshot ticker membership — the SAME
set LiveRuntime._bucket_by_ticker treats as tradeable (any row with
a non-null liquidity_bucket at any rebalance, not just the latest
top-200 cohort). Used to warn (not block) when a user adds a ticker
to a strategy's allowed_tickers that can never satisfy this set —
see ASETPLTFRM-471.
"""
from __future__ import annotations

import logging

from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)


def get_off_universe_tickers(tickers: list[str]) -> list[str]:
    """Subset of ``tickers`` absent from ``stocks.universe_snapshot``.

    Fail-open: returns ``[]`` on an empty input or a query failure —
    this drives a soft UI warning, never a hard block, so silently
    under-warning on a transient DuckDB hiccup is the safe failure
    mode (never falsely flag every ticker as off-universe).
    """
    if not tickers:
        return []
    try:
        rows = query_iceberg_table(
            "stocks.universe_snapshot",
            "SELECT DISTINCT ticker FROM universe_snapshot "
            "WHERE liquidity_bucket IS NOT NULL",
            [],
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "get_off_universe_tickers: query failed: %s — "
            "skipping off-universe check (fail-open)",
            exc,
        )
        return []
    in_universe = {r["ticker"] for r in rows if r.get("ticker")}
    return sorted(t for t in tickers if t not in in_universe)
