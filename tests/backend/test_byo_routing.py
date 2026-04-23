"""Phase-B unit tests for BYO routing + limit enforcement."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import backend.llm_byo as byo


@pytest.fixture
def _fake_cache(monkeypatch):
    """In-memory stand-in for the Redis cache singleton."""

    store: dict[str, bytes] = {}

    class _FakeCache:
        def get(self, key):
            v = store.get(key)
            return v.decode() if isinstance(v, bytes) else v

        def set(self, key, value, ttl=None):
            if isinstance(value, str):
                value = value.encode()
            store[key] = value

    cache = _FakeCache()
    monkeypatch.setattr(
        "backend.cache.get_cache",
        lambda: cache,
        raising=False,
    )
    return store


class TestBYOContextVar:
    def test_get_returns_none_by_default(self):
        assert byo.get_active_byo_context() is None

    def test_apply_and_auto_clear(self):
        ctx = byo.BYOContext(
            user_id="u1", groq_key="gsk_x", anthropic_key=None,
        )
        with byo.apply_byo_context(ctx):
            assert byo.get_active_byo_context() is ctx
        assert byo.get_active_byo_context() is None

    def test_apply_none_is_noop(self):
        with byo.apply_byo_context(None):
            assert byo.get_active_byo_context() is None
        assert byo.get_active_byo_context() is None


class TestMonthlyCounter:
    @pytest.mark.asyncio
    async def test_increments_and_returns_new_count(
        self, _fake_cache, monkeypatch,
    ):
        # override cache module lookup inside the helper
        import backend.llm_byo as mod
        fake = MagicMock()
        store = {}

        def _get(k):
            return store.get(k)

        def _set(k, v, ttl=None):
            store[k] = v if isinstance(v, bytes) else v.encode()

        fake.get.side_effect = _get
        fake.set.side_effect = _set

        monkeypatch.setattr(
            "cache.get_cache", lambda: fake,
        )
        n1 = await mod._check_and_increment_byo_counter(
            "u2", 100,
        )
        n2 = await mod._check_and_increment_byo_counter(
            "u2", 100,
        )
        assert n1 == 1
        assert n2 == 2

    @pytest.mark.asyncio
    async def test_raises_429_at_limit(
        self, _fake_cache, monkeypatch,
    ):
        import backend.llm_byo as mod
        fake = MagicMock()
        fake.get.return_value = "100"

        def _set(k, v, ttl=None):
            pass
        fake.set.side_effect = _set

        monkeypatch.setattr(
            "cache.get_cache", lambda: fake,
        )
        with pytest.raises(HTTPException) as exc:
            await mod._check_and_increment_byo_counter(
                "u3", 100,
            )
        assert exc.value.status_code == 429
        assert "limit reached" in exc.value.detail.lower()


class TestResolveByoForChat:
    @pytest.mark.asyncio
    async def test_superuser_never_routes_through_byo(self):
        got = await byo.resolve_byo_for_chat(
            user_id="u",
            role="superuser",
            chat_request_count=50,
            byo_monthly_limit=100,
        )
        assert got is None

    @pytest.mark.asyncio
    async def test_below_threshold_returns_none(self):
        got = await byo.resolve_byo_for_chat(
            user_id="u",
            role="pro",
            chat_request_count=5,
            byo_monthly_limit=100,
        )
        assert got is None

    @pytest.mark.asyncio
    async def test_above_threshold_no_keys_raises_429(
        self, monkeypatch,
    ):
        """Past the free allowance with no configured key
        must block the chat with 429.
        """
        @asynccontextmanager
        async def _sf():
            yield MagicMock()

        async def _fake_get(session, uid, provider):
            return None

        monkeypatch.setattr(
            "auth.repo.byo_repo.get_decrypted_key",
            _fake_get,
        )
        monkeypatch.setattr(
            "backend.db.engine.get_session_factory",
            lambda: _sf,
        )
        with pytest.raises(HTTPException) as exc:
            await byo.resolve_byo_for_chat(
                user_id="u",
                role="pro",
                chat_request_count=11,
                byo_monthly_limit=100,
            )
        assert exc.value.status_code == 429
