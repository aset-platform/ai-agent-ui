"""Finance-only guardrail node.

First node in the LangGraph supervisor graph.  Checks
content safety, determines if the query is financial,
and extracts ticker symbols.  Non-financial queries are
routed to the decline node.  Zero LLM cost.
"""

from __future__ import annotations

import logging
import re
import time

_logger = logging.getLogger(__name__)

from agents.router import (
    _STOCK_KEYWORDS,
    _TICKER_PATTERN,
    is_blocked,
)

# Additional financial keywords beyond _STOCK_KEYWORDS
# to improve classification accuracy.
_EXTRA_FINANCIAL: set[str] = {
    "portfolio",
    "holdings",
    "allocation",
    "weightage",
    "rebalance",
    "diversify",
    "profit",
    "loss",
    "pnl",
    "p&l",
    "capital gain",
    "capital loss",
    "gain",
    "yield",
    "beta",
    "alpha",
    "risk",
    "hedge",
    "margin",
    "leverage",
    "valuation",
    "outlook",
    "predict",
    "prophet",
    "technical",
    "fundamental",
    "indicator",
    "indicators",
    "screener",
    "correlation",
    "headline",
    "sentiment",
    "recommendation",
    "analyst",
    "upgrade",
    "downgrade",
    "overweight",
    "underweight",
}

_ALL_FINANCIAL = _STOCK_KEYWORDS | _EXTRA_FINANCIAL

# Common uppercase words that look like tickers but
# are not.  Extends the filter in router.py.
_COMMON_WORDS: set[str] = {
    "I", "A", "IT", "IS", "AM", "AN", "AT", "AS",
    "BE", "BY", "DO", "GO", "HE", "IF", "IN", "ME",
    "MY", "NO", "OF", "OK", "ON", "OR", "SO", "TO",
    "UP", "US", "WE", "THE", "AND", "FOR", "NOT",
    "BUT", "ALL", "CAN", "HER", "WAS", "ONE", "OUR",
    "OUT", "ARE", "HAS", "HIS", "HOW", "ITS", "MAY",
    "NEW", "NOW", "OLD", "SEE", "WAY", "WHO", "DID",
    "GET", "HIM", "LET", "SAY", "SHE", "TOO", "USE",
}


_INTENT_LABELS: dict[str, str] = {
    "portfolio": "Portfolio analysis (holdings, P&L, rebalancing)",
    "stock_analysis": "Stock analysis (technicals, OHLCV, indicators)",
    "forecast": "Price forecast (Prophet model, price targets)",
    "research": "News & research (headlines, analyst ratings)",
}


def _build_clarification(
    user_input: str,
    old_intent: str,
    new_intent: str,
) -> str:
    """Build a clarification message with numbered options.

    Args:
        user_input: Raw user message.
        old_intent: Previous conversation intent.
        new_intent: Newly detected intent.

    Returns:
        Markdown-formatted clarification string.
    """
    old_label = _INTENT_LABELS.get(
        old_intent, old_intent,
    )
    new_label = _INTENT_LABELS.get(
        new_intent, new_intent,
    )
    return (
        f"I noticed your request could go in two "
        f"directions. Which would you prefer?\n\n"
        f"1. **{new_label}**\n"
        f"2. **{old_label}** "
        f"(continuing current conversation)\n\n"
        f"Reply with **1** or **2**, or rephrase "
        f"your question to be more specific."
    )


def _extract_tickers(user_input: str) -> list[str]:
    """Extract ticker-like symbols from *user_input*.

    Filters out common English words that look like
    ticker symbols (e.g. ``"I"``, ``"A"``, ``"IT"``).

    Args:
        user_input: Raw user message.

    Returns:
        List of ticker strings.
    """
    raw = _TICKER_PATTERN.findall(user_input)
    return [t for t in raw if t not in _COMMON_WORDS]


def _merge_tickers(
    ctx_tickers: list[str] | None,
    user_input: str,
) -> list[str]:
    """Merge context tickers with newly extracted ones.

    Args:
        ctx_tickers: Tickers from conversation context.
        user_input: Raw user message for extraction.

    Returns:
        Deduplicated merged list.
    """
    merged = list(ctx_tickers or [])
    seen = set(merged)
    for t in _extract_tickers(user_input):
        if t not in seen:
            merged.append(t)
            seen.add(t)
    return merged


