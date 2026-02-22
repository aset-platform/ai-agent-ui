"""Concrete general-purpose agent backed by the Groq LLM provider.

:class:`GeneralAgent` is the default agent registered at server startup.
It extends :class:`~agents.base.BaseAgent` and is wired with two tools:
:func:`~tools.time_tool.get_current_time` and :func:`~tools.search_tool.search_web`.

**Switching to Claude Sonnet 4.6** requires only two lines (see inline comment
near :meth:`GeneralAgent._build_llm`) plus setting ``ANTHROPIC_API_KEY`` in the
environment.  Full instructions are in ``CLAUDE.md``.

Typical usage::

    from tools.registry import ToolRegistry
    from agents.general_agent import create_general_agent

    registry = ToolRegistry()
    # (register tools first)
    agent = create_general_agent(registry)
    reply = agent.run("What is the current time?")
"""

# To switch to Claude Sonnet 4.6:
#   1. Change import: from langchain_anthropic import ChatAnthropic
#   2. Change model init: return ChatAnthropic(model=self.config.model, temperature=self.config.temperature)
#   3. Set ANTHROPIC_API_KEY instead of GROQ_API_KEY in the environment.
#   See CLAUDE.md for full details.

from langchain_groq import ChatGroq

from agents.base import AgentConfig, BaseAgent
from tools.registry import ToolRegistry


class GeneralAgent(BaseAgent):
    """General-purpose conversational agent powered by ChatGroq.

    Inherits the complete agentic loop from :class:`~agents.base.BaseAgent`
    and only overrides :meth:`_build_llm` to supply the Groq provider.
    """

    def _build_llm(self) -> ChatGroq:
        """Instantiate and return a :class:`~langchain_groq.ChatGroq` model.

        Uses the ``model`` and ``temperature`` values from
        :attr:`~agents.base.BaseAgent.config`.

        Returns:
            A :class:`~langchain_groq.ChatGroq` instance configured with
            the agent's model name and sampling temperature.

        Note:
            **Temporary** — using Groq until Anthropic API access is
            resolved.  See the module-level comment for the two-line swap
            to :class:`~langchain_anthropic.ChatAnthropic`.
        """
        # Temporary: using Groq until Anthropic API access is resolved.
        return ChatGroq(model=self.config.model, temperature=self.config.temperature)


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
