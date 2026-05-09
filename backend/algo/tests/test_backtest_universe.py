"""Universe resolution from strategy.universe.{scope,filter}."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from auth.models import UserContext
from backend.algo.backtest.universe import resolve_universe


def _make_strategy(
    scope: str,
    *,
    market: str | None = None,
    ticker_types: list[str] | None = None,
):
    class _U:
        pass
    u = _U()
    u.scope = scope

    if market is not None or ticker_types is not None:
        class _F:
            pass
        f = _F()
        f.market = market
        f.ticker_type = ticker_types
        u.filter = f
    else:
        u.filter = None

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


@pytest.mark.asyncio
async def test_filter_market_india_drops_us_tickers():
    user = UserContext(
        user_id="33333333-3333-3333-3333-333333333333",
        email="t@t", role="pro",
    )
    strategy = _make_strategy(
        "discovery", market="india", ticker_types=["stock"],
    )
    candidates = ["TCS.NS", "INFY.NS", "AAPL", "RELIANCE.BO"]
    fake_registry = {
        "TCS.NS": {"ticker_type": "stock"},
        "INFY.NS": {"ticker_type": "stock"},
        "AAPL": {"ticker_type": "stock"},
        "RELIANCE.BO": {"ticker_type": "stock"},
    }
    with patch(
        "backend.algo.backtest.universe._scoped_tickers",
        new=AsyncMock(return_value=candidates),
    ), patch(
        "backend.algo.backtest.universe._registry_meta",
        return_value=fake_registry,
    ):
        out = await resolve_universe(user=user, strategy=strategy)
    # AAPL is US — dropped. Indian (.NS, .BO) kept.
    assert "AAPL" not in out
    assert "TCS.NS" in out
    assert "INFY.NS" in out
    assert "RELIANCE.BO" in out


@pytest.mark.asyncio
async def test_filter_ticker_type_stock_drops_etfs():
    user = UserContext(
        user_id="44444444-4444-4444-4444-444444444444",
        email="t@t", role="pro",
    )
    strategy = _make_strategy(
        "discovery", market="india", ticker_types=["stock"],
    )
    candidates = ["TCS.NS", "NIFTYBEES.NS"]
    fake_registry = {
        "TCS.NS": {"ticker_type": "stock"},
        "NIFTYBEES.NS": {"ticker_type": "etf"},
    }
    with patch(
        "backend.algo.backtest.universe._scoped_tickers",
        new=AsyncMock(return_value=candidates),
    ), patch(
        "backend.algo.backtest.universe._registry_meta",
        return_value=fake_registry,
    ):
        out = await resolve_universe(user=user, strategy=strategy)
    assert out == ["TCS.NS"]


@pytest.mark.asyncio
async def test_filter_market_all_keeps_everything():
    user = UserContext(
        user_id="55555555-5555-5555-5555-555555555555",
        email="t@t", role="pro",
    )
    strategy = _make_strategy(
        "discovery", market="all", ticker_types=["stock"],
    )
    candidates = ["TCS.NS", "AAPL"]
    fake_registry = {
        "TCS.NS": {"ticker_type": "stock"},
        "AAPL": {"ticker_type": "stock"},
    }
    with patch(
        "backend.algo.backtest.universe._scoped_tickers",
        new=AsyncMock(return_value=candidates),
    ), patch(
        "backend.algo.backtest.universe._registry_meta",
        return_value=fake_registry,
    ):
        out = await resolve_universe(user=user, strategy=strategy)
    # market=all skips the market gate; ticker_type=stock keeps both.
    assert sorted(out) == ["AAPL", "TCS.NS"]


@pytest.mark.asyncio
async def test_filter_drops_tickers_missing_from_registry():
    user = UserContext(
        user_id="66666666-6666-6666-6666-666666666666",
        email="t@t", role="pro",
    )
    strategy = _make_strategy(
        "discovery", market="india", ticker_types=["stock"],
    )
    candidates = ["TCS.NS", "GHOST.NS"]
    fake_registry = {"TCS.NS": {"ticker_type": "stock"}}
    with patch(
        "backend.algo.backtest.universe._scoped_tickers",
        new=AsyncMock(return_value=candidates),
    ), patch(
        "backend.algo.backtest.universe._registry_meta",
        return_value=fake_registry,
    ):
        out = await resolve_universe(user=user, strategy=strategy)
    # GHOST.NS isn't in stock_master — dropped.
    assert out == ["TCS.NS"]


@pytest.mark.asyncio
async def test_no_filter_skips_filtering_entirely():
    """Backward compat: a bare strategy (no `filter` attr) just
    returns the scoped candidate set unchanged."""
    user = UserContext(
        user_id="77777777-7777-7777-7777-777777777777",
        email="t@t", role="pro",
    )
    strategy = _make_strategy("watchlist")  # no filter
    with patch(
        "backend.algo.backtest.universe._scoped_tickers",
        new=AsyncMock(return_value=["TCS.NS", "AAPL"]),
    ):
        out = await resolve_universe(user=user, strategy=strategy)
    assert out == ["TCS.NS", "AAPL"]
