"""Shared user-context builder for LangGraph input.

Extracts portfolio holdings from Iceberg via the singleton
:func:`~tools._stock_shared._require_repo` and returns a
dict with currency / market mix metadata.

Functions
---------
- :func:`build_user_context`
"""

import logging

_logger = logging.getLogger(__name__)


def build_user_context(user_id: str) -> dict:
    """Build user context from portfolio holdings.

    Args:
        user_id: Authenticated user identifier.

    Returns:
        Dict with ``currencies``, ``markets``, and
        ``total_holdings`` keys — or empty dict when
        holdings are unavailable.
    """
    if not user_id:
        return {}
    try:
        from tools._stock_shared import _require_repo

        repo = _require_repo()
        holdings = repo.get_portfolio_holdings(
            user_id,
        )
        if holdings.empty:
            return {}

        currencies: dict[str, int] = {}
        markets: dict[str, int] = {}
        for _, h in holdings.iterrows():
            ccy = h.get("currency", "USD")
            mkt = h.get("market", "us")
            currencies[ccy] = currencies.get(ccy, 0) + 1
            markets[mkt] = markets.get(mkt, 0) + 1
        return {
            "currencies": currencies,
            "markets": markets,
            "total_holdings": len(holdings),
        }
    except Exception:
        _logger.debug(
            "user context build failed",
            exc_info=True,
        )
        return {}
