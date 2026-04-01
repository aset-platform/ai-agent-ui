"""Sub-agent node factory for LangGraph.

Provides ``_make_sub_agent_node()`` which creates a
tool-calling loop node from a ``SubAgentConfig``.
Each node invokes ``FallbackLLM`` with bound tools,
loops on ``tool_calls``, collects NDJSON events, and
calls ``format_response()`` for post-processing.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agents.config import MAX_ITERATIONS
from tools.registry import ToolRegistry

# Thread-local event sink — set by WS/HTTP handler
# before running the graph, read by sub-agent nodes.
_tls = threading.local()


def set_event_sink(callback: Callable | None) -> None:
    """Set a callback for real-time tool events."""
    _tls.event_sink = callback


def _emit(event: dict) -> None:
    """Push an event to the current thread's sink."""
    sink = getattr(_tls, "event_sink", None)
    if sink is not None:
        try:
            sink(event)
        except Exception:
            pass


_logger = logging.getLogger(__name__)

# Tools known to call external APIs (for data source
# tracking).
_EXTERNAL_TOOLS: dict[str, str] = {
    "fetch_stock_data": "yfinance",
    "get_stock_info": "yfinance",
    "get_dividend_history": "yfinance",
    "fetch_multiple_stocks": "yfinance",
    "fetch_quarterly_results": "yfinance",
    "search_market_news": "serpapi",
    "search_financial_news": "serpapi",
    "get_ticker_news": "yfinance",
    "get_analyst_recommendations": "yfinance",
}


@dataclass
class SubAgentConfig:
    """Configuration for a LangGraph sub-agent node."""

    agent_id: str
    name: str
    description: str
    system_prompt: str
    tool_names: list[str] = field(
        default_factory=list,
    )
    format_response: (
        Callable[[str, list], str] | None
    ) = None


def _infer_data_source(
    tool_name: str,
    result: str,
) -> str:
    """Infer data source from tool name and result.

    Returns ``"iceberg"`` if result indicates cached
    data, otherwise the external source.
    """
    # If result mentions "already up to date" or
    # "cached" or "fresh", it came from Iceberg.
    lower = result[:500].lower()
    if any(
        w in lower
        for w in (
            "already up to date",
            "skipped",
            "cached",
            "fresh",
            "from local",
        )
    ):
        return "iceberg"

    return _EXTERNAL_TOOLS.get(tool_name, "iceberg")


_CURRENCY_SYMBOLS: dict[str, str] = {
    "INR": "₹", "USD": "$", "EUR": "€",
    "GBP": "£", "JPY": "¥",
}


def _build_context_block(
    user_ctx: dict,
) -> str:
    """Build a context block for the system prompt.

    Summarises the user's portfolio currency/market
    mix so the LLM uses the correct currency symbols.
    """
    currencies = user_ctx.get("currencies", {})
    markets = user_ctx.get("markets", {})
    total = user_ctx.get("total_holdings", 0)
    if not currencies and not markets:
        return ""

    parts = ["## User Portfolio Context"]
    if total:
        parts.append(f"- Holdings: {total} stocks")
    if currencies:
        ccy_parts = []
        for ccy, count in sorted(
            currencies.items(),
            key=lambda x: -x[1],
        ):
            sym = _CURRENCY_SYMBOLS.get(ccy, ccy)
            ccy_parts.append(
                f"{ccy} ({sym}) — {count} holdings"
            )
        parts.append(
            "- Currencies: " + ", ".join(ccy_parts)
        )
    if markets:
        mkt_parts = []
        for mkt, count in sorted(
            markets.items(),
            key=lambda x: -x[1],
        ):
            mkt_parts.append(f"{mkt} ({count})")
        parts.append(
            "- Markets: " + ", ".join(mkt_parts)
        )

    # Explicit instruction based on currency mix
    ccy_list = list(currencies.keys())
    if len(ccy_list) == 1:
        sym = _CURRENCY_SYMBOLS.get(
            ccy_list[0], ccy_list[0],
        )
        parts.append(
            f"\nAll holdings are {ccy_list[0]}. "
            f"Use {sym} for all monetary values."
        )
    elif len(ccy_list) > 1:
        parts.append(
            "\nMulti-currency portfolio. "
            "Always break down values per currency. "
            "Ask the user which market if unclear."
        )

    return "\n".join(parts)


