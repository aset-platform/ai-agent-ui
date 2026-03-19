"""General-purpose agent with N-tier Groq/Anthropic LLM cascade.

:class:`GeneralAgent` is the default agent registered at server startup.
It extends :class:`~agents.base.BaseAgent` and is wired with two tools:
:func:`~tools.time_tool.get_current_time` and
:func:`~tools.search_tool.search_web`.

The agent uses :class:`~llm_fallback.FallbackLLM` which cascades through
an ordered list of Groq models before falling back to Anthropic Claude.

Typical usage::

    from tools.registry import ToolRegistry
    from agents.general_agent import create_general_agent

    registry = ToolRegistry()
    # (register tools first)
    agent = create_general_agent(registry)
    reply = agent.run("What is the current time?")
"""

from agents.base import AgentConfig, BaseAgent
from config import get_settings
from message_compressor import MessageCompressor
from token_budget import TokenBudget
from tools.registry import ToolRegistry


def _parse_tiers(csv: str) -> list[str]:
    """Split a comma-separated model list into a clean list."""
    return [m.strip() for m in csv.split(",") if m.strip()]


class GeneralAgent(BaseAgent):
    """General-purpose agent with N-tier LLM cascade.

    Inherits the agentic loop and LLM builders from
    :class:`~agents.base.BaseAgent`.
    """

    pass


def create_general_agent(
    tool_registry: ToolRegistry,
    token_budget: TokenBudget | None = None,
    compressor: MessageCompressor | None = None,
    obs_collector=None,
) -> GeneralAgent:
    """Build a :class:`GeneralAgent` with default settings.

    Args:
        tool_registry: The shared :class:`~tools.registry.ToolRegistry`.
        token_budget: Shared :class:`TokenBudget` instance.
            Created with defaults if ``None``.
        compressor: Shared :class:`MessageCompressor` instance.
            Created with defaults if ``None``.
        obs_collector: Optional
            :class:`~observability.ObservabilityCollector`.

    Returns:
        A ready-to-use :class:`GeneralAgent` instance.
    """
    settings = get_settings()
    config = AgentConfig(
        agent_id="general",
        name="General Agent",
        description=(
            "A general-purpose agent that can answer"
            " questions and search the web."
        ),
        groq_model_tiers=_parse_tiers(
            settings.groq_model_tiers,
        ),
        temperature=0.0,
        tool_names=["get_current_time", "search_web"],
    )
    return GeneralAgent(
        config=config,
        tool_registry=tool_registry,
        token_budget=token_budget or TokenBudget(),
        compressor=compressor
        or MessageCompressor(
            max_history_turns=settings.max_history_turns,
            max_tool_result_chars=(settings.max_tool_result_chars),
        ),
        obs_collector=obs_collector,
    )
