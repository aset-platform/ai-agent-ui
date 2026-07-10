"""Current stocks.universe_snapshot ticker membership — the SAME
set LiveRuntime._bucket_by_ticker treats as tradeable (any row with
a non-null liquidity_bucket at any rebalance, not just the latest
top-200 cohort). Used to warn (not block) when a user adds a ticker
to a strategy's allowed_tickers that can never satisfy this set —
see ASETPLTFRM-471.
"""
from __future__ import annotations

import json
import logging

from backend.cache import get_cache
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)

# Scope-level data (identical across all users/strategies, changes
# only on the weekly universe rebalance job) — TTL-cached per
# CLAUDE.md §4.1 rule 6. 300s matches backend.cache.TTL_STABLE;
# invalidated automatically by snapshot_job._upsert_snapshot's
# get_cache().invalidate("cache:universe:*") on every rebalance
# write, so no new invalidation code is needed here.
_UNIVERSE_MEMBERS_CACHE_KEY = "cache:universe:members"
_UNIVERSE_MEMBERS_CACHE_TTL = 300


def _load_in_universe_tickers() -> set[str] | None:
    """Return the full in-universe ticker set, cache-first.

    Cache read/write failures are swallowed — the cache is
    best-effort, never a hard dependency. Returns ``None`` only
    when the underlying DuckDB query itself fails, matching the
    prior fail-open behaviour.
    """
    try:
        cache = get_cache()
        cached = cache.get(_UNIVERSE_MEMBERS_CACHE_KEY)
        if cached is not None:
            return set(json.loads(cached))
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "get_off_universe_tickers: cache read failed: %s — "
            "falling through to DuckDB",
            exc,
        )

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
        return None

    in_universe = {r["ticker"] for r in rows if r.get("ticker")}
    try:
        get_cache().set(
            _UNIVERSE_MEMBERS_CACHE_KEY,
            json.dumps(sorted(in_universe)),
            ttl=_UNIVERSE_MEMBERS_CACHE_TTL,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "get_off_universe_tickers: cache write failed: %s — "
            "continuing without caching this result",
            exc,
        )
    return in_universe


def get_off_universe_tickers(tickers: list[str]) -> list[str]:
    """Subset of ``tickers`` absent from ``stocks.universe_snapshot``.

    Fail-open: returns ``[]`` on an empty input or a query failure —
    this drives a soft UI warning, never a hard block, so silently
    under-warning on a transient DuckDB hiccup is the safe failure
    mode (never falsely flag every ticker as off-universe).

    The in-universe ticker set is scope-level data (identical across
    users/strategies) and is TTL-cached — see
    ``_load_in_universe_tickers``.
    """
    if not tickers:
        return []
    in_universe = _load_in_universe_tickers()
    if in_universe is None:
        return []
    return sorted(t for t in tickers if t not in in_universe)
