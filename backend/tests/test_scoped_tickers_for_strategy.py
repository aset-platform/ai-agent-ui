"""Tests for _scoped_tickers_for_strategy."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.insights_routes import (
    _scoped_tickers_for_strategy,
)


class _FakeUser:
    def __init__(self):
        self.user_id = str(uuid4())
        self.role = "pro"


@pytest.mark.asyncio
async def test_scope_watchlist_includes_algo_open():
    user = _FakeUser()
    with patch(
        "backend.insights_routes._scoped_tickers",
        AsyncMock(return_value=["A.NS"]),
    ), patch(
        "backend.algo.live.open_positions.open_algo_positions",
        AsyncMock(return_value={"B.NS"}),
    ):
        out = await _scoped_tickers_for_strategy(
            user, "watchlist",
        )
    assert "A.NS" in out
    assert "B.NS" in out


@pytest.mark.asyncio
async def test_scope_portfolio_does_not_inject():
    user = _FakeUser()
    with patch(
        "backend.insights_routes._scoped_tickers",
        AsyncMock(return_value=["X.NS"]),
    ), patch(
        "backend.algo.live.open_positions.open_algo_positions",
        AsyncMock(return_value={"NEVER_INJECT.NS"}),
    ) as algo_spy:
        out = await _scoped_tickers_for_strategy(
            user, "portfolio",
        )
    assert out == ["X.NS"]
    algo_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_scope_discovery_does_not_inject():
    user = _FakeUser()
    with patch(
        "backend.insights_routes._scoped_tickers",
        AsyncMock(return_value=["X.NS", "Y.NS"]),
    ), patch(
        "backend.algo.live.open_positions.open_algo_positions",
        AsyncMock(return_value={"NEVER_INJECT.NS"}),
    ) as algo_spy:
        out = await _scoped_tickers_for_strategy(
            user, "discovery",
        )
    assert sorted(out) == ["X.NS", "Y.NS"]
    algo_spy.assert_not_awaited()
