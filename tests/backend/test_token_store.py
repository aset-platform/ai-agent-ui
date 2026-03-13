"""Tests for auth.token_store — InMemory + Redis backends."""

import sys
import time
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from auth.token_store import (
    InMemoryTokenStore,
    RedisTokenStore,
    create_token_store,
)

# ---------------------------------------------------------------
# InMemoryTokenStore
# ---------------------------------------------------------------


class TestInMemoryTokenStore:
    """Unit tests for the in-memory backend."""

    def test_add_and_contains(self):
        store = InMemoryTokenStore()
        store.add("jti-1", ttl_seconds=300)
        assert store.contains("jti-1")

    def test_contains_returns_false_for_unknown(self):
        store = InMemoryTokenStore()
        assert not store.contains("nope")

    def test_remove_deletes_key(self):
        store = InMemoryTokenStore()
        store.add("jti-2", ttl_seconds=300)
        store.remove("jti-2")
        assert not store.contains("jti-2")

    def test_expired_key_not_found(self):
        store = InMemoryTokenStore()
        store.add("jti-3", ttl_seconds=1)
        # Simulate passage of time.
        store._store["jti-3"] = time.time() - 1
        assert not store.contains("jti-3")

    def test_prune_removes_expired(self):
        store = InMemoryTokenStore()
        store._store["old"] = time.time() - 10
        store.add("new", ttl_seconds=300)
        assert "old" not in store._store
        assert store.contains("new")

    def test_remove_missing_key_is_noop(self):
        store = InMemoryTokenStore()
        store.remove("nonexistent")  # no exception

    def test_ping_returns_true(self):
        store = InMemoryTokenStore()
        assert store.ping() is True


# ---------------------------------------------------------------
# RedisTokenStore (mocked)
# ---------------------------------------------------------------


class TestRedisTokenStore:
    """Unit tests for the Redis backend with mocked client."""

    def _make_store(self):
        """Create a RedisTokenStore with a mocked redis."""
        mock_client = MagicMock()
        mock_redis_mod = MagicMock()
        mock_redis_mod.from_url.return_value = mock_client

        with patch.dict(
            sys.modules,
            {"redis": mock_redis_mod},
        ):
            store = RedisTokenStore(
                "redis://localhost:6379/0",
                prefix="test:",
            )
        return store, mock_client

    def test_add_calls_setex(self):
        store, client = self._make_store()
        store.add("jti-1", 600)
        client.setex.assert_called_once_with(
            "test:jti-1",
            600,
            "1",
        )

    def test_contains_calls_exists(self):
        store, client = self._make_store()
        client.exists.return_value = 1
        assert store.contains("jti-1")
        client.exists.assert_called_once_with("test:jti-1")

    def test_remove_calls_delete(self):
        store, client = self._make_store()
        store.remove("jti-1")
        client.delete.assert_called_once_with("test:jti-1")

    def test_ping_delegates_to_client(self):
        store, client = self._make_store()
        client.ping.return_value = True
        assert store.ping() is True

    def test_ping_returns_false_on_error(self):
        store, client = self._make_store()
        import redis as _r

        # Wire real redis module so except clause works.
        store._redis = _r
        client.ping.side_effect = _r.RedisError("down")
        assert store.ping() is False


# ---------------------------------------------------------------
# RedisTokenStore — operation resilience (mocked)
# ---------------------------------------------------------------


class TestRedisResilience:
    """Verify graceful degradation when Redis is down."""

    def _make_store(self):
        mock_client = MagicMock()
        mock_redis_mod = MagicMock()
        mock_redis_mod.from_url.return_value = mock_client
        # Wire RedisError so except clauses match.
        import redis as _r

        mock_redis_mod.RedisError = _r.RedisError

        with patch.dict(
            sys.modules,
            {"redis": mock_redis_mod},
        ):
            store = RedisTokenStore(
                "redis://localhost:6379/0",
                prefix="test:",
            )
        # Override _redis to real module for error class.
        store._redis = _r
        return store, mock_client

    def test_add_logs_on_failure(self):
        store, client = self._make_store()
        import redis as _r

        client.setex.side_effect = _r.RedisError("boom")
        store.add("jti-x", 300)  # no exception

    def test_contains_returns_false_on_failure(self):
        store, client = self._make_store()
        import redis as _r

        client.exists.side_effect = _r.RedisError("boom")
        assert store.contains("jti-x") is False

    def test_remove_logs_on_failure(self):
        store, client = self._make_store()
        import redis as _r

        client.delete.side_effect = _r.RedisError("boom")
        store.remove("jti-x")  # no exception


# ---------------------------------------------------------------
# Integration tests (fakeredis)
# ---------------------------------------------------------------


