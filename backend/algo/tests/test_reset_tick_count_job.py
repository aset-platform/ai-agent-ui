"""Tests: algo_ws_tick_count_reset job — OBS-1.

Daily IST-midnight job that walks the WS registry and zeros
``tick_count_today`` on every active multiplexer.
"""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.algo.broker import ws_registry
from backend.algo.jobs.reset_tick_count import (
    run_reset_tick_count_job,
)


@pytest.fixture(autouse=True)
def _clear_registry():
    ws_registry._registry.clear()
    yield
    ws_registry._registry.clear()


@pytest.mark.asyncio
async def test_reset_iterates_all_muxes():
    """Every multiplexer in the registry has reset_tick_count called."""
    m1 = MagicMock()
    m1.tick_count_today = 42
    m2 = MagicMock()
    m2.tick_count_today = 7
    ws_registry._registry[uuid4()] = m1
    ws_registry._registry[uuid4()] = m2

    result = await run_reset_tick_count_job()

    m1.reset_tick_count.assert_called_once_with()
    m2.reset_tick_count.assert_called_once_with()
    assert result == {"reset_count": 2}


@pytest.mark.asyncio
async def test_reset_with_empty_registry():
    """Empty registry → no-op, count 0, doesn't raise."""
    result = await run_reset_tick_count_job()
    assert result == {"reset_count": 0}
