# backend/algo/broker/redis_keys.py
"""Centralised Redis-key builders for the order-safety hardening layer.

Three key families live here:

1. ``algo:placeorder:dedup:{user_id}:{strategy_id}:{symbol}:{side}:
   {minute_bucket}`` — pre-submit duplicate guard (PR #4 §3.4,
   hardened in PR #5 Task 1.4). Content-addressed on
   ``(user, strategy, symbol, side, minute_bucket)`` — deliberately
   WITHOUT qty — so that (a) a retry that recomputes a different qty
   in the same minute collides with the original, and (b) the same
   signal firing twice in the same minute collides (cross-call
   duplicate guard). A legitimate same-symbol/side scale-in within
   the same minute is intentionally deduped (accepted tradeoff —
   safer default for real-money orders). SETNX with
   ``ALGO_DEDUP_TTL_S`` TTL (default 60 s).

2. ``kite:freeze:{date_ist}`` — once-per-day Redis hash of
   ``tradingsymbol -> freeze_qty`` (PR #4 §3.5). Used by
   ``freeze_cache.get_freeze_qty``.

3. ``kite:tick_size:{date_ist}`` — once-per-day Redis hash of
   ``tradingsymbol -> tick_size`` (float stored as string). Used by
   ``freeze_cache.get_tick_size`` to avoid hardcoding 0.05.

Keeping the builders in one module makes it easy to grep for any
key shape, swap in fakes in tests, and verify there is exactly one
source of truth for the format strings.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta


_DEDUP_KEY_FMT = (
    "algo:placeorder:dedup:{user_id}:{strategy_id}:"
    "{symbol}:{side}:{minute_bucket}"
)
_FREEZE_HASH_KEY_FMT = "kite:freeze:{date_ist}"
_FREEZE_FALLBACK_FLAG_FMT = (
    "kite:freeze:fallback:{date_ist}:{symbol}"
)
_TICK_SIZE_HASH_KEY_FMT = "kite:tick_size:{date_ist}"

# IST = UTC+05:30 (no DST in India)
_IST = timezone(timedelta(hours=5, minutes=30))


def _minute_bucket(now_unix: float | None = None) -> int:
    """Floor the current Unix time to the nearest minute.

    Wrapping ``time.time`` here gives tests a single
    monkeypatch surface (``redis_keys.time.time``) instead of
    having to patch a method on KiteClient.
    """
    if now_unix is None:
        now_unix = time.time()
    return int(now_unix // 60)


def build_dedup_key(
    *,
    user_id: object,
    strategy_id: object,
    symbol: str,
    side: str,
    now_unix: float | None = None,
) -> str:
    """Build the Redis SETNX key for the pre-submit duplicate guard.

    ``user_id`` / ``strategy_id`` are coerced via ``str(...)`` so
    UUID, str, and None all serialise predictably.

    Content-addressed on ``(user, strategy, symbol, side,
    minute_bucket)`` where ``minute_bucket = int(now_unix // 60)``.
    qty is deliberately NOT part of the key. This means:

    - A retry that recomputes a different qty for the same
      symbol/side in the same minute → same key → second SETNX
      returns False → blocked (the finding #19 goal).
    - The same signal firing twice in the same minute → same key →
      blocked (the cross-call duplicate guard restored).
    - A legitimate same-symbol/side scale-in within the same minute
      is ALSO deduped. This is an intentional, accepted tradeoff —
      the safer default is to suppress a possibly-duplicate
      real-money order rather than risk a double fill. A scale-in
      that genuinely needs to fire in the same minute can be split
      across minute boundaries or use a distinct strategy_id.

    The minute_bucket reuses ``_minute_bucket`` so callers can pass a
    deterministic ``now_unix`` in tests and the live path supplies
    the current wall-clock time.
    """
    return _DEDUP_KEY_FMT.format(
        user_id=str(user_id) if user_id is not None else "anon",
        strategy_id=(
            str(strategy_id) if strategy_id is not None
            else "no_strategy"
        ),
        symbol=symbol,
        side=side,
        minute_bucket=_minute_bucket(now_unix),
    )


def today_ist_iso(now: datetime | None = None) -> str:
    """Return today's date in IST as ``YYYY-MM-DD``.

    NSE freeze quantities are circular-aligned to the trading day,
    which the platform treats as the local IST calendar day. We
    pin the conversion to a +05:30 offset rather than relying on
    ``Asia/Kolkata`` so this works inside any container TZ.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return now.astimezone(_IST).date().isoformat()


def build_freeze_key(now: datetime | None = None) -> str:
    """Build the Redis hash key for the daily freeze-qty cache."""
    return _FREEZE_HASH_KEY_FMT.format(date_ist=today_ist_iso(now))


def build_tick_size_key(now: datetime | None = None) -> str:
    """Build the Redis hash key for the daily tick-size cache."""
    return _TICK_SIZE_HASH_KEY_FMT.format(date_ist=today_ist_iso(now))


def build_freeze_fallback_flag_key(
    *, symbol: str, now: datetime | None = None,
) -> str:
    """Build the per-(symbol, date) flag key used to throttle
    ``freeze_qty_fallback_applied`` events to one per day.
    """
    return _FREEZE_FALLBACK_FLAG_FMT.format(
        date_ist=today_ist_iso(now),
        symbol=symbol,
    )
