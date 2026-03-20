"""Tests for the Redis cache layer.

Exercises :class:`cache.CacheService` and
:class:`cache._NoOpCache` using ``fakeredis``.
"""

import logging

import fakeredis

from cache import (
    CacheService,
    TTL_STABLE,
    TTL_VOLATILE,
    _NoOpCache,
)


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _make_cache() -> CacheService:
    """Build a CacheService backed by fakeredis."""
    import redis as _redis_mod

    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(
        server=server,
        decode_responses=True,
    )
    svc = object.__new__(CacheService)
    svc._redis = _redis_mod
    svc._client = client
    return svc


# ---------------------------------------------------------------
# CacheService tests
# ---------------------------------------------------------------


class TestCacheService:
    """Core get/set/invalidate operations."""

    def test_get_set(self):
        """set then get returns the value."""
        cache = _make_cache()
        cache.set("k1", '{"a":1}')
        assert cache.get("k1") == '{"a":1}'

    def test_get_missing(self):
        """Non-existent key returns None."""
        cache = _make_cache()
        assert cache.get("no-such-key") is None

    def test_set_with_ttl(self):
        """setex is called with the correct TTL."""
        cache = _make_cache()
        cache.set("vol", "data", ttl=TTL_VOLATILE)
        ttl = cache._client.ttl("vol")
        assert 0 < ttl <= TTL_VOLATILE

        cache.set("stb", "data", ttl=TTL_STABLE)
        ttl2 = cache._client.ttl("stb")
        assert 0 < ttl2 <= TTL_STABLE

    def test_invalidate_pattern(self):
        """Glob-based invalidation deletes matches."""
        cache = _make_cache()
        cache.set("cache:dash:a", "1")
        cache.set("cache:dash:b", "2")
        cache.set("cache:other:c", "3")

        cache.invalidate("cache:dash:*")

        assert cache.get("cache:dash:a") is None
        assert cache.get("cache:dash:b") is None
        assert cache.get("cache:other:c") == "3"

    def test_invalidate_exact(self):
        """Exact-key deletion."""
        cache = _make_cache()
        cache.set("key1", "val")
        cache.invalidate_exact("key1")
        assert cache.get("key1") is None

    def test_ping(self):
        """Healthy client returns True."""
        cache = _make_cache()
        assert cache.ping() is True


# ---------------------------------------------------------------
# _NoOpCache tests
# ---------------------------------------------------------------


class TestNoOpCache:
    """Stub cache returns safe defaults."""

    def test_get_returns_none(self):
        """get always returns None."""
        noop = _NoOpCache()
        assert noop.get("anything") is None

    def test_set_is_noop(self):
        """set does not raise."""
        noop = _NoOpCache()
        noop.set("k", "v", ttl=60)
        assert noop.get("k") is None

    def test_ping_returns_false(self):
        """ping always returns False."""
        noop = _NoOpCache()
        assert noop.ping() is False


# ---------------------------------------------------------------
# Graceful Redis failure
# ---------------------------------------------------------------


class TestRedisFailureGraceful:
    """Broken Redis client logs warning, no crash."""

    def test_get_failure_returns_none(
        self, caplog,
    ):
        """GET on broken client returns None."""
        import redis as _redis_mod

        svc = object.__new__(CacheService)
        svc._redis = _redis_mod

        broken = fakeredis.FakeRedis(
            decode_responses=True,
        )
        broken.close()
        svc._client = broken

        with caplog.at_level(logging.WARNING):
            result = svc.get("any-key")

        assert result is None

    def test_set_failure_no_crash(self, caplog):
        """SET on broken client is silent."""
        import redis as _redis_mod

        svc = object.__new__(CacheService)
        svc._redis = _redis_mod

        broken = fakeredis.FakeRedis(
            decode_responses=True,
        )
        broken.close()
        svc._client = broken

        with caplog.at_level(logging.WARNING):
            svc.set("k", "v")  # must not raise
