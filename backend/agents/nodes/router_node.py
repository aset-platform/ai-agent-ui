"""Two-tier intent router node (Tier 1 — keyword).

Scores the user query against 5 intent keyword sets.
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
        "upgrade",
        "downgrade",
        "market trend",
        "sector trend",
        "latest on",
        "what happened",
        "why did",
    },
    "recommendation": {
        "recommend",
        "recommendations",
        "suggestion",
        "suggestions",
        "what should i buy",
        "what should i sell",
        "portfolio advice",
        "improve my portfolio",
        "improve portfolio",
        "recommendation history",
        "how did your picks",
        "hit rate",
        "track record",
        "pick stocks",
        "why recommended",
        "why was it recommended",
        "recommendation detail",
        "piotroski",
    },
}


def score_intents(
    query: str,
) -> dict[str, int]:
    """Score all intents for *query*.

    Args:
        query: Raw user message (any casing).

    Returns:
        Dict of ``{intent: score}`` for matching
        intents.  Empty dict if no keywords match.
    """
    lower = query.lower()
    scores: dict[str, int] = {}
    for intent, keywords in _INTENT_MAP.items():
        score = sum(
            1 for kw in keywords if kw in lower
        )
        if score > 0:
            scores[intent] = score
    return scores


def best_intent(query: str) -> str | None:
    """Return the highest-scoring intent for *query*.

    Pure keyword matching against :data:`_INTENT_MAP`.
    Zero LLM cost.

    Args:
        query: Raw user message (any casing).

    Returns:
        Intent string (e.g. ``"stock_analysis"``) or
        ``None`` when no keywords match.
    """
    scores = score_intents(query)
    if not scores:
        return None
    return max(scores, key=scores.get)


def router_node(state: dict) -> dict:
    """Tier 1: keyword-based intent classification.

    Scores the query against each intent category.
    Best score wins.  No match → Tier 2 (LLM).
    """
    intent = best_intent(
        state.get("user_input", ""),
    )
    if intent is None:
        return {"next_agent": "llm_classifier"}
    return {
        "intent": intent,
        "next_agent": "supervisor",
    }
