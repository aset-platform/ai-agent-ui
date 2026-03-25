"""Two-tier intent router node (Tier 1 — keyword).

Scores the user query against 4 intent keyword sets.
High-confidence match routes directly to the supervisor.
No match routes to the LLM classifier (Tier 2).
Zero LLM cost.
"""

from __future__ import annotations

# Intent → keyword set mapping.
# Each keyword should be a lowercase substring that
# can appear in the user's message.
_INTENT_MAP: dict[str, set[str]] = {
    "portfolio": {
        "portfolio",
        "holdings",
        "holding",
        "allocation",
        "weightage",
        "weight",
        "rebalance",
        "rebalancing",
        "diversify",
        "diversification",
        "sector breakdown",
        "my stocks",
        "my stock",
        "invested",
        "p&l",
        "pnl",
        "profit and loss",
        "returns",
        "return",
        "unrealized",
        "unrealised",
    },
    "stock_analysis": {
        "analyse",
        "analyze",
        "analysis",
        "technical",
        "indicator",
        "indicators",
        "rsi",
        "macd",
        "sma",
        "ema",
        "bollinger",
        "support",
        "resistance",
        "ohlcv",
        "ohlc",
        "candlestick",
        "compare",
        "comparison",
        "fetch",
        "load",
        "screener",
    },
    "forecast": {
        "forecast",
        "predict",
        "prediction",
        "prophet",
        "target",
        "price target",
        "outlook",
        "6 month",
        "3 month",
        "9 month",
        "projection",
        "future price",
        "will go up",
        "will go down",
    },
    "research": {
        "news",
        "headline",
        "headlines",
        "sentiment",
        "analyst",
        "recommendation",
        "upgrade",
        "downgrade",
        "market trend",
        "sector trend",
        "latest on",
        "what happened",
        "why did",
    },
}


def router_node(state: dict) -> dict:
    """Tier 1: keyword-based intent classification.

    Scores the query against each intent category.
    Best score wins.  No match → Tier 2 (LLM).
    """
    query = state.get("user_input", "").lower()

    scores: dict[str, int] = {}
    for intent, keywords in _INTENT_MAP.items():
        score = sum(
            1 for kw in keywords if kw in query
        )
        if score > 0:
            scores[intent] = score

    if not scores:
        # Ambiguous → Tier 2 LLM classifier
        return {"next_agent": "llm_classifier"}

    best = max(scores, key=scores.get)
    return {
        "intent": best,
        "next_agent": "supervisor",
    }
