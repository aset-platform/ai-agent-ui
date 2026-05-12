# backend/algo/broker/redis_keys.py
"""Centralised Redis-key builders for the order-safety hardening layer.

Two key families live here:

1. ``algo:placeorder:dedup:{user_id}:{strategy_id}:{symbol}:{side}:
   {qty}:{minute_bucket}`` — pre-submit duplicate guard (PR #4 §3.4).
   ``minute_bucket = floor(time.time() / 60)``. SETNX with 60s TTL.

2. ``kite:freeze:{date_ist}`` — once-per-day Redis hash of
   ``tradingsymbol -> freeze_qty`` (PR #4 §3.5). Used by
   ``freeze_cache.get_freeze_qty``.

Keeping the builders in one module makes it easy to grep for any
key shape, swap in fakes in tests, and verify there is exactly one
source of truth for the format strings.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta


_DEDUP_KEY_FMT = (
    "algo:placeorder:dedup:{user_id}:{strategy_id}:"
    "{symbol}:{side}:{qty}:{minute_bucket}"
)
_FREEZE_HASH_KEY_FMT = "kite:freeze:{date_ist}"
_FREEZE_FALLBACK_FLAG_FMT = (
    "kite:freeze:fallback:{date_ist}:{symbol}"
)

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
    qty: int,
    now_unix: float | None = None,
) -> str:
    """Build the Redis SETNX key for the pre-submit duplicate guard.

    ``user_id`` / ``strategy_id`` are coerced via ``str(...)`` so
    UUID, str, and None all serialise predictably. Same-minute
    repeats produce the SAME key; cross-minute repeats produce
    different keys (different ``minute_bucket``).
    """
    return _DEDUP_KEY_FMT.format(
        user_id=str(user_id) if user_id is not None else "anon",
        strategy_id=(
            str(strategy_id) if strategy_id is not None
            else "no_strategy"
        ),
        symbol=symbol,
        side=side,
        qty=int(qty),
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
