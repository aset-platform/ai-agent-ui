"""General-purpose agent with Groq-first / Anthropic-fallback LLM.

:class:`GeneralAgent` is the default agent registered at server startup.
It extends :class:`~agents.base.BaseAgent` and is wired with two tools:
:func:`~tools.time_tool.get_current_time` and :func:`~tools.search_tool.search_web`.

The agent uses :class:`~llm_fallback.FallbackLLM` which tries Groq first and
automatically falls back to Anthropic Claude on rate-limit or connection errors.

Typical usage::

    from tools.registry import ToolRegistry
    from agents.general_agent import create_general_agent

    registry = ToolRegistry()
    # (register tools first)
    agent = create_general_agent(registry)
    reply = agent.run("What is the current time?")
"""

from agents.base import AgentConfig, BaseAgent
from llm_fallback import FallbackLLM
from tools.registry import ToolRegistry


class GeneralAgent(BaseAgent):
    """General-purpose conversational agent with Groq-first / Anthropic-fallback LLM.

    Inherits the complete agentic loop from :class:`~agents.base.BaseAgent`
    and only overrides :meth:`_build_llm` to supply :class:`~llm_fallback.FallbackLLM`.
    """

    def _build_llm(self) -> FallbackLLM:
        """Instantiate and return a :class:`~llm_fallback.FallbackLLM`.

        Groq is tried first; Anthropic is used as fallback on rate-limit or
        connection errors.  Uses the ``model`` and ``temperature`` values from
        :attr:`~agents.base.BaseAgent.config`.

        Returns:
            A :class:`~llm_fallback.FallbackLLM` instance configured with
            the agent's Groq model name, Anthropic model, and temperature.
        """
        return FallbackLLM(
            groq_model=self.config.model,
            anthropic_model="claude-sonnet-4-6",
            temperature=self.config.temperature,
            agent_id=self.config.agent_id,
        )


def create_general_agent(tool_registry: ToolRegistry) -> GeneralAgent:
    """Factory function that builds a :class:`GeneralAgent` with default settings.

    Constructs an :class:`~agents.base.AgentConfig` with the ``"general"``
    agent ID, registers the two default tools, and returns a fully
    initialised :class:`GeneralAgent`.

    Args:
        tool_registry: The shared :class:`~tools.registry.ToolRegistry`
            instance from which ``get_current_time`` and ``search_web``
            will be fetched and bound to the LLM.

    Returns:
        A ready-to-use :class:`GeneralAgent` instance.

    Example:
        >>> from tools.registry import ToolRegistry
        >>> from tools.time_tool import get_current_time
        >>> registry = ToolRegistry()
        >>> registry.register(get_current_time)
        >>> agent = create_general_agent(registry)
        >>> agent.config.agent_id
        'general'
    """
    config = AgentConfig(
        agent_id="general",
        name="General Agent",
        description="A general-purpose agent that can answer questions and search the web.",
        model="openai/gpt-oss-120b",
        temperature=0.0,
        tool_names=["get_current_time", "search_web"],
    )
    return GeneralAgent(config=config, tool_registry=tool_registry)
