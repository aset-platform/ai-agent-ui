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

from backend.algo.broker.redis_keys import (
    build_freeze_fallback_flag_key,
    build_freeze_key,
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
    "should_emit_fallback_event",
]


def default_for_bucket(bucket: str | None) -> int:
    """Return the defensive freeze-qty default for ``bucket``.

    ``None`` or any unrecognised string maps to ``"unknown"`` (50k).
    """
    key = bucket if bucket in _NSE_DEFAULTS else "unknown"
    return _NSE_DEFAULTS[key]


def _refresh_freeze_map(
    kc: Any, redis_client: Any, key: str,
) -> dict[str, int]:
    """Pull the full ``kite.instruments("NSE")`` list and write it to
    Redis as a hash. Returns the in-memory dict so the immediate
    caller doesn't have to round-trip Redis again.

    Any SDK failure is re-raised — the cache miss path is allowed
    to fail; the caller decides whether to fall back to defaults
    or propagate. Redis write failures are swallowed (we still want
    to serve the current request even if persistence dies).
    """
    instruments = kc.instruments("NSE")
    mapping: dict[str, int] = {}
    for row in instruments or []:
        sym = row.get("tradingsymbol")
        if not sym:
            continue
        raw_fq = row.get("freeze_qty")
        try:
            fq = int(raw_fq) if raw_fq is not None else 0
        except (TypeError, ValueError):
            fq = 0
        mapping[str(sym)] = fq
    if not mapping:
        return mapping
    try:
        redis_client.hset(key, mapping={
            k: str(v) for k, v in mapping.items()
        })
        redis_client.expire(key, _FREEZE_TTL_S)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "freeze_cache: redis hset failed key=%s err=%s — "
            "current request will serve from in-memory map",
            key, exc,
        )
    return mapping


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
