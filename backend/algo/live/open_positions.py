"""Algo-held tickers derived from algo.events.

Used by `_scoped_tickers_for_strategy(scope="watchlist")` so
strategies with `universe.scope=watchlist` can always iterate
over (and exit) positions opened by the algo runtime.

Read-only. Iceberg query is the authoritative source — no
Kite fallback. Fail-open: empty set on any read failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from backend.cache import get_cache
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)

_LOOKBACK_SINCE = "2024-01-01"
_CACHE_TTL_S = 60


async def open_algo_positions(user_id: UUID) -> set[str]:
    """Tickers with net long qty > 0 across all live algo
    fills since 2024-01-01.

    Reads from ``algo.events`` (mode='live',
    type='order_filled_live'). Net qty per symbol =
    sum(qty if side=BUY else -qty); ignores
    payload.dry_run = True rows.

    Cached in Redis at ``cache:algo:open_positions:{user_id}``
    with 60s TTL. Returns empty set on any failure
    (fail-open — universe simply doesn't include algo-held
    tickers in that case, which is the safe degradation).
    """
    cache = get_cache()
    cache_key = f"cache:algo:open_positions:{user_id}"
    if cache is not None:
        cached_raw = cache.get(cache_key)
        if cached_raw:
            try:
                return set(json.loads(cached_raw))
            except (ValueError, TypeError):
                pass

    try:
        rows = await asyncio.to_thread(
            query_iceberg_table,
            "algo.events",
            "SELECT ts_ns, payload_json "
            "FROM events "
            "WHERE user_id = ? "
            "  AND mode = 'live' "
            "  AND type = 'order_filled_live' "
            "  AND ts_date >= ? "
            "ORDER BY ts_ns ASC",
            [str(user_id), _LOOKBACK_SINCE],
        )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "open_algo_positions iceberg read failed "
            "for user=%s",
            user_id, exc_info=True,
        )
        return set()

    net: dict[str, int] = {}
    for row in rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (ValueError, TypeError):
            continue
        if payload.get("dry_run"):
            continue
        sym = payload.get("symbol") or ""
        if not sym:
            continue
        side = (payload.get("side") or "").upper()
        try:
            qty = int(payload.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if side == "BUY":
            net[sym] = net.get(sym, 0) + qty
        elif side == "SELL":
            net[sym] = net.get(sym, 0) - qty

    out = {sym for sym, q in net.items() if q > 0}

    if cache is not None:
        try:
            cache.set(
                cache_key,
                json.dumps(sorted(out)),
                ttl=_CACHE_TTL_S,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "open_algo_positions cache set failed",
                exc_info=True,
            )
    return out
