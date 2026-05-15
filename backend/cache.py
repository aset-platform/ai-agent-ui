"""Redis-backed read cache for dashboard and API endpoints.

Provides a thin ``CacheService`` that stores pre-serialised
JSON responses in Redis with per-key TTL.  Falls back to a
no-op in-memory stub when Redis is unavailable so that the
application never breaks because of a cache miss.

Usage::

    from cache import get_cache

    cache = get_cache()
    hit = cache.get("cache:dash:watchlist:u123")
    if hit is not None:
        return Response(content=hit, ...)
    # ... compute response ...
    cache.set("cache:dash:watchlist:u123", body, ttl=60)

Write-through invalidation::

    cache.invalidate("cache:dash:watchlist:*")
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

_logger = logging.getLogger(__name__)

# Default TTLs (seconds) grouped by volatility.
TTL_HERO = 10  # /dashboard/home aggregate — keep tight
TTL_VOLATILE = 60  # watchlist, llm-usage
TTL_STABLE = 300  # charts, insights, registry
TTL_ADMIN = 30  # tier-health, metrics


class CacheService:
    """Shared read-cache backed by Redis.

    All public methods are resilient — a Redis failure logs
    a warning and returns ``None`` / no-op so that callers
    always fall through to the authoritative Iceberg read.
    """

    def __init__(self, redis_url: str) -> None:
        import redis as _redis_mod

        self._redis = _redis_mod
        from auth.token_store import get_redis_client

        self._client = get_redis_client(redis_url)
        if self._client is None:
            _logger.warning(
                "CacheService: Redis unavailable; "
                "operating in pass-through mode.",
            )

    # ----------------------------------------------------------
    # Read
    # ----------------------------------------------------------

    def get(self, key: str) -> str | None:
        """Return cached JSON string or ``None``."""
        if self._client is None:
            return None
        try:
            return self._client.get(key)
        except self._redis.RedisError:
            _logger.warning(
                "cache GET failed key=%s",
                key,
                exc_info=True,
            )
            return None

    # ----------------------------------------------------------
    # Write
    # ----------------------------------------------------------

    def set(
        self,
        key: str,
        value: str,
        ttl: int = TTL_STABLE,
    ) -> None:
        """Store *value* with TTL (seconds)."""
        if self._client is None:
            return
        try:
            self._client.setex(key, ttl, value)
        except self._redis.RedisError:
            _logger.warning(
                "cache SET failed key=%s",
                key,
                exc_info=True,
            )

    # ----------------------------------------------------------
    # Atomic counters
    # ----------------------------------------------------------

    def incr(
        self,
        key: str,
        by: int = 1,
        ttl: int | None = None,
    ) -> int | None:
        """Atomically increment *key* by *by* and
        return the new value.

        Uses a Redis pipeline so ``INCRBY`` and the
        optional ``EXPIRE`` execute as one round-trip
        — safe against the GET/SET race that a
        read-check-write pattern exhibits under
        concurrent access from the same user.

        Returns ``None`` when the Redis client is
        unavailable (caller should treat as "cache
        down, fall through to source of truth").
        """
        if self._client is None:
            return None
        try:
            pipe = self._client.pipeline()
            pipe.incrby(key, by)
            if ttl is not None:
                pipe.expire(key, ttl)
            results = pipe.execute()
            new_val = results[0]
            return int(new_val) if new_val is not None else None
        except self._redis.RedisError:
            _logger.warning(
                "cache INCR failed key=%s",
                key,
                exc_info=True,
            )
            return None

    def decr(
        self,
        key: str,
        by: int = 1,
    ) -> int | None:
        """Atomically decrement *key* by *by*.

        Pairs with :meth:`incr` for rollback on
        conditional increments (over-limit detected
        post-increment).
        """
        if self._client is None:
            return None
        try:
            return int(self._client.decrby(key, by))
        except self._redis.RedisError:
            _logger.warning(
                "cache DECR failed key=%s",
                key,
                exc_info=True,
            )
            return None

    # ----------------------------------------------------------
    # Invalidation
    # ----------------------------------------------------------

    def invalidate(self, pattern: str) -> None:
        """Delete all keys matching *pattern*.

        Uses ``SCAN`` + ``DELETE`` to avoid blocking the
        Redis event loop (no ``KEYS`` command).

        Args:
            pattern: Glob pattern, e.g.
                ``"cache:dash:watchlist:*"``.
        """
        if self._client is None:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = self._client.scan(
                    cursor=cursor,
                    match=pattern,
                    count=200,
                )
                if keys:
                    self._client.delete(*keys)
                if cursor == 0:
                    break
        except self._redis.RedisError:
            _logger.warning(
                "cache invalidate failed pattern=%s",
                pattern,
                exc_info=True,
            )

    def invalidate_exact(self, *keys: str) -> None:
        """Delete one or more exact keys."""
        if self._client is None or not keys:
            return
        try:
            self._client.delete(*keys)
        except self._redis.RedisError:
            _logger.warning(
                "cache delete failed keys=%s",
                keys,
                exc_info=True,
            )

    def ping(self) -> bool:
        """Return ``True`` if Redis is reachable."""
        if self._client is None:
            return False
        try:
            return self._client.ping()
        except self._redis.RedisError:
            return False

    # ----------------------------------------------------------
    # LIST ops (ASETPLTFRM-417 / FE-5.1)
    #
    # Used by the live-mode trade_feature_snapshots buffer:
    # one Redis LIST per ``(user_id, trading_date_ist)``,
    # drained at 15:30 IST by the EOD flush job into ONE
    # Iceberg commit per user. Every method is resilient — a
    # Redis failure logs a warning and returns a sentinel so
    # the live fill path NEVER blocks on snapshot bookkeeping.
    # ----------------------------------------------------------

    def rpush(self, key: str, value: str) -> int | None:
        """Append ``value`` to the right of LIST ``key``.

        Returns the new list length (or ``None`` if Redis is
        unavailable / errored — caller treats as "snapshot
        lost; fill ledger remains source of truth").
        """
        if self._client is None:
            return None
        try:
            return int(self._client.rpush(key, value))
        except self._redis.RedisError:
            _logger.warning(
                "cache RPUSH failed key=%s",
                key,
                exc_info=True,
            )
            return None

    def lrange(
        self,
        key: str,
        start: int = 0,
        stop: int = -1,
    ) -> list[str]:
        """Return LIST elements in ``[start, stop]`` (inclusive,
        Redis semantics). Empty list on missing key or error."""
        if self._client is None:
            return []
        try:
            raw = self._client.lrange(key, start, stop)
            return [
                v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
                for v in raw
            ]
        except self._redis.RedisError:
            _logger.warning(
                "cache LRANGE failed key=%s",
                key,
                exc_info=True,
            )
            return []

    def delete(self, *keys: str) -> int:
        """Delete one or more exact keys. Returns the count of
        keys actually removed (Redis semantics). Returns 0 if
        Redis is unavailable."""
        if self._client is None or not keys:
            return 0
        try:
            return int(self._client.delete(*keys))
        except self._redis.RedisError:
            _logger.warning(
                "cache DELETE failed keys=%s",
                keys,
                exc_info=True,
            )
            return 0

    def expire(self, key: str, ttl: int) -> bool:
        """Set TTL (seconds) on ``key``. Returns True iff TTL
        was applied (key existed)."""
        if self._client is None:
            return False
        try:
            return bool(self._client.expire(key, ttl))
        except self._redis.RedisError:
            _logger.warning(
                "cache EXPIRE failed key=%s",
                key,
                exc_info=True,
            )
            return False

    def scan_keys(self, pattern: str) -> list[str]:
        """Return every key matching ``pattern`` via ``SCAN``
        (non-blocking). Empty list on Redis outage."""
        if self._client is None:
            return []
        out: list[str] = []
        try:
            cursor = 0
            while True:
                cursor, keys = self._client.scan(
                    cursor=cursor,
                    match=pattern,
                    count=200,
                )
                for k in keys:
                    out.append(
                        k.decode()
                        if isinstance(
                            k,
                            (bytes, bytearray),
                        )
                        else str(k)
                    )
                if cursor == 0:
                    break
        except self._redis.RedisError:
            _logger.warning(
                "cache SCAN failed pattern=%s",
                pattern,
                exc_info=True,
            )
        return out


class _NoOpCache:
    """Stub used when ``REDIS_URL`` is empty."""

    def get(self, key: str) -> None:
        return None

    def set(
        self,
        key: str,
        value: str,
        ttl: int = 0,
    ) -> None:
        pass

    def incr(
        self,
        key: str,
        by: int = 1,
        ttl: int | None = None,
    ) -> None:
        return None

    def decr(
        self,
        key: str,
        by: int = 1,
    ) -> None:
        return None

    def invalidate(self, pattern: str) -> None:
        pass

    def invalidate_exact(self, *keys: str) -> None:
        pass

    def ping(self) -> bool:
        return False

    # FE-5.1 LIST ops — no-op stubs matching CacheService API.
    def rpush(self, key: str, value: str) -> None:
        return None

    def lrange(
        self,
        key: str,
        start: int = 0,
        stop: int = -1,
    ) -> list[str]:
        return []

    def delete(self, *keys: str) -> int:
        return 0

    def expire(self, key: str, ttl: int) -> bool:
        return False

    def scan_keys(self, pattern: str) -> list[str]:
        return []


@lru_cache(maxsize=1)
def get_cache() -> CacheService | _NoOpCache:
    """Return the process-wide cache singleton.

    Reads ``REDIS_URL`` from the environment (set by
    ``backend/main.py`` at startup).  Returns a no-op
    stub when Redis is not configured.
    """
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        _logger.info(
            "CacheService: no REDIS_URL; " "using no-op stub.",
        )
        return _NoOpCache()
    return CacheService(redis_url)
