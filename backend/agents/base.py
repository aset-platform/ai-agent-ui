"""Abstract base class for the multi-agent framework.

:class:`BaseAgent` implements the provider-agnostic agentic loop, delegating
to :func:`~agents.loop.run` and :func:`~agents.stream.stream`.  Subclasses
only need to implement :meth:`_build_llm`.

Typical usage::

    from agents.general_agent import GeneralAgent, create_general_agent
    from tools.registry import ToolRegistry

    registry = ToolRegistry()
    agent = create_general_agent(registry)
    response = agent.run("What time is it?", history=[])
"""

import logging
from abc import ABC, abstractmethod
from typing import Iterator, List, Dict

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

from agents.config import AgentConfig, MAX_ITERATIONS
from tools.registry import ToolRegistry

import agents.loop as _loop
import agents.stream as _stream


class BaseAgent(ABC):
    """Abstract base class implementing the provider-agnostic agentic loop.

    Subclasses must implement :meth:`_build_llm` to return a concrete
    LangChain chat model (or duck-typed equivalent like
    :class:`~llm_fallback.FallbackLLM`).

    Attributes:
        config: The :class:`~agents.config.AgentConfig` for this agent.
        tool_registry: The shared :class:`~tools.registry.ToolRegistry`.
        logger: Logger named ``agent.<agent_id>``.
        llm: The raw (unbound) LLM instance.
        llm_with_tools: The LLM with tools bound.
    """

    def __init__(self, config: AgentConfig, tool_registry: ToolRegistry) -> None:
        """Initialise the agent and bind tools to the LLM.

        Args:
            config: Configuration bundle for this agent.
            tool_registry: Registry from which the agent's allowed tools
                are fetched by name.
        """
        self.config = config
        self.tool_registry = tool_registry
        self.logger = logging.getLogger(f"agent.{config.agent_id}")
        self._setup()

    def _setup(self) -> None:
        """Build the LLM and bind the agent's permitted tools to it.

        Called automatically by :meth:`__init__`.
        """
        self.llm = self._build_llm()
        tools = self.tool_registry.get_tools(self.config.tool_names)
        self.llm_with_tools = self.llm.bind_tools(tools)
        self.logger.debug(
            "Agent '%s' setup complete. Bound tools: %s",
            self.config.agent_id,
            self.config.tool_names,
        )

    @abstractmethod
    def _build_llm(self):
        """Construct and return the provider-specific chat model.

        Returns:
            An uninvoked LangChain chat model instance (or duck-typed
            equivalent with ``bind_tools`` and ``invoke`` methods).
        """
        ...

    def _build_messages(self, user_input: str, history: List[Dict]) -> List[BaseMessage]:
        """Convert raw conversation history and user input into LangChain messages.

        Args:
            user_input: The latest message from the user.
            history: Prior conversation turns as
                ``[{"role": "user"|"assistant", "content": "..."}]``.

        Returns:
            An ordered list of :class:`~langchain_core.messages.BaseMessage` objects.
        """
        messages: List[BaseMessage] = []
        if self.config.system_prompt:
            messages.append(SystemMessage(content=self.config.system_prompt))
        for msg in history:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
        messages.append(HumanMessage(content=user_input))
        return messages

    def run(self, user_input: str, history: List[Dict] = []) -> str:
        """Execute the agentic loop and return the final text response.

        Args:
            user_input: The user's latest message.
            history: Prior conversation turns as raw dicts.

        Returns:
            The final natural-language response.

        Raises:
            Exception: Any exception raised by the LLM or tools is re-raised.
        """
        return _loop.run(self, user_input, history)

    def stream(self, user_input: str, history: List[Dict] = []) -> Iterator[str]:
        """Execute the agentic loop, yielding NDJSON status events.

        Args:
            user_input: The user's latest message.
            history: Prior conversation turns as raw dicts.

        Yields:
            JSON-encoded event strings, each terminated with ``\\n``.

        Raises:
            Exception: Any exception is re-raised after yielding an error event.
        """
        return _stream.stream(self, user_input, history)
