"""Synthesis node — final response formatting.

If the sub-agent produced a complete response (>100
chars), passes it through unchanged.  Otherwise, uses
the synthesis LLM cascade to polish the answer.
"""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

_logger = logging.getLogger(__name__)

_ACTIONS_RE = re.compile(
    r"<!--actions:(.*?)-->", re.DOTALL,
)

# Patterns that indicate a stock-analysis response that
# MUST have come from tool calls.  Only matches specific
# technical/quantitative indicators — NOT general
# financial terms like "sector" or "allocation" which
# are legitimate in portfolio follow-up responses.
_DATA_INDICATORS = re.compile(
    r"(?:"
    r"CMP|Current\s+Price\s*[\:\|]"
    r"|Market\s+Cap\s*[\:\|]"
    r"|P/E\s+(?:Ratio|TTM|\()"
    r"|EPS\s*[\:\|₹\$]"
    r"|ROE\s*[\:\|]"
    r"|RSI\s*\(\d"
    r"|MACD\s+(?:line|signal|crossover)"
    r"|SMA\s+\d{2,3}"
    r"|Bollinger"
    r"|MAE\s*[\:\|]"
    r"|RMSE\s*[\:\|]"
    r"|MAPE\s*[\:\|]"
    r"|52.wk\s+(?:high|low|range)"
    r"|Price\s+Target\s*[\:\|]"
    r")",
    re.IGNORECASE,
)

_HALLUCINATION_FALLBACK = (
    "I wasn't able to complete the analysis right now "
    "— the data tools didn't respond in time. This "
    "can happen when the LLM service is temporarily "
    "overloaded.\n\n"
    "**Please try again** in a moment, or rephrase "
    "your request. For example:\n"
    "- *\"analyse SBIN.NS\"*\n"
    "- *\"fetch and analyse ICICIBANK.NS\"*"
)

_SYNTHESIS_PROMPT = (
    "You are a financial analyst on the ASET Platform. "
    "Synthesize a clear, actionable response from the "
    "data provided. Include specific numbers, dates, "
    "and actionable recommendations where applicable. "
    "Be concise but thorough.\n\n"
    "FORMAT: Use Markdown — **bold** for key figures, "
    "bullet points for lists, tables for comparisons, "
    "### headings for sections. Keep paragraphs short "
    "(2-3 sentences max).\n\n"
    "TABLE PRESERVATION: When the agent response "
    "contains markdown tables, comparison grids, or "
    "structured data — preserve them exactly. Do not "
    "collapse tables into prose. Add narrative context "
    "around tables but never remove or flatten them.\n\n"
    "NO HALLUCINATION ON TRUNCATION: If any tool output "
    "or prior message ends with a literal marker like "
    "`[truncated N chars]`, you MUST NOT invent, "
    "enumerate, or claim items beyond what is visible. "
    "Do not write phrases like 'truncated in display' "
    "or 'confirmed in memory context'. Instead, list "
    "only what is shown and say 'some rows were trimmed "
    "to fit token limits — ask me to narrow the query "
    "to see the rest.'"
)

# Minimum response length to skip synthesis.
_PASSTHROUGH_MIN_CHARS = 100


def _extract_actions(
    text: str,
) -> tuple[str, list[dict]]:
    """Strip ``<!--actions:[...]-->`` from text.

    Args:
        text: Response text that may contain an actions
            HTML comment block.

    Returns:
        Tuple of (clean_text, actions_list).
    """
    m = _ACTIONS_RE.search(text)
    if not m:
        return text, []
    try:
        actions = json.loads(m.group(1))
        if not isinstance(actions, list):
            return text, []
    except (json.JSONDecodeError, TypeError):
        return text, []
    clean = text[: m.start()] + text[m.end() :]
    return clean.strip(), actions


def _is_hallucinated(state: dict, text: str) -> bool:
    """Detect if the response contains fabricated data.

    Returns ``True`` when the text looks like a data-heavy
    analysis (prices, metrics, technicals) but no actual
    tool calls were made during the agent's execution.

    Args:
        state: Graph state with ``tool_events``.
        text: Response text to check.

    Returns:
        ``True`` if likely hallucinated.
    """
    tool_events = state.get("tool_events", [])
    real_tools = [
        e for e in tool_events
        if e.get("type") == "tool_done"
    ]
    if real_tools:
        return False
    # Check for data-heavy patterns in a response
    # that had zero tool calls.
    matches = _DATA_INDICATORS.findall(text)
    if len(matches) >= 3:
        _logger.warning(
            "Hallucination detected: %d data "
            "indicators but 0 tool calls — "
            "rejecting response",
            len(matches),
        )
        return True
    return False


def synthesis(state: dict) -> dict:
    """Format the final response.

    Long sub-agent responses pass through unchanged.
    Short or empty responses get LLM synthesis.
    Extracts ``<!--actions:[...]-->`` blocks for the
    frontend to render as clickable buttons.
    Rejects hallucinated data-heavy responses that
    had zero tool calls.
    """
    final = state.get("final_response", "")

    # Hallucination guard: reject data-heavy responses
    # that were generated without any tool calls.
    if final and _is_hallucinated(state, final):
        return {"final_response": _HALLUCINATION_FALLBACK}

    if final and len(final) >= _PASSTHROUGH_MIN_CHARS:
        clean, actions = _extract_actions(final)
        _store_in_cache(
            {**state, "final_response": clean}, clean,
        )
        result = {"final_response": clean}
        if actions:
            result["response_actions"] = actions
        return result

    # Need synthesis — use FallbackLLM
    # Import here to avoid circular deps at module
    # load time.
    try:
        from config import get_settings
        from llm_fallback import FallbackLLM
        from message_compressor import (
            MessageCompressor,
        )
        from token_budget import get_token_budget

        settings = get_settings()
        tiers = [
            t.strip()
            for t in (
                settings.synthesis_model_tiers
                or settings.groq_model_tiers
            ).split(",")
            if t.strip()
        ]
        from config import get_pool_groups
        from observability import (
            get_obs_collector,
        )

        llm = FallbackLLM(
            groq_models=tiers,
            anthropic_model=None,
            temperature=0,
            agent_id="synthesis",
            token_budget=get_token_budget(),
            compressor=MessageCompressor(),
            obs_collector=get_obs_collector(),
            cascade_profile="synthesis",
            pool_groups=get_pool_groups(
                "synthesis",
            ),
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
    """Store query-response in semantic cache.

    Only caches responses where at least one tool was
    invoked.  Responses generated without tool calls
    are likely hallucinated and must NOT be cached.
    """
    try:
        from agents.nodes.query_cache import (
            store_cache,
        )

        query = state.get("user_input", "")
        intent = state.get("intent", "")
        tool_events = state.get("tool_events", [])

        if not query or not response:
            return
        if not tool_events:
            _logger.debug(
                "Skipping cache — no tool calls: %s",
                query[:50],
            )
            return

        store_cache(query, response, intent)
    except Exception:
        pass  # cache store is best-effort
