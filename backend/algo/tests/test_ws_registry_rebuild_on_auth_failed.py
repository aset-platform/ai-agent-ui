"""Tests: get_or_create_multiplexer rebuilds a stale multiplexer.

Regression: a daily Kite token expiry set
``_auth_failed`` on the cached multiplexer (PR #250's non-retryable
403 halt). ``get_or_create_multiplexer`` only checked ``_closed``,
so every subsequent Zerodha re-login returned the halted instance —
which holds the stale access token and never reconnects — leaving
live LTPs (homepage scorecards + watchlist) dead until a backend
restart. The fix rebuilds when the cached mux is closed OR
auth-failed, using the fresh token from the re-login.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from backend.algo.broker import ws_registry
from backend.algo.tests.fixtures.mock_kite_ws_server import (
    patch_multiplexer_ticker,
)


@pytest.fixture(autouse=True)
def _clear_registry():
    ws_registry._registry.clear()
    yield
    ws_registry._registry.clear()


@pytest.mark.asyncio
async def test_reuses_healthy_multiplexer():
    """A healthy cached mux is returned as-is (no rebuild)."""
    uid = uuid4()
    async with patch_multiplexer_ticker():
        first = await ws_registry.get_or_create_multiplexer(
            user_id=uid, api_key="k", access_token="tok1",
        )
        try:
            second = await ws_registry.get_or_create_multiplexer(
                user_id=uid, api_key="k", access_token="tok2",
            )
            assert second is first
        finally:
            await ws_registry.teardown_user(uid)


@pytest.mark.asyncio
async def test_rebuilds_when_auth_failed():
    """An auth-failed mux is torn down and rebuilt with the new
    token — the core fix."""
    uid = uuid4()
    async with patch_multiplexer_ticker():
        stale = await ws_registry.get_or_create_multiplexer(
            user_id=uid, api_key="k", access_token="old_tok",
        )
        # Simulate the non-retryable 403 halt (overnight expiry).
        stale._auth_failed = True

        fresh = await ws_registry.get_or_create_multiplexer(
            user_id=uid, api_key="k", access_token="new_tok",
        )
        try:
            assert fresh is not stale
            assert fresh._access_token == "new_tok"
            assert not fresh.auth_failed
            assert stale._closed  # old instance torn down
            assert ws_registry._registry[uid] is fresh
        finally:
            await ws_registry.teardown_user(uid)


@pytest.mark.asyncio
async def test_rebuilds_when_closed():
    """A closed mux is also rebuilt (pre-existing contract kept)."""
    uid = uuid4()
    async with patch_multiplexer_ticker():
        stale = await ws_registry.get_or_create_multiplexer(
            user_id=uid, api_key="k", access_token="old_tok",
        )
        await stale.close()

        fresh = await ws_registry.get_or_create_multiplexer(
            user_id=uid, api_key="k", access_token="new_tok",
        )
        try:
            assert fresh is not stale
            assert fresh._access_token == "new_tok"
        finally:
            await ws_registry.teardown_user(uid)
