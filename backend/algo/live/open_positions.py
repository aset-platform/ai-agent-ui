"""Algo-held tickers derived from algo.events.

Used by `_scoped_tickers_for_strategy(scope="watchlist")` so
strategies with `universe.scope=watchlist` can always iterate
over (and exit) positions opened by the algo runtime.

Read-only. Iceberg query is the authoritative source — no
Kite fallback. Fail-open: empty set on any read failure.

The default scope is `("live",)` — only real-money fills
count. Pass `modes=("live", "paper")` to include paper-mode
fills (used by validation flows + dashboard surfaces that
want to render paper positions with a badge).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from uuid import UUID

from backend.cache import get_cache
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)

_LOOKBACK_SINCE = "2024-01-01"
_CACHE_TTL_S = 60

# paper runtime emits `type='order_filled'` (no `_live`
# suffix); live runtime emits `type='order_filled_live'`.
_FILL_TYPES_BY_MODE = {
    "live": "order_filled_live",
    "paper": "order_filled",
}


async def open_algo_positions(
    user_id: UUID,
    modes: Sequence[str] = ("live",),
) -> set[str]:
    """Tickers with net long qty > 0 across algo fills since
    ``_LOOKBACK_SINCE``.

    Reads from ``algo.events`` filtered to the requested
    ``modes`` (default: live-only). Net qty per symbol =
    sum(qty if side=BUY else -qty); ignores
    payload.dry_run = True rows.

    Cached in Redis at
    ``cache:algo:open_positions:{user_id}:{modes_key}`` with
    60s TTL — the cache key carries the mode tuple so a
    live-only request doesn't poison a live+paper request
    (or vice versa). Returns empty set on any failure
    (fail-open — universe simply doesn't include algo-held
    tickers in that case, which is the safe degradation).
    """
    modes_key = ",".join(sorted(modes))
    cache = get_cache()
    cache_key = (
        f"cache:algo:open_positions:{user_id}:{modes_key}"
    )
    if cache is not None:
        cached_raw = cache.get(cache_key)
        if cached_raw:
            try:
                return set(json.loads(cached_raw))
            except (ValueError, TypeError):
                pass

    # Build a (mode, type) match list so one SQL hits both.
    pairs: list[tuple[str, str]] = []
    for m in modes:
        t = _FILL_TYPES_BY_MODE.get(m)
        if t is None:
            continue
        pairs.append((m, t))
    if not pairs:
        return set()

    mode_in = ",".join(f"'{m}'" for m, _ in pairs)
    type_in = ",".join(f"'{t}'" for _, t in pairs)
    try:
        rows = await asyncio.to_thread(
            query_iceberg_table,
            "algo.events",
            "SELECT ts_ns, payload_json "
            "FROM events "
            "WHERE user_id = ? "
            f"  AND mode IN ({mode_in}) "
            f"  AND type IN ({type_in}) "
            "  AND ts_date >= ? "
            "ORDER BY ts_ns ASC",
            [str(user_id), _LOOKBACK_SINCE],
        )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "open_algo_positions iceberg read failed "
            "for user=%s modes=%s",
            user_id, modes_key, exc_info=True,
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
