"""Unit tests for the Insights ticker-scoping helper.

Covers the three-tier visibility model enforced by
``backend.insights_routes._scoped_tickers``:

* ``discovery`` — full universe for pro + superuser, else
  watchlist ∪ holdings.
* ``watchlist`` — watchlist ∪ holdings for all roles.
* ``portfolio`` — current holdings only for all roles.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import backend.insights_routes as ir
from auth.models import UserContext


def _ctx(role: str) -> UserContext:
    return UserContext(
        user_id="user-1",
        email="u@example.com",
        role=role,
    )


@pytest.fixture(autouse=True)
def _mock_repos(monkeypatch):
    """Stub watchlist + portfolio + registry lookups."""

    class FakeRepo:
        async def get_user_tickers(self, uid):
            return ["AAPL", "MSFT"]

    class FakeStockRepo:
        def get_portfolio_holdings(self, uid):
            return pd.DataFrame(
                {"ticker": ["TSLA"], "quantity": [5]},
            )

        def get_all_registry(self):
            return {
                "AAPL": {"ticker_type": "stock"},
                "MSFT": {"ticker_type": "stock"},
                "TSLA": {"ticker_type": "stock"},
                "SPY": {"ticker_type": "etf"},
                "^GSPC": {"ticker_type": "index"},
                "GC=F": {"ticker_type": "commodity"},
            }

    monkeypatch.setattr(
        ir._helpers,
        "_get_repo",
        lambda: FakeRepo(),
    )
    monkeypatch.setattr(
        ir,
        "_get_stock_repo",
        lambda: FakeStockRepo(),
    )


@pytest.mark.asyncio
async def test_portfolio_scope_returns_holdings_only():
    got = await ir._scoped_tickers(_ctx("general"), "portfolio")
    assert got == ["TSLA"]


@pytest.mark.asyncio
async def test_watchlist_scope_unions_watchlist_and_holdings():
    got = await ir._scoped_tickers(_ctx("general"), "watchlist")
    assert sorted(got) == ["AAPL", "MSFT", "TSLA"]


@pytest.mark.asyncio
async def test_discovery_general_sees_watchlist_and_holdings():
    got = await ir._scoped_tickers(_ctx("general"), "discovery")
    assert sorted(got) == ["AAPL", "MSFT", "TSLA"]


@pytest.mark.asyncio
async def test_discovery_pro_sees_full_universe():
    got = await ir._scoped_tickers(_ctx("pro"), "discovery")
    # Stocks + ETFs only — index / commodity excluded.
    assert "SPY" in got
    assert "AAPL" in got
    assert "^GSPC" not in got
    assert "GC=F" not in got


@pytest.mark.asyncio
async def test_discovery_superuser_sees_full_universe():
    got = await ir._scoped_tickers(_ctx("superuser"), "discovery")
    assert "SPY" in got
    assert "^GSPC" not in got


@pytest.mark.asyncio
async def test_watchlist_scope_deduplicates():
    got = await ir._scoped_tickers(_ctx("pro"), "watchlist")
    # AAPL in watchlist; TSLA in holdings. No duplicates.
    assert len(got) == len(set(t.upper() for t in got))


@pytest.mark.asyncio
async def test_empty_portfolio_still_returns_watchlist(
    monkeypatch,
):
    class FakeStockRepo:
        def get_portfolio_holdings(self, uid):
            return pd.DataFrame()

        def get_all_registry(self):
            return {"AAPL": {"ticker_type": "stock"}}

    monkeypatch.setattr(
        ir,
        "_get_stock_repo",
        lambda: FakeStockRepo(),
    )
    got = await ir._scoped_tickers(_ctx("general"), "watchlist")
    assert sorted(got) == ["AAPL", "MSFT"]


@pytest.mark.asyncio
async def test_empty_portfolio_portfolio_scope_returns_empty(
    monkeypatch,
):
    class FakeStockRepo:
        def get_portfolio_holdings(self, uid):
            return pd.DataFrame()

        def get_all_registry(self):
            return {}

    monkeypatch.setattr(
        ir,
        "_get_stock_repo",
        lambda: FakeStockRepo(),
    )
    got = await ir._scoped_tickers(_ctx("pro"), "portfolio")
    assert got == []


def test_full_universe_excludes_non_analyzable_types():
    class FakeStockRepo:
        def get_all_registry(self):
            return {
                "AAPL": {"ticker_type": "stock"},
                "SPY": {"ticker_type": "etf"},
                "^GSPC": {"ticker_type": "index"},
                "GC=F": {"ticker_type": "commodity"},
            }

    got = ir._full_universe(FakeStockRepo())
    assert sorted(got) == ["AAPL", "SPY"]
