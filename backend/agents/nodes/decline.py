"""Decline node — polite non-financial query response.

Returns a fixed decline message for queries that are
not related to stocks, portfolio, or financial markets.
Zero LLM cost.
"""

from __future__ import annotations

_DECLINE_MSG = (
    "I'm specialized in stock analysis and portfolio "
    "management. I can help with market data, stock "
    "analysis, forecasts, and portfolio questions. "
    "What would you like to know about your "
    "investments?"
)


def decline_node(state: dict) -> dict:
    """Return polite decline for non-financial queries."""
    return {
        "final_response": _DECLINE_MSG,
        "tool_events": [],
        "intent": state.get("intent", "decline"),
        "current_agent": "decline",
    }
