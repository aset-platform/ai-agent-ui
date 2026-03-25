"""Synthesis node — final response formatting.

If the sub-agent produced a complete response (>100
chars), passes it through unchanged.  Otherwise, uses
the synthesis LLM cascade to polish the answer.
"""

from __future__ import annotations

import logging

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

_logger = logging.getLogger(__name__)

_SYNTHESIS_PROMPT = (
    "You are a financial analyst on the ASET Platform. "
    "Synthesize a clear, actionable response from the "
    "data provided. Include specific numbers, dates, "
    "and actionable recommendations where applicable. "
    "Be concise but thorough."
)

# Minimum response length to skip synthesis.
_PASSTHROUGH_MIN_CHARS = 100


def synthesis(state: dict) -> dict:
    """Format the final response.

    Long sub-agent responses pass through unchanged.
    Short or empty responses get LLM synthesis.
    """
    final = state.get("final_response", "")

    if final and len(final) >= _PASSTHROUGH_MIN_CHARS:
        # Store in query cache for deduplication
        _store_in_cache(state, final)
        return {"final_response": final}

    # Need synthesis — use FallbackLLM
    # Import here to avoid circular deps at module
    # load time.
    try:
        from config import get_settings
        from llm_fallback import FallbackLLM
        from message_compressor import (
            MessageCompressor,
        )
        from token_budget import TokenBudget

        settings = get_settings()
        tiers = [
            t.strip()
            for t in (
                settings.synthesis_model_tiers
                or settings.groq_model_tiers
            ).split(",")
            if t.strip()
        ]
        llm = FallbackLLM(
            groq_models=tiers,
            anthropic_model=None,
            temperature=0,
            agent_id="synthesis",
            token_budget=TokenBudget(),
            compressor=MessageCompressor(),
            cascade_profile="synthesis",
        )

        messages = list(state.get("messages", []))
        messages.insert(
            0,
            SystemMessage(content=_SYNTHESIS_PROMPT),
        )
        if final:
            messages.append(
                HumanMessage(content=final),
            )

        resp = llm.invoke(messages)
        synthesized = resp.content
        _store_in_cache(state, synthesized)
        return {"final_response": synthesized}

    except Exception:
        _logger.warning(
            "Synthesis LLM failed, returning raw "
            "response",
            exc_info=True,
        )
        return {
            "final_response": final
            or "I couldn't generate a response. "
            "Please try rephrasing your question."
        }


def _store_in_cache(
    state: dict, response: str,
) -> None:
    """Store query-response in semantic cache."""
    try:
        from agents.nodes.query_cache import (
            store_cache,
        )

        query = state.get("user_input", "")
        intent = state.get("intent", "")
        if query and response:
            store_cache(query, response, intent)
    except Exception:
        pass  # cache store is best-effort