def _make_sub_agent_node(
    config: SubAgentConfig,
    tool_registry: ToolRegistry,
    llm_factory: Callable,
) -> Callable:
    """Create a sub-agent node function.

    The returned function has signature
    ``node(state: dict) -> dict`` compatible with
    LangGraph node requirements.

    Args:
        config: Sub-agent configuration.
        tool_registry: Shared tool registry.
        llm_factory: Callable that returns a
            FallbackLLM instance for the given
            agent_id.
    """

    def _build_synthesis_llm():
        """Create synthesis-tier FallbackLLM."""
        try:
            from config import (
                get_pool_groups,
                get_settings,
            )
            from llm_fallback import FallbackLLM
            from message_compressor import (
                MessageCompressor,
            )
            from token_budget import get_token_budget

            s = get_settings()
            if s.ai_agent_ui_env == "test":
                return None
            tiers = [
                t.strip()
                for t in (
                    s.synthesis_model_tiers
                    or s.groq_model_tiers
                ).split(",")
                if t.strip()
            ]
            return FallbackLLM(
                groq_models=tiers,
                anthropic_model="claude-sonnet-4-6",
                temperature=0,
                agent_id=config.agent_id,
                token_budget=get_token_budget(),
                compressor=MessageCompressor(),
                cascade_profile="synthesis",
                pool_groups=get_pool_groups(
                    "synthesis",
                ),
            )
        except Exception:
            return None

    def node(state: dict) -> dict:
        """Execute the sub-agent tool-calling loop."""
        llm = llm_factory(agent_id=config.agent_id)
        tools = tool_registry.get_tools(
            config.tool_names,
        )
        llm_with_tools = llm.bind_tools(tools)

        # Inject dynamic user context into prompt
        prompt = config.system_prompt
        user_ctx = state.get("user_context") or {}
        if user_ctx:
            ctx_block = _build_context_block(user_ctx)
            if ctx_block:
                prompt = prompt + "\n\n" + ctx_block

        # Build messages: summary-based context instead
        # of raw conversation history.
        #
        # Raw history causes two problems:
        #  1. Intent switch: prior agent's verbose tool
        #     results confuse weaker models into
        #     hallucinating instead of calling tools.
        #  2. Same-intent follow-up: history grows
        #     unboundedly, hitting Groq's 12K TPM by
        #     turn 3-4 and cascading to weaker models.
        #
        # Solution: use the rolling ConversationContext
        # summary (~100 tokens) instead of raw messages
        # (~3,000+ tokens). Summary is already maintained
        # by conversation_context.py after each turn.
        #
        # Rules:
        #  - First message: prompt + user query
        #  - Same-intent follow-up: prompt + summary
        #    + user query
        #  - Intent switch: prompt + user query only
        all_msgs = list(state.get("messages", []))
        user_input = state.get("user_input", "")
        session_id = state.get("session_id", "")

        # Extract the current user message
        last_human = [
            m for m in all_msgs
            if isinstance(m, HumanMessage)
        ]
        current_query = (
            last_human[-1]
            if last_human
            else HumanMessage(content=user_input)
        )

        # Look up conversation context for summary
        ctx_summary = ""
        is_intent_switch = False
        if session_id:
            try:
                from agents.conversation_context import (
                    context_store,
                )

                _ctx = context_store.get(session_id)
                if _ctx:
                    if (
                        _ctx.last_agent
                        and _ctx.last_agent
                        != config.agent_id
                    ):
                        is_intent_switch = True
                        _logger.info(
                            "Intent switch %s → %s: "
                            "clean context",
                            _ctx.last_agent,
                            config.agent_id,
                        )
                    if (
                        _ctx.summary
                        and not is_intent_switch
                    ):
                        ctx_summary = _ctx.summary
            except Exception:
                pass

        messages: list = [
            SystemMessage(content=prompt),
        ]

        # Inject retrieved memories from pgvector.
        _mem_list = state.get(
            "retrieved_memories", [],
        )
        if _mem_list:
            from memory_retriever import (
                format_memories_for_prompt,
            )

            _mem_block = format_memories_for_prompt(
                _mem_list,
            )
            if _mem_block:
                messages.append(
                    SystemMessage(
                        content=_mem_block,
                    ),
                )

        if ctx_summary:
            messages.append(
                SystemMessage(
                    content=(
                        "[Prior conversation]\n"
                        + ctx_summary
                    ),
                ),
            )
        messages.append(current_query)
        events: list[dict] = []
        data_sources: list[str] = []

        response = None
        for iteration in range(MAX_ITERATIONS):
            events.append({
                "type": "thinking",
                "iteration": iteration + 1,
                "agent": config.agent_id,
            })

            response = llm_with_tools.invoke(
                messages, iteration=iteration + 1,
            )
            messages.append(response)

            if not getattr(
                response, "tool_calls", None
            ):
                break

            for tc in response.tool_calls:
                name = tc["name"]
                args = tc.get("args", {})

                start_ev = {
                    "type": "tool_start",
                    "tool": name,
                    "args": args,
                    "agent": config.agent_id,
                }
                events.append(start_ev)
                _emit(start_ev)

                try:
                    result = tool_registry.invoke(
                        name, args,
                    )
                except Exception as exc:
                    result = (
                        f"Tool error: {exc!s}"
                    )
                    _logger.warning(
                        "Tool %s failed: %s",
                        name,
                        exc,
                    )

                source = _infer_data_source(
                    name, result,
                )
                data_sources.append(source)

                preview = result[:200]
                done_ev = {
                    "type": "tool_done",
                    "tool": name,
                    "preview": preview,
                    "agent": config.agent_id,
                }
                events.append(done_ev)
                _emit(done_ev)

                messages.append(
                    ToolMessage(
                        content=result,
                        tool_call_id=tc["id"],
                    )
                )

        # Synthesis pass: if tools were called, re-invoke
        # with synthesis-tier models for higher quality
        # final text (gpt-oss-120b > llama-3.3-70b).
        _had_tools = any(
            e.get("type") == "tool_done"
            for e in events
        )
        if _had_tools and response is not None:
            syn_llm = _build_synthesis_llm()
            if syn_llm is not None:
                try:
                    _logger.debug(
                        "Synthesis pass for %s",
                        config.agent_id,
                    )
                    response = syn_llm.invoke(
                        messages,
                        iteration=iteration + 2,
                    )
                    messages.append(response)
                except Exception:
                    _logger.debug(
                        "Synthesis pass failed, "
                        "using tool-tier response",
                        exc_info=True,
                    )

        # Extract final text
        final = ""
        if response is not None:
            final = getattr(
                response, "content", ""
            ) or ""

        # Post-processing
        if config.format_response is not None:
            try:
                final = config.format_response(
                    final, messages,
                )
            except Exception:
                _logger.warning(
                    "format_response failed for %s",
                    config.agent_id,
                    exc_info=True,
                )

        return {
            "messages": [
                AIMessage(content=final),
            ],
            "tool_events": events,
            "final_response": final,
            "data_sources_used": data_sources,
            "current_agent": config.agent_id,
        }

    # Give the node a readable name for LangGraph
    node.__name__ = f"node_{config.agent_id}"
    return node