class TestRedisIntegration:
    """End-to-end token store tests against fakeredis."""

    @pytest.fixture()
    def store(self):
        """Create a RedisTokenStore backed by fakeredis."""
        server = fakeredis.FakeServer()
        client = fakeredis.FakeRedis(
            server=server,
            decode_responses=True,
        )
        s = RedisTokenStore.__new__(RedisTokenStore)
        import redis as _r

        s._redis = _r
        s._client = client
        s._prefix = "test:"
        return s

    def test_add_and_contains(self, store):
        store.add("jti-1", 300)
        assert store.contains("jti-1")

    def test_contains_missing(self, store):
        assert not store.contains("nope")

    def test_remove(self, store):
        store.add("jti-2", 300)
        store.remove("jti-2")
        assert not store.contains("jti-2")

    def test_ttl_is_set(self, store):
        store.add("jti-3", 120)
        ttl = store._client.ttl("test:jti-3")
        assert 0 < ttl <= 120

    def test_ping(self, store):
        assert store.ping() is True

    def test_multiple_keys_independent(self, store):
        store.add("a", 300)
        store.add("b", 300)
        store.remove("a")
        assert not store.contains("a")
        assert store.contains("b")

    def test_overwrite_resets_ttl(self, store):
        store.add("k", 60)
        store.add("k", 600)
        ttl = store._client.ttl("test:k")
        assert ttl > 60


# ---------------------------------------------------------------
# Factory
# ---------------------------------------------------------------


# ---------------------------------------------------------------
# AuthService.store_health()
# ---------------------------------------------------------------


# ---------------------------------------------------------------
# Shared Redis client
# ---------------------------------------------------------------


class TestSharedRedisClient:
    """Verify create_token_store shares a single connection."""

    def test_two_stores_share_client(self):
        """Two stores from same URL reuse one Redis client."""
        mock_client = MagicMock()
        mock_redis_mod = MagicMock()
        mock_redis_mod.from_url.return_value = mock_client
        mock_client.ping.return_value = True

        from auth.token_store import get_redis_client

        get_redis_client.cache_clear()

        with patch.dict(
            sys.modules,
            {"redis": mock_redis_mod},
        ):
            deny = create_token_store(
                "redis://localhost:6379/0",
                prefix="auth:deny:",
            )
            oauth = create_token_store(
                "redis://localhost:6379/0",
                prefix="auth:oauth_state:",
            )

        get_redis_client.cache_clear()

        # Both stores reference the same underlying client.
        assert deny._client is oauth._client
        # from_url called only once (shared pool).
        mock_redis_mod.from_url.assert_called_once()

    def test_different_prefixes_isolated(self):
        """Shared client still isolates keys by prefix."""
        server = fakeredis.FakeServer()
        client = fakeredis.FakeRedis(
            server=server,
            decode_responses=True,
        )

        deny = RedisTokenStore.__new__(RedisTokenStore)
        import redis as _r

        deny._redis = _r
        deny._client = client
        deny._prefix = "auth:deny:"

        oauth = RedisTokenStore.__new__(RedisTokenStore)
        oauth._redis = _r
        oauth._client = client
        oauth._prefix = "auth:oauth_state:"

        deny.add("jti-1", 300)
        oauth.add("state-1", 300)

        assert deny.contains("jti-1")
        assert not deny.contains("state-1")
        assert oauth.contains("state-1")
        assert not oauth.contains("jti-1")


class TestStoreHealth:
    """Unit tests for the public store_health() method."""

    def test_returns_backend_and_ok(self):
        from auth.service import AuthService

        svc = AuthService(
            secret_key="x" * 32,
            token_store=InMemoryTokenStore(),
        )
        result = svc.store_health()
        assert result == {
            "backend": "InMemoryTokenStore",
            "ok": True,
        }

    def test_degraded_when_ping_fails(self):
        from auth.service import AuthService

        store = InMemoryTokenStore()
        store.ping = lambda: False  # type: ignore[assignment]
        svc = AuthService(
            secret_key="x" * 32,
            token_store=store,
        )
        result = svc.store_health()
        assert result["ok"] is False
        assert result["backend"] == "InMemoryTokenStore"


class TestCreateTokenStore:
    """Tests for the create_token_store factory."""

    def test_empty_url_returns_in_memory(self):
        store = create_token_store("")
        assert isinstance(store, InMemoryTokenStore)

    def test_redis_url_returns_redis_store(self):
        mock_redis_mod = MagicMock()
        mock_redis_mod.from_url.return_value = MagicMock()
        with patch.dict(
            sys.modules,
            {"redis": mock_redis_mod},
        ):
            store = create_token_store(
                "redis://localhost:6379/0",
            )
        assert isinstance(store, RedisTokenStore)

    def test_redis_connection_failure_falls_back(self):
        mock_redis_mod = MagicMock()
        mock_redis_mod.from_url.side_effect = ConnectionError("refused")
        with patch.dict(
            sys.modules,
            {"redis": mock_redis_mod},
        ):
            store = create_token_store(
                "redis://bad:6379/0",
            )
        assert isinstance(store, InMemoryTokenStore)
