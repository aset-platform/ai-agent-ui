"""Phase-B unit tests for BYO routing + limit enforcement."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import backend.llm_byo as byo


class _AtomicFakeCache:
    """In-memory cache with a thread-safe ``incr`` /
    ``decr`` pair — the behaviour we need a real
    Redis pipeline to guarantee."""

    def __init__(self):
        self._store: dict[str, int] = {}
        self._lock = threading.Lock()

    def incr(self, key, by=1, ttl=None):
        with self._lock:
            v = self._store.get(key, 0) + by
            self._store[key] = v
            return v

    def decr(self, key, by=1):
        with self._lock:
            v = self._store.get(key, 0) - by
            self._store[key] = v
            return v

    # get/set kept so TestBYOContextVar fixtures still work
    def get(self, key):
        v = self._store.get(key)
        return str(v) if v is not None else None

    def set(self, key, value, ttl=None):
        self._store[key] = int(value)


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
        self, monkeypatch,
    ):
        import backend.llm_byo as mod
        cache = _AtomicFakeCache()
        monkeypatch.setattr(
            "cache.get_cache", lambda: cache,
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
        self, monkeypatch,
    ):
        import backend.llm_byo as mod
        cache = _AtomicFakeCache()
        # Pre-seed counter at limit
        cache._store[mod._month_key("u3")] = 100
        monkeypatch.setattr(
            "cache.get_cache", lambda: cache,
        )
        with pytest.raises(HTTPException) as exc:
            await mod._check_and_increment_byo_counter(
                "u3", 100,
            )
        assert exc.value.status_code == 429
        assert "limit reached" in exc.value.detail.lower()
        # Over-limit increment was rolled back:
        # counter still at the limit, not 101.
        assert cache._store[mod._month_key("u3")] == 100

    @pytest.mark.asyncio
    async def test_parallel_requests_never_exceed_limit(
        self, monkeypatch,
    ):
        """Regression for the GET/SET TOCTOU bug.

        Two asyncio tasks hammering the counter with
        the limit set to their combined headroom must
        never both succeed past the limit — the
        persisted value after the dust settles is
        exactly ``limit``, and exactly ``limit``
        requests returned success.
        """
        import backend.llm_byo as mod
        cache = _AtomicFakeCache()
        monkeypatch.setattr(
            "cache.get_cache", lambda: cache,
        )

        LIMIT = 50
        TOTAL_REQUESTS = 200  # 150 over-limit

        successes = 0
        rejections = 0
        lock = asyncio.Lock()

        async def one_request():
            nonlocal successes, rejections
            try:
                await mod._check_and_increment_byo_counter(
                    "race_user", LIMIT,
                )
                async with lock:
                    successes += 1
            except HTTPException as e:
                if e.status_code == 429:
                    async with lock:
                        rejections += 1
                else:
                    raise

        await asyncio.gather(*[
            one_request()
            for _ in range(TOTAL_REQUESTS)
        ])

        # Exactly LIMIT requests served, the rest 429
        assert successes == LIMIT, (
            f"Expected {LIMIT} successes, got "
            f"{successes} (rejections={rejections})"
        )
        assert rejections == TOTAL_REQUESTS - LIMIT
        # Persisted counter is bounded by the limit —
        # every rollback succeeded.
        assert (
            cache._store[mod._month_key("race_user")]
            == LIMIT
        )


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
