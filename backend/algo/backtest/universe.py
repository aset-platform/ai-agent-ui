"""Resolve a Strategy's stored universe.scope to a concrete
list of tickers, reusing the existing _scoped_tickers helper
from insights_routes (the same scoping that powers the
Insights tabs).
"""
from __future__ import annotations

import logging
from typing import Any

from auth.models import UserContext
from backend.insights_routes import _scoped_tickers

_logger = logging.getLogger(__name__)

_VALID_SCOPES = {"discovery", "watchlist", "portfolio"}


async def resolve_universe(
    *,
    user: UserContext,
    strategy: Any,
) -> list[str]:
    """Return the list of tickers the backtest should iterate.

    Strategy AST stores ``universe.scope`` ∈ ``{discovery, watchlist,
    portfolio}``. Anything else degrades to ``watchlist`` (the
    safest non-empty default).
    """
    raw = getattr(strategy.universe, "scope", "watchlist")
    scope = raw if raw in _VALID_SCOPES else "watchlist"
    return await _scoped_tickers(user=user, scope=scope)
