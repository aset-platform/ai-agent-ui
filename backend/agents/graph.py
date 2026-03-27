"""LangGraph supervisor graph assembly.

Builds the compiled ``StateGraph`` that orchestrates
all sub-agents via a guardrail → router → supervisor →
sub-agent → synthesis pipeline.

Usage::

    graph = build_supervisor_graph(
        tool_registry, llm_factory, settings,
    )
    result = graph.invoke(input_state)
"""

from __future__ import annotations

import logging
from typing import Callable

from agents.configs.forecaster import FORECASTER_CONFIG
from agents.configs.portfolio import PORTFOLIO_CONFIG
from agents.configs.research import RESEARCH_CONFIG
from agents.configs.sentiment import SENTIMENT_CONFIG
from agents.configs.stock_analyst import (
    STOCK_ANALYST_CONFIG,
)
from agents.graph_state import AgentState
from agents.nodes.decline import decline_node
from agents.nodes.guardrail import guardrail
from agents.nodes.llm_classifier import llm_classifier
from agents.nodes.log_query import log_query
from agents.nodes.router_node import router_node
from agents.nodes.supervisor import supervisor
from agents.nodes.synthesis import synthesis
from agents.sub_agents import _make_sub_agent_node
from config import Settings
from langgraph.graph import END, START, StateGraph
from langsmith import traceable
from tools.registry import ToolRegistry

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------


def _route_by_next_agent(state: dict) -> str:
    """Conditional edge: route by ``next_agent`` field."""
    return state.get("next_agent", "decline")


def _scrub_graph_inputs(inputs: dict) -> dict:
    """Strip secrets from traceable inputs."""
    scrubbed = dict(inputs)
    if "settings" in scrubbed:
        scrubbed["settings"] = "<Settings: redacted>"
    return scrubbed


@traceable(
    name="build_supervisor_graph",
    run_type="chain",
    process_inputs=_scrub_graph_inputs,
)
def build_supervisor_graph(
    tool_registry: ToolRegistry,
    llm_factory: Callable,
    settings: Settings,
) -> object:
    """Build and compile the supervisor StateGraph.

    Args:
        tool_registry: Shared tool registry with all
            registered tools.
        llm_factory: Callable ``(agent_id=) -> LLM``
            that returns a FallbackLLM instance.
        settings: App settings.

    Returns:
        Compiled LangGraph graph.
    """
    # Create sub-agent nodes from configs
    portfolio_node = _make_sub_agent_node(
        PORTFOLIO_CONFIG,
        tool_registry,
        llm_factory,
    )
    stock_node = _make_sub_agent_node(
        STOCK_ANALYST_CONFIG,
        tool_registry,
        llm_factory,
    )
    forecaster_node = _make_sub_agent_node(
        FORECASTER_CONFIG,
        tool_registry,
        llm_factory,
    )
    research_node = _make_sub_agent_node(
        RESEARCH_CONFIG,
        tool_registry,
        llm_factory,
    )
    sentiment_node = _make_sub_agent_node(
        SENTIMENT_CONFIG,
        tool_registry,
        llm_factory,
    )

    # Build graph
    g = StateGraph(AgentState)

    # ── Add nodes ──────────────────────────────────
    g.add_node("guardrail", guardrail)
    g.add_node("router", router_node)
    g.add_node("llm_classifier", llm_classifier)
    g.add_node("supervisor", supervisor)
    g.add_node("portfolio", portfolio_node)
    g.add_node("stock_analyst", stock_node)
    g.add_node("forecaster", forecaster_node)
    g.add_node("research", research_node)
    g.add_node("sentiment", sentiment_node)
    g.add_node("synthesis", synthesis)
    g.add_node("log_query", log_query)
    g.add_node("decline", decline_node)

    # ── Edges ──────────────────────────────────────

    # Entry → guardrail
    g.add_edge(START, "guardrail")

    # Guardrail → router | decline | cache_hit
    g.add_conditional_edges(
        "guardrail",
        _route_by_next_agent,
        {
            "router": "router",
            "decline": "decline",
            "cache_hit": "log_query",
        },
    )

    # Router → supervisor | llm_classifier
    g.add_conditional_edges(
        "router",
        _route_by_next_agent,
        {
            "supervisor": "supervisor",
            "llm_classifier": "llm_classifier",
        },
    )

    # LLM classifier → supervisor | decline
    g.add_conditional_edges(
        "llm_classifier",
        _route_by_next_agent,
        {
            "supervisor": "supervisor",
            "decline": "decline",
        },
    )

    # Supervisor → sub-agent
    g.add_conditional_edges(
        "supervisor",
        _route_by_next_agent,
        {
            "portfolio": "portfolio",
            "stock_analyst": "stock_analyst",
            "forecaster": "forecaster",
            "research": "research",
            "sentiment": "sentiment",
        },
    )

    # All sub-agents → synthesis
    g.add_edge("portfolio", "synthesis")
    g.add_edge("stock_analyst", "synthesis")
    g.add_edge("forecaster", "synthesis")
    g.add_edge("research", "synthesis")
    g.add_edge("sentiment", "synthesis")

    # Synthesis → log → END
    g.add_edge("synthesis", "log_query")
    g.add_edge("log_query", END)

    # Decline → log → END
    g.add_edge("decline", "log_query")

    compiled = g.compile()
    _logger.info(
        "Supervisor graph compiled: %d nodes",
        len(g.nodes),
    )
    return compiled
