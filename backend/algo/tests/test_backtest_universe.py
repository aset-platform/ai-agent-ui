"""Universe resolution from strategy.universe.scope."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from auth.models import UserContext
from backend.algo.backtest.universe import resolve_universe


def _make_strategy(scope: str):
    class _U:
        pass
    u = _U()
    u.scope = scope

    class _S:
        pass
    s = _S()
    s.universe = u
    return s


@pytest.mark.asyncio
async def test_resolve_universe_passes_scope_to_helper():
    user = UserContext(
        user_id="11111111-1111-1111-1111-111111111111",
        email="t@t", role="pro",
    )
    strategy = _make_strategy("watchlist")
    with patch(
        "backend.algo.backtest.universe._scoped_tickers",
        new=AsyncMock(return_value=["TCS.NS", "INFY.NS"]),
    ) as helper:
        out = await resolve_universe(user=user, strategy=strategy)
    helper.assert_awaited_once()
    assert helper.call_args.kwargs == {
        "user": user, "scope": "watchlist",
    }
    assert out == ["TCS.NS", "INFY.NS"]


@pytest.mark.asyncio
async def test_resolve_universe_unknown_scope_falls_back_to_watchlist():
    user = UserContext(
        user_id="22222222-2222-2222-2222-222222222222",
        email="t@t", role="pro",
    )
    strategy = _make_strategy("nonsense")
    with patch(
        "backend.algo.backtest.universe._scoped_tickers",
        new=AsyncMock(return_value=[]),
    ) as helper:
        await resolve_universe(user=user, strategy=strategy)
    assert helper.call_args.kwargs["scope"] == "watchlist"
