"""Pluggable token-store backends for JWT deny-list and OAuth state.

Provides a :class:`TokenStore` protocol with two implementations:

- :class:`InMemoryTokenStore` — dict-based; suitable for
  single-instance development.
- :class:`RedisTokenStore` — Redis-backed; suitable for multi-process
  production deployments.

Factory
-------
:func:`create_token_store` inspects the ``redis_url`` setting and
returns the appropriate implementation.
"""

import logging
import time
from typing import Protocol

_logger = logging.getLogger(__name__)


class TokenStore(Protocol):
    """Minimal key-value store with TTL support."""

    def add(self, key: str, ttl_seconds: int) -> None:
        """Store *key* with a time-to-live.

        Args:
            key: Opaque string (JTI, OAuth state, etc.).
            ttl_seconds: Seconds until automatic expiry.
        """
        ...  # pragma: no cover

    def contains(self, key: str) -> bool:
        """Return ``True`` if *key* exists and has not expired.

        Args:
            key: The key to look up.
        """
        ...  # pragma: no cover

    def remove(self, key: str) -> None:
        """Delete *key* if it exists.

        Args:
            key: The key to remove.
        """
        ...  # pragma: no cover

    def ping(self) -> bool:
        """Return ``True`` if the backend is reachable."""
        ...  # pragma: no cover


# ---------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------


class InMemoryTokenStore:
    """Dict-backed token store with lazy TTL expiry.

    Suitable for single-process development; state is lost on
    restart.

    Attributes:
        _store: Mapping of key → expiry timestamp (epoch seconds).
    """

    def __init__(self) -> None:
        self._store: dict[str, float] = {}

    def add(self, key: str, ttl_seconds: int) -> None:
        """Store *key* with a time-to-live.

        Args:
            key: Opaque string.
            ttl_seconds: Seconds until expiry.
        """
        self._store[key] = time.time() + ttl_seconds
        self._prune()

    def contains(self, key: str) -> bool:
        """Return ``True`` if *key* exists and has not expired.

        Args:
            key: The key to look up.
        """
        expires = self._store.get(key)
        if expires is None:
            return False
        if time.time() > expires:
            del self._store[key]
            return False
        return True

    def remove(self, key: str) -> None:
        """Delete *key* if it exists.

        Args:
            key: The key to remove.
        """
        self._store.pop(key, None)

    def ping(self) -> bool:
        """Return ``True`` — in-memory store is always available."""
        return True

    def _prune(self) -> None:
        """Remove entries whose TTL has elapsed."""
        now = time.time()
        expired = [k for k, v in self._store.items() if v < now]
        for k in expired:
            del self._store[k]


# ---------------------------------------------------------------
# Redis implementation
# ---------------------------------------------------------------


class RedisTokenStore:
    """Redis-backed token store with operation-level resilience.

    Uses ``SETEX`` for automatic TTL expiry.  All operations are
    synchronous (blocking) to match the rest of the auth stack.

    If Redis becomes unreachable after initial connection, each
    operation degrades gracefully:

    - ``add`` — logs a warning (revoked token may remain usable
      until its JWT ``exp`` claim expires naturally).
    - ``contains`` — returns ``False`` (fail-open; a revoked token
      may be accepted temporarily).
    - ``remove`` — logs a warning (key will expire via TTL).

    Attributes:
        _client: A ``redis.Redis`` instance.
        _prefix: Key prefix to namespace entries.
    """

    def __init__(
        self,
        redis_url: str,
        prefix: str = "auth:deny:",
    ) -> None:
        """Connect to Redis.

        Args:
            redis_url: Redis connection URL
                (e.g. ``redis://localhost:6379/0``).
            prefix: Key prefix for namespacing.
        """
        import redis as _redis_mod

        self._redis = _redis_mod
        self._client = _redis_mod.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=2,
            retry_on_timeout=True,
        )
        self._prefix = prefix
        _logger.info(
            "RedisTokenStore connected: url=%s prefix=%s",
            redis_url,
            prefix,
        )

    def add(self, key: str, ttl_seconds: int) -> None:
        """Store *key* in Redis with TTL.

        Args:
            key: Opaque string.
            ttl_seconds: Seconds until expiry.
        """
        try:
            self._client.setex(
                f"{self._prefix}{key}",
                ttl_seconds,
                "1",
            )
        except self._redis.RedisError:
            _logger.warning(
                "Redis write failed for key=%s; token"
                " revocation may be delayed.",
                key,
                exc_info=True,
            )

    def contains(self, key: str) -> bool:
        """Return ``True`` if *key* exists in Redis.

        Returns ``False`` on connection failure (fail-open).

        Args:
            key: The key to look up.
        """
        try:
            return bool(
                self._client.exists(
                    f"{self._prefix}{key}",
                )
            )
        except self._redis.RedisError:
            _logger.warning(
                "Redis read failed for key=%s; treating"
                " as not-found (fail-open).",
                key,
                exc_info=True,
            )
            return False

    def remove(self, key: str) -> None:
        """Delete *key* from Redis.

        Args:
            key: The key to remove.
        """
        try:
            self._client.delete(f"{self._prefix}{key}")
        except self._redis.RedisError:
            _logger.warning(
                "Redis delete failed for key=%s;"
                " entry will expire via TTL.",
                key,
                exc_info=True,
            )

    def ping(self) -> bool:
        """Return ``True`` if Redis responds to PING."""
        try:
            return bool(self._client.ping())
        except self._redis.RedisError:
            return False


# ---------------------------------------------------------------
# Factory
# ---------------------------------------------------------------


def create_token_store(
    redis_url: str = "",
    prefix: str = "auth:deny:",
) -> TokenStore:
    """Create the appropriate token store backend.

    If *redis_url* is non-empty, returns a :class:`RedisTokenStore`;
    otherwise returns an :class:`InMemoryTokenStore`.

    Args:
        redis_url: Redis connection URL (empty = in-memory).
        prefix: Key prefix for Redis keys.

    Returns:
        A :class:`TokenStore`-compatible instance.
    """
    if redis_url:
        try:
            store = RedisTokenStore(redis_url, prefix)
            _logger.info("Using Redis token store.")
            return store
        except Exception:
            _logger.warning(
                "Redis connection failed; falling back to"
                " in-memory token store.",
                exc_info=True,
            )
    _logger.info("Using in-memory token store.")
    return InMemoryTokenStore()
