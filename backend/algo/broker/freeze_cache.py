# backend/algo/broker/freeze_cache.py
"""Daily NSE freeze-quantity cache + defensive defaults (PR #4 §3.5).

NSE publishes a per-symbol "freeze quantity" — the maximum quantity
that can be placed in a single order. Larger orders MUST be split
into multiple submissions. Kite returns the freeze qty via
``kite.instruments("NSE")`` (~6k rows). To avoid hammering the SDK
once per order we cache the entire map in a Redis hash keyed on the
IST calendar date with a 25-hour TTL (survives one missed refresh).

When the SDK returns ``freeze_qty in (None, 0)`` for a symbol we
fall back to a defensive default keyed on the symbol's liquidity
bucket (see spec §7 Q3). On first use per (ticker, date) a
``freeze_qty_fallback_applied`` event is emitted so ops can audit
how often we're guessing.

Sync API by design — KiteClient is sync, the runtime calls into it
via ``asyncio.to_thread``. Keeping freeze_cache sync avoids forcing
a second event-loop hop on the hot order path.
"""
from __future__ import annotations

import logging
from typing import Any

from decimal import Decimal

from backend.algo.broker.redis_keys import (
    build_freeze_fallback_flag_key,
    build_freeze_key,
    build_tick_size_key,
)

_logger = logging.getLogger(__name__)


# Defensive defaults — compiled from the most recent NSE freeze-qty
# circular. Annual review TODO; NSE updates the circular ~quarterly.
# Keyed on the same liquidity_bucket vocabulary used by PR #2
# (largecap / midcap / smallcap / unknown).
_NSE_DEFAULTS: dict[str, int] = {
    "largecap": 500_000,
    "midcap": 100_000,
    "smallcap": 50_000,
    # Most conservative — used when the runtime couldn't classify
    # the ticker (brand-new symbol, missing snapshot row, etc.).
    "unknown": 50_000,
}

# Cache TTL — 25h covers a missed refresh on a long weekend.
_FREEZE_TTL_S = 25 * 3600

# Re-export so callers can do `from freeze_cache import ...` for both
# the cache helpers and the key builders.
__all__ = [
    "_NSE_DEFAULTS",
    "build_freeze_key",
    "default_for_bucket",
    "get_freeze_qty",
    "get_tick_size",
    "should_emit_fallback_event",
]

# Default tick size — NSE equity default. The real per-symbol value
# is fetched from kite.instruments() and cached in Redis. This fallback
# is intentionally conservative: 0.05 is the smallest valid tick on NSE
# so rounding to it never violates a stricter (larger) tick requirement,
# but it CAN produce a price that is not a multiple of the true tick
# (e.g. 0.10 stocks). Callers MUST treat this as a "cache unavailable"
# fallback and log accordingly.
_DEFAULT_TICK_SIZE = Decimal("0.05")


def default_for_bucket(bucket: str | None) -> int:
    """Return the defensive freeze-qty default for ``bucket``.

    ``None`` or any unrecognised string maps to ``"unknown"`` (50k).
    """
    key = bucket if bucket in _NSE_DEFAULTS else "unknown"
    return _NSE_DEFAULTS[key]


def _refresh_freeze_map(
    kc: Any, redis_client: Any, key: str,
) -> dict[str, int]:
    """Pull the full ``kite.instruments("NSE")`` list, write freeze-qty
    AND tick-size hashes to Redis. Returns the in-memory freeze-qty dict.

    Any SDK failure is re-raised — the cache miss path is allowed
    to fail; the caller decides whether to fall back to defaults
    or propagate. Redis write failures are swallowed (we still want
    to serve the current request even if persistence dies).
    """
    instruments = kc.instruments("NSE")
    freeze_map: dict[str, int] = {}
    tick_map: dict[str, str] = {}
    for row in instruments or []:
        sym = row.get("tradingsymbol")
        if not sym:
            continue
        raw_fq = row.get("freeze_qty")
        try:
            fq = int(raw_fq) if raw_fq is not None else 0
        except (TypeError, ValueError):
            fq = 0
        freeze_map[str(sym)] = fq
        raw_ts = row.get("tick_size")
        try:
            ts = float(raw_ts) if raw_ts is not None else 0.05
        except (TypeError, ValueError):
            ts = 0.05
        tick_map[str(sym)] = str(ts)
    if not freeze_map:
        return freeze_map
    try:
        redis_client.hset(key, mapping={
            k: str(v) for k, v in freeze_map.items()
        })
        redis_client.expire(key, _FREEZE_TTL_S)
        tick_key = build_tick_size_key()
        redis_client.hset(tick_key, mapping=tick_map)
        redis_client.expire(tick_key, _FREEZE_TTL_S)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "freeze_cache: redis hset failed key=%s err=%s — "
            "current request will serve from in-memory map",
            key, exc,
        )
    return freeze_map


