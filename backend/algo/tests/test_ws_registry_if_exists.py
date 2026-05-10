"""Tests: ws_registry.get_multiplexer_if_exists — OBS-1.

Sibling of ``get_or_create_multiplexer`` — a non-creating lookup
used by the read-only WS health endpoint so that polling the
endpoint never spins up a Kite WS connection.
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
    """Ensure each test starts with an empty process-local registry."""
    ws_registry._registry.clear()
    yield
    ws_registry._registry.clear()


def test_returns_none_when_absent():
    assert ws_registry.get_multiplexer_if_exists(uuid4()) is None


@pytest.mark.asyncio
async def test_returns_existing_when_present():
    uid = uuid4()
    async with patch_multiplexer_ticker():
        created = await ws_registry.get_or_create_multiplexer(
            user_id=uid,
            api_key="k",
            access_token="tok",
        )
        try:
            looked_up = ws_registry.get_multiplexer_if_exists(uid)
            assert looked_up is created
        finally:
            await ws_registry.teardown_user(uid)


def test_does_not_create_on_miss():
    """Calling the helper for an unknown user must not allocate."""
    assert len(ws_registry._registry) == 0
    result = ws_registry.get_multiplexer_if_exists(uuid4())
    assert result is None
    assert len(ws_registry._registry) == 0
