"""Resolve a Strategy's stored universe.{scope,filter} to a
concrete list of tickers.

Two-stage pipeline:

1. ``scope`` (``discovery|watchlist|portfolio``) selects the
   candidate set via the existing ``_scoped_tickers`` helper —
   same scoping that powers the Insights tabs.
2. ``filter`` (``market``, ``ticker_type``) trims that candidate
   set against the platform's stock_master registry. The filter
   matches the AST schema in ``backend/algo/strategy/ast.py``
   (``UniverseFilter``).

Backward-compat: if the strategy passes a bare object (no
``filter`` attribute), only stage 1 runs.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from auth.models import UserContext
from backend.insights_routes import _scoped_tickers
from backend.market_utils import detect_market

_logger = logging.getLogger(__name__)

_VALID_SCOPES = {"discovery", "watchlist", "portfolio"}


def _registry_meta() -> dict[str, dict[str, Any]]:
    """Lazy stock_master fetch. Cached at the StockRepository
    layer so repeated callers within one request don't re-hit
    Iceberg."""
    from stocks.repository import StockRepository
    return StockRepository().get_all_registry()


def _apply_filter(
    tickers: Iterable[str],
    *,
    markets: set[str] | None,
    ticker_types: set[str] | None,
) -> list[str]:
    """Drop tickers whose market or ticker_type don't match.

    ``markets``: if not None, keep only tickers whose
    ``detect_market(ticker)`` is in the set. ``"all"`` short-
    circuits the market check.

    ``ticker_types``: if not None, keep only tickers whose
    registry ``ticker_type`` is in the set.
    """
    market_check = (
        markets is not None and "all" not in markets
    )
    type_check = ticker_types is not None
    if not market_check and not type_check:
        return list(tickers)

    registry = _registry_meta() if type_check else {}
    out: list[str] = []
    for t in tickers:
        if market_check and detect_market(t) not in markets:
            continue
        if type_check:
            meta = registry.get(t)
            if meta is None:
                # Unknown ticker — skip rather than including a
                # row we can't classify. The runner would also
                # have no OHLCV bars for it.
                continue
            tt = meta.get("ticker_type", "stock")
            if tt not in ticker_types:
                continue
        out.append(t)
    return out


async def resolve_universe(
    *,
    user: UserContext,
    strategy: Any,
) -> list[str]:
    """Return the list of tickers the backtest should iterate.

    Strategy AST stores ``universe.scope`` ∈ ``{discovery,
    watchlist, portfolio}`` and an optional ``universe.filter``
    (``market``, ``ticker_type``). Unknown scopes degrade to
    ``watchlist`` (safest non-empty default).
    """
    raw = getattr(strategy.universe, "scope", "watchlist")
    scope = raw if raw in _VALID_SCOPES else "watchlist"
    candidates = await _scoped_tickers(user=user, scope=scope)

    filter_obj = getattr(strategy.universe, "filter", None)
    if filter_obj is None:
        return candidates

    raw_market = getattr(filter_obj, "market", None)
    markets: set[str] | None = (
        {str(raw_market)} if raw_market else None
    )
    raw_types = getattr(filter_obj, "ticker_type", None)
    ticker_types: set[str] | None = (
        {str(t) for t in raw_types} if raw_types else None
    )

    filtered = _apply_filter(
        candidates,
        markets=markets,
        ticker_types=ticker_types,
    )
    _logger.info(
        "resolve_universe scope=%s filter=(market=%s, "
        "ticker_type=%s) → %d candidates → %d after filter",
        scope, raw_market, raw_types,
        len(candidates), len(filtered),
    )
    return filtered