def get_freeze_qty(
    *,
    kc: Any,
    redis_client: Any,
    symbol: str,
) -> int:
    """Return the cached NSE freeze qty for ``symbol``.

    Behaviour:
        - Cache hit (Redis hash has the date key + symbol field) →
          return the cached int. Zero is preserved (caller decides
          whether to fall back via ``default_for_bucket``).
        - Cache miss (no hash entry) → SDK call, hash populated,
          symbol's value returned. Returns ``0`` if the symbol isn't
          in the SDK response either.
        - Redis unavailable (``redis_client is None`` or raises) →
          single SDK call straight-through, no caching. Caller is
          unaffected; on the next call we try Redis again.

    The caller is responsible for translating ``0`` into the
    bucket-keyed default via ``default_for_bucket(bucket)``.
    """
    key = build_freeze_key()
    if redis_client is None:
        try:
            instruments = kc.instruments("NSE") or []
            for row in instruments:
                if row.get("tradingsymbol") == symbol:
                    raw = row.get("freeze_qty")
                    return int(raw) if raw is not None else 0
            return 0
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "freeze_cache: SDK fetch failed no-redis path "
                "symbol=%s err=%s — caller will use default",
                symbol, exc,
            )
            return 0
    try:
        cached = redis_client.hget(key, symbol)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "freeze_cache: redis hget failed key=%s symbol=%s "
            "err=%s — proceeding to SDK refresh", key, symbol, exc,
        )
        cached = None
    if cached is not None:
        try:
            return int(cached)
        except (TypeError, ValueError):
            return 0
    # Cache miss — refresh whole hash. Any SDK failure surfaces here.
    try:
        mapping = _refresh_freeze_map(kc, redis_client, key)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "freeze_cache: instruments() refresh failed err=%s — "
            "caller will use default", exc,
        )
        return 0
    return mapping.get(symbol, 0)


def get_tick_size(
    *,
    kc: Any,
    redis_client: Any,
    symbol: str,
) -> Decimal:
    """Return the NSE tick size for ``symbol`` as a Decimal.

    Cache strategy mirrors ``get_freeze_qty``:
    - Redis hash hit → return parsed Decimal.
    - Cache miss → instruments() refresh (populates BOTH freeze and
      tick_size hashes), then return from in-memory map.
    - Redis unavailable → single SDK call, no caching.
    - Any failure → returns ``_DEFAULT_TICK_SIZE`` (0.05) with a
      warning so the order is still attempted (may fail Kite-side
      for 0.10-tick stocks, but is recoverable).
    """
    tick_key = build_tick_size_key()
    if redis_client is None:
        try:
            instruments = kc.instruments("NSE") or []
            for row in instruments:
                if row.get("tradingsymbol") == symbol:
                    raw = row.get("tick_size")
                    try:
                        return Decimal(str(float(raw))) if raw else _DEFAULT_TICK_SIZE
                    except (TypeError, ValueError):
                        return _DEFAULT_TICK_SIZE
            return _DEFAULT_TICK_SIZE
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "freeze_cache: tick_size SDK fetch failed no-redis "
                "symbol=%s err=%s — using default %s",
                symbol, exc, _DEFAULT_TICK_SIZE,
            )
            return _DEFAULT_TICK_SIZE
    try:
        cached = redis_client.hget(tick_key, symbol)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "freeze_cache: redis hget tick_size failed key=%s "
            "symbol=%s err=%s — proceeding to SDK refresh",
            tick_key, symbol, exc,
        )
        cached = None
    if cached is not None:
        try:
            return Decimal(str(float(cached)))
        except (TypeError, ValueError):
            return _DEFAULT_TICK_SIZE
    # Cache miss — refresh both hashes via the same instruments() call.
    try:
        freeze_key = build_freeze_key()
        in_mem = _refresh_freeze_map(kc, redis_client, freeze_key)
        _ = in_mem  # freeze side-effect; we re-read tick from Redis
        cached_after = redis_client.hget(tick_key, symbol)
        if cached_after is not None:
            return Decimal(str(float(cached_after)))
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "freeze_cache: tick_size refresh failed symbol=%s "
            "err=%s — using default %s",
            symbol, exc, _DEFAULT_TICK_SIZE,
        )
    return _DEFAULT_TICK_SIZE


def should_emit_fallback_event(
    *,
    redis_client: Any,
    symbol: str,
) -> bool:
    """Return True iff this is the FIRST fallback for (symbol, today).

    Uses a Redis SETNX flag with a 25h TTL so we never flood the
    event stream with one row per chunked order. If Redis is
    unavailable we conservatively emit the event — better to over-
    log a known-loud scenario than to lose visibility on it.
    """
    if redis_client is None:
        return True
    flag_key = build_freeze_fallback_flag_key(symbol=symbol)
    try:
        acquired = redis_client.set(
            flag_key, "1", nx=True, ex=_FREEZE_TTL_S,
        )
        return bool(acquired)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "freeze_cache: redis flag check failed symbol=%s "
            "err=%s — emitting event defensively", symbol, exc,
        )
        return True
