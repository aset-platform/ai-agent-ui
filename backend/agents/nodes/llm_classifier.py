"""LLM intent classifier node (Tier 2).

Fallback classifier for ambiguous queries that Tier 1
keyword router cannot classify.  Uses the cheapest Groq
model with structured output.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq

_logger = logging.getLogger(__name__)

_VALID_INTENTS = frozenset({
    "portfolio",
    "stock_analysis",
    "forecast",
    "research",
})

_CLASSIFY_PROMPT = (
    "You are a financial query classifier. "
    "Classify the following user query into exactly "
    "one category.\n\n"
    "Categories:\n"
    "- portfolio: questions about user's holdings, "
    "allocation, P&L, rebalancing\n"
    "- stock_analysis: requests to analyse a stock, "
    "view indicators, compare stocks\n"
    "- forecast: predictions, price targets, "
    "outlook, Prophet forecasting\n"
    "- research: news, headlines, analyst "
    "recommendations, sentiment\n"
    "- decline: NOT a financial query at all\n\n"
    "Respond with ONLY the category name, nothing "
    "else.\n\n"
    "Query: {query}\n"
    "Category:"
)


def llm_classifier(state: dict) -> dict:
    """Tier 2: LLM-based intent classification.

    Uses cheapest Groq model for a single
    classification call.
    """
    query = state.get("user_input", "")

    try:
        llm = ChatGroq(
            model="llama-3.3-70b-versatile",
            temperature=0,
            max_retries=0,
            max_tokens=20,
        )
        prompt = _CLASSIFY_PROMPT.format(query=query)
        resp = llm.invoke(
            [HumanMessage(content=prompt)],
        )
        intent = resp.content.strip().lower()
        intent = intent.rstrip(".")

        _logger.debug(
            "LLM classifier → %s for: %s",
            intent,
            query[:80],
        )
    except Exception:
        _logger.warning(
            "LLM classifier failed, defaulting to "
            "stock_analysis",
            exc_info=True,
        )
        intent = "stock_analysis"

    if intent in _VALID_INTENTS:
        return {
            "intent": intent,
            "next_agent": "supervisor",
        }

    # "decline" or unrecognised → decline
    return {"next_agent": "decline"}
