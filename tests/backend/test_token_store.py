"""Tests for auth.token_store — InMemory + Redis backends."""

import sys
import time
from unittest.mock import MagicMock, patch

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
            sys.modules, {"redis": mock_redis_mod},
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
            "test:jti-1", 600, "1",
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


# ---------------------------------------------------------------
# Factory
# ---------------------------------------------------------------


class TestCreateTokenStore:
    """Tests for the create_token_store factory."""

    def test_empty_url_returns_in_memory(self):
        store = create_token_store("")
        assert isinstance(store, InMemoryTokenStore)

    def test_redis_url_returns_redis_store(self):
        mock_redis_mod = MagicMock()
        mock_redis_mod.from_url.return_value = MagicMock()
        with patch.dict(
            sys.modules, {"redis": mock_redis_mod},
        ):
            store = create_token_store(
                "redis://localhost:6379/0",
            )
        assert isinstance(store, RedisTokenStore)

    def test_redis_connection_failure_falls_back(self):
        mock_redis_mod = MagicMock()
        mock_redis_mod.from_url.side_effect = (
            ConnectionError("refused")
        )
        with patch.dict(
            sys.modules, {"redis": mock_redis_mod},
        ):
            store = create_token_store(
                "redis://bad:6379/0",
            )
        assert isinstance(store, InMemoryTokenStore)
