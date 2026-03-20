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
                "cache GET failed key=%s", key,
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
                "cache SET failed key=%s", key,
                exc_info=True,
            )

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


class _NoOpCache:
    """Stub used when ``REDIS_URL`` is empty."""

    def get(self, key: str) -> None:
        return None

    def set(
        self, key: str, value: str, ttl: int = 0,
    ) -> None:
        pass

    def invalidate(self, pattern: str) -> None:
        pass

    def invalidate_exact(self, *keys: str) -> None:
        pass

    def ping(self) -> bool:
        return False


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
            "CacheService: no REDIS_URL; "
            "using no-op stub.",
        )
        return _NoOpCache()
    return CacheService(redis_url)