def guardrail(state: dict) -> dict:
    """Check if query is financial and extract tickers.

    Returns a dict with ``next_agent`` set to either
    ``"router"`` (financial) or ``"decline"``
    (non-financial or blocked).
    """
    user_input: str = state.get("user_input", "")

    # Record start time for latency tracking
    start_ns = time.monotonic_ns()

    # ── Query cache check ─────────────────────────
    try:
        from agents.nodes.query_cache import (
            check_cache,
        )

        cached = check_cache(user_input)
        if cached:
            return {
                "final_response": cached,
                "next_agent": "cache_hit",
                "start_time_ns": start_ns,
                "tool_events": [],
                "current_agent": "cache",
            }
    except Exception:
        pass  # cache check is best-effort

    # ── Content safety ──────────────────────────────
    if is_blocked(user_input):
        return {
            "next_agent": "decline",
            "error": "blocked",
            "start_time_ns": start_ns,
        }

    # ── Intent-aware follow-up detection ────────────
    # 1. Run keyword router (zero LLM cost)
    # 2. If keywords found + same intent → reuse agent
    # 3. If keywords found + different intent → router
    # 4. If no keywords + context → LLM classifier
    #    for ambiguous messages ("which one?")
    session_id = state.get("session_id", "")
    _ctx = None
    if session_id:
        try:
            from agents.conversation_context import (
                context_store,
            )

            _ctx = context_store.get(session_id)
        except Exception:
            _logger.debug(
                "Context lookup failed",
                exc_info=True,
            )

    from agents.nodes.router_node import (
        best_intent,
        score_intents,
    )

    detected_intent = best_intent(user_input)

    if _ctx and _ctx.last_agent:
        if detected_intent:
            if detected_intent == _ctx.last_intent:
                # Same-intent follow-up → reuse agent
                _logger.debug(
                    "Same-intent follow-up (%s)"
                    " — reusing agent=%s",
                    detected_intent,
                    _ctx.last_agent,
                )
                return {
                    "tickers": _merge_tickers(
                        _ctx.tickers_mentioned,
                        user_input,
                    ),
                    "next_agent": _ctx.last_agent,
                    "intent": _ctx.last_intent,
                    "start_time_ns": start_ns,
                }

            # Intent CHANGED — check if ambiguous
            scores = score_intents(user_input)
            old_score = scores.get(
                _ctx.last_intent, 0,
            )
            new_score = scores.get(
                detected_intent, 0,
            )

            if old_score > 0 and old_score == new_score:
                # Ambiguous: tied scores across intents
                # Offer clarification to the user
                _logger.debug(
                    "Ambiguous intent switch: "
                    "%s=%d vs %s=%d — clarifying",
                    _ctx.last_intent,
                    old_score,
                    detected_intent,
                    new_score,
                )
                clarification = (
                    _build_clarification(
                        user_input,
                        _ctx.last_intent,
                        detected_intent,
                    )
                )
                return {
                    "final_response": clarification,
                    "next_agent": "cache_hit",
                    "start_time_ns": start_ns,
                    "tool_events": [],
                    "current_agent": "clarification",
                }

            # Clear winner → route through router
            _logger.debug(
                "Intent switch: %s → %s"
                " — re-routing",
                _ctx.last_intent,
                detected_intent,
            )
            # Fall through to financial relevance
        else:
            # No keywords → LLM topic classifier
            # for ambiguous messages
            try:
                from agents.nodes.topic_classifier import (
                    classify_followup,
                )

                result = classify_followup(
                    user_input, _ctx,
                )
                if result == "follow_up":
                    _logger.debug(
                        "Ambiguous follow-up"
                        " — reusing agent=%s",
                        _ctx.last_agent,
                    )
                    return {
                        "tickers": (
                            _ctx.tickers_mentioned
                        ),
                        "next_agent": _ctx.last_agent,
                        "intent": _ctx.last_intent,
                        "start_time_ns": start_ns,
                    }
            except Exception:
                _logger.debug(
                    "Follow-up detection failed",
                    exc_info=True,
                )

    # ── Financial relevance ─────────────────────────
    lower = user_input.lower()
    tokens = set(re.findall(r"[a-z&]+", lower))

    # Strong keywords (unambiguous financial terms)
    strong = tokens & _STOCK_KEYWORDS
    # Weak keywords (could be financial or general)
    weak = tokens & _EXTRA_FINANCIAL

    tickers = _extract_tickers(user_input)
    has_ticker = bool(tickers)

    # Financial if: strong keyword, or ticker found,
    # or 2+ weak keywords (reduces false positives
    # from single ambiguous words like "news").
    has_keyword = bool(
        strong or has_ticker or len(weak) >= 2
    )

    if not has_keyword and not has_ticker:
        return {
            "next_agent": "decline",
            "start_time_ns": start_ns,
        }

    return {
        "tickers": tickers,
        "next_agent": "router",
        "start_time_ns": start_ns,
    }
