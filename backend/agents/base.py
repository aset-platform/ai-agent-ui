"""Abstract base classes and data structures for the multi-agent framework.

This module defines two public symbols:

:class:`AgentConfig`
    A plain :func:`~dataclasses.dataclass` that carries every piece of
    configuration an agent needs: its identity, the underlying LLM model name,
    sampling temperature, an optional system prompt, and the list of tool names
    it is permitted to use.

:class:`BaseAgent`
    An :class:`~abc.ABC` that implements the full **agentic loop**:

    1. Convert the raw ``history`` list (plain dicts from the HTTP layer) into
       typed LangChain :class:`~langchain_core.messages.BaseMessage` objects.
    2. Invoke the LLM (with tools bound).
    3. If the model returned tool calls, execute each one via
       :class:`~tools.registry.ToolRegistry`, append a
       :class:`~langchain_core.messages.ToolMessage`, and repeat from step 2.
    4. When the model returns a response with no tool calls, return
       :attr:`~langchain_core.messages.AIMessage.content` as the final answer.

Concrete agent implementations (e.g. :class:`~agents.general_agent.GeneralAgent`)
only need to implement :meth:`BaseAgent._build_llm` to return a provider-specific
chat model.  All loop logic, message formatting, and logging live here.

Typical usage::

    # (You would normally use a concrete subclass, not BaseAgent directly.)
    from agents.general_agent import GeneralAgent, create_general_agent
    from tools.registry import ToolRegistry

    registry = ToolRegistry()
    agent = create_general_agent(registry)
    response = agent.run("What time is it?", history=[])
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, BaseMessage, SystemMessage

from tools.registry import ToolRegistry


@dataclass
class AgentConfig:
    """Immutable configuration bundle for a single agent instance.

    Attributes:
        agent_id: Unique string identifier for routing and logging,
            e.g. ``"general"`` or ``"code-reviewer"``.
        name: Human-readable display name, e.g. ``"General Agent"``.
        description: One-sentence description exposed via the
            ``GET /agents`` endpoint so callers can choose an agent.
        model: LLM model identifier passed to the provider SDK,
            e.g. ``"openai/gpt-oss-120b"`` or ``"claude-sonnet-4-6"``.
        temperature: Sampling temperature.  ``0.0`` (default) produces
            deterministic outputs; higher values increase creativity.
        system_prompt: Optional system message prepended to every
            conversation.  Empty string means no system message.
        tool_names: Names of tools (as registered in
            :class:`~tools.registry.ToolRegistry`) this agent may call.
            An empty list means the agent has no tools.
    """

    agent_id: str
    name: str
    description: str
    model: str
    temperature: float = 0.0
    system_prompt: str = ""
    tool_names: list[str] = field(default_factory=list)


class BaseAgent(ABC):
    """Abstract base class implementing the provider-agnostic agentic loop.

    Subclasses must implement :meth:`_build_llm` to return a concrete
    LangChain chat model.  The loop, message conversion, tool dispatch,
    and structured logging are handled here.

    Attributes:
        config: The :class:`AgentConfig` used to construct this agent.
        tool_registry: The shared :class:`~tools.registry.ToolRegistry`
            that provides tool lookup and invocation.
        logger: A :class:`logging.Logger` named ``agent.<agent_id>``.
        llm: The raw (unbound) LLM instance returned by :meth:`_build_llm`.
        llm_with_tools: The LLM with tools bound via
            :meth:`~langchain_core.language_models.BaseChatModel.bind_tools`.
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
        # Logger name includes agent_id so log lines are filterable per agent.
        self.logger = logging.getLogger(f"agent.{config.agent_id}")
        self._setup()

    def _setup(self) -> None:
        """Build the LLM and bind the agent's permitted tools to it.

        Called automatically by :meth:`__init__`.  Fetches tools from the
        registry by the names listed in :attr:`AgentConfig.tool_names` and
        passes them to :meth:`~langchain_core.language_models.BaseChatModel.bind_tools`
        so the LLM is aware of their schemas.
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

        Subclasses must override this method to return a concrete LangChain
        chat model (e.g. :class:`~langchain_groq.ChatGroq` or
        :class:`~langchain_anthropic.ChatAnthropic`).  The returned object
        must implement
        :meth:`~langchain_core.language_models.BaseChatModel.bind_tools`.

        Returns:
            An uninvoked LangChain chat model instance.
        """
        ...

    def _build_messages(
        self, user_input: str, history: list[dict]
    ) -> list[BaseMessage]:
        """Convert a raw conversation history and new user input into LangChain messages.

        The ``history`` list comes from the HTTP request body as plain dicts
        with ``"role"`` and ``"content"`` keys.  Only ``"user"`` and
        ``"assistant"`` roles are recognised; any other role is silently
        dropped.

        The new ``user_input`` is always appended as the final message.

        Args:
            user_input: The latest message from the user.
            history: Prior conversation turns in ``[{"role": ..., "content": ...}, ...]``
                format, oldest first.

        Returns:
            An ordered list of :class:`~langchain_core.messages.BaseMessage`
            objects ready to be passed to the LLM.
        """
        messages: list[BaseMessage] = []
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

    def run(self, user_input: str, history: list[dict] = []) -> str:
        """Execute the agentic loop and return the LLM's final text response.

        The loop proceeds as follows:

        1. Build the initial message list from ``history`` + ``user_input``.
        2. Invoke ``llm_with_tools``.
        3. If the response contains tool calls, execute each via
           :class:`~tools.registry.ToolRegistry`, append a
           :class:`~langchain_core.messages.ToolMessage` per call, and
           repeat from step 2.
        4. When the response contains no tool calls, return
           :attr:`~langchain_core.messages.AIMessage.content`.

        Args:
            user_input: The user's latest message.
            history: Prior conversation turns as raw dicts
                (``[{"role": "user"|"assistant", "content": "..."}]``).
                Defaults to an empty list (single-turn conversation).

        Returns:
            The final natural-language response from the LLM, or the
            string ``"No response"`` if the model returned an empty
            content field.

        Raises:
            Exception: Any exception raised by the LLM provider or tool
                execution is logged with a full traceback at ``ERROR``
                level and then re-raised to the caller.

        Example:
            >>> agent = create_general_agent(tool_registry)
            >>> reply = agent.run("What time is it?")
            >>> isinstance(reply, str)
            True
        """
        self.logger.info(
            "Request start | agent=%s | input_len=%d",
            self.config.agent_id,
            len(user_input),
        )
        messages = self._build_messages(user_input, history)
        iteration = 0

        try:
            while True:
                iteration += 1
                self.logger.debug(
                    "Iteration %d | message_count=%d", iteration, len(messages)
                )
                response = self.llm_with_tools.invoke(messages)
                messages.append(response)

                # An empty tool_calls list signals that the LLM is done.
                if not response.tool_calls:
                    break

                tool_names_called = [tc["name"] for tc in response.tool_calls]
                self.logger.info("Tools called: %s", tool_names_called)

                for tc in response.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc.get("args", {})
                    self.logger.debug("Tool args | %s: %s", tool_name, tool_args)

                    result = self.tool_registry.invoke(tool_name, tool_args)
                    # Truncate in the log; the LLM receives the full result.
                    self.logger.debug(
                        "Tool result | %s: %s", tool_name, result[:300]
                    )
                    # LangChain requires each ToolMessage to reference its
                    # originating tool call via tool_call_id.
                    messages.append(
                        ToolMessage(content=result, tool_call_id=tc["id"])
                    )

        except Exception:
            self.logger.error("Agent run failed", exc_info=True)
            raise

        self.logger.info(
            "Request end | agent=%s | iterations=%d",
            self.config.agent_id,
            iteration,
        )
        return response.content or "No response"
