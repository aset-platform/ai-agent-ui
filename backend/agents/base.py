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

from __future__ import annotations

import logging
from abc import ABC
from typing import Iterator

import agents.loop as _loop
import agents.stream as _stream
from agents.config import AgentConfig
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from tools.registry import ToolRegistry


class BaseAgent(ABC):
    """Abstract base class implementing the provider-agnostic agentic loop.

    Subclasses must implement :meth:`_build_llm` to return a concrete
    LangChain chat model (or duck-typed equivalent like
    :class:`~llm_fallback.FallbackLLM`).

    Attributes:
        config: The :class:`~agents.config.AgentConfig` for this agent.
        tool_registry: The shared :class:`~tools.registry.ToolRegistry`.
        token_budget: Shared :class:`~token_budget.TokenBudget` tracker.
        compressor: Shared :class:`~message_compressor.MessageCompressor`.
        logger: Logger named ``agent.<agent_id>``.
        llm: The raw (unbound) LLM instance.
        llm_with_tools: The LLM with tools bound.
    """

    def __init__(
        self,
        config: AgentConfig,
        tool_registry: ToolRegistry,
        token_budget=None,
        compressor=None,
        obs_collector=None,
    ) -> None:
        """Initialise the agent and bind tools to the LLM.

        Args:
            config: Configuration bundle for this agent.
            tool_registry: Registry from which the agent's
                allowed tools are fetched by name.
            token_budget: Shared
                :class:`~token_budget.TokenBudget`.
            compressor: Shared
                :class:`~message_compressor.MessageCompressor`.
            obs_collector: Optional
                :class:`~observability.ObservabilityCollector`.
        """
        self.config = config
        self.tool_registry = tool_registry
        self.logger = logging.getLogger(f"agent.{config.agent_id}")

        # Set dependencies BEFORE _setup() calls _build_llm().
        if token_budget is None:
            from token_budget import TokenBudget

            token_budget = TokenBudget()
        if compressor is None:
            from message_compressor import MessageCompressor

            compressor = MessageCompressor()
        self.token_budget = token_budget
        self.compressor = compressor
        self.obs_collector = obs_collector

        self._setup()

    def _setup(self) -> None:
        """Build LLMs and bind the agent's permitted tools.

        Creates two LLM instances:
        - ``llm_with_tools`` — tool-calling cascade (used during
          agentic loop iterations with tool calls).
        - ``llm_synthesis`` — synthesis cascade (used for the
          final response when no more tool calls).  Falls back to
          ``llm_with_tools`` if not overridden.

        Called automatically by :meth:`__init__`.
        """
        self.llm = self._build_llm()
        tools = self.tool_registry.get_tools(
            self.config.tool_names,
        )
        self.llm_with_tools = self.llm.bind_tools(tools)

        # Synthesis LLM (optional override).
        syn = self._build_synthesis_llm()
        if syn is not None:
            self.llm_synthesis = syn.bind_tools(tools)
        else:
            self.llm_synthesis = self.llm_with_tools

        self.logger.debug(
            "Agent '%s' setup complete. Bound tools: %s",
            self.config.agent_id,
            self.config.tool_names,
        )

    def _build_llm(self):
        """Construct the tool-calling LLM cascade.

        Default implementation builds a
        :class:`~llm_fallback.FallbackLLM` with the
        standard Groq cascade + Anthropic fallback.
        Subclasses may override for custom behaviour.

        Returns:
            An uninvoked LangChain chat model instance (or duck-typed
            equivalent with ``bind_tools``/``invoke`` methods).
        """
        from config import get_settings
        from llm_fallback import FallbackLLM

        settings = get_settings()
        is_test = settings.ai_agent_ui_env == "test"

        def _parse(csv: str) -> list[str]:
            return [t.strip() for t in csv.split(",") if t.strip()]

        tiers = (
            _parse(settings.test_model_tiers)
            if is_test
            else self.config.groq_model_tiers
        )
        return FallbackLLM(
            groq_models=tiers,
            anthropic_model=(None if is_test else "claude-sonnet-4-6"),
            temperature=self.config.temperature,
            agent_id=self.config.agent_id,
            token_budget=self.token_budget,
            compressor=self.compressor,
            obs_collector=self.obs_collector,
            cascade_profile=("test" if is_test else "tool"),
        )

    def _build_synthesis_llm(self):
        """Construct the synthesis LLM cascade.

        Returns a quality-optimised cascade for
        final responses, or ``None`` in test mode.

        Returns:
            An LLM instance, or ``None``.
        """
        from config import get_settings
        from llm_fallback import FallbackLLM

        settings = get_settings()
        if settings.ai_agent_ui_env == "test":
            return None

        def _parse(csv: str) -> list[str]:
            return [t.strip() for t in csv.split(",") if t.strip()]

        return FallbackLLM(
            groq_models=_parse(
                settings.synthesis_model_tiers,
            ),
            anthropic_model="claude-sonnet-4-6",
            temperature=self.config.temperature,
            agent_id=self.config.agent_id,
            token_budget=self.token_budget,
            compressor=self.compressor,
            obs_collector=self.obs_collector,
            cascade_profile="synthesis",
        )

    def _build_messages(
        self,
        user_input: str,
        history: list[dict],
        session_id: str = "",
    ) -> list[BaseMessage]:
        """Convert raw history and user input into LangChain messages.

        Args:
            user_input: The latest message from the user.
            history: Prior conversation turns as
                ``[{"role": "user"|"assistant",
                "content": "..."}]``.
            session_id: Optional session identifier used to
                inject conversation context into the system
                prompt.  Defaults to ``""`` (no injection).

        Returns:
            Ordered list of BaseMessage objects.
        """
        messages: list[BaseMessage] = []

        # Build system prompt with context injection.
        system = self.config.system_prompt or ""
        if session_id:
            try:
                from agents.conversation_context import (
                    context_store,
                )

                ctx = context_store.get(session_id)
                if ctx and ctx.summary:
                    context_block = (
                        "[Conversation Context]\n"
                        f"Turn {ctx.turn_count} of an "
                        "ongoing conversation.\n"
                        f"Summary: {ctx.summary}\n"
                        f"Current topic: "
                        f"{ctx.current_topic}\n"
                    )
                    if ctx.user_tickers:
                        tickers = ", ".join(
                            ctx.user_tickers,
                        )
                        context_block += (
                            f"User portfolio: {tickers}\n"
                        )
                    if ctx.market_preference:
                        context_block += (
                            f"Market: "
                            f"{ctx.market_preference}\n"
                        )
                    context_block += "\n---\n"
                    system = context_block + system
            except Exception:
                pass  # Context injection is best-effort

        if system:
            messages.append(
                SystemMessage(content=system),
            )

        for msg in history:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "user":
                messages.append(
                    HumanMessage(content=content),
                )
            elif role == "assistant":
                messages.append(
                    AIMessage(content=content),
                )
        messages.append(
            HumanMessage(content=user_input),
        )
        return messages

    def run(
        self,
        user_input: str,
        history: list[dict] | None = None,
        max_iterations: int | None = None,
    ) -> str:
        """Execute the agentic loop and return the final text response.

        Args:
            user_input: The user's latest message.
            history: Prior conversation turns as raw dicts.
            max_iterations: Override the default iteration
                cap (default :data:`MAX_ITERATIONS` = 15).

        Returns:
            The final natural-language response.

        Raises:
            Exception: Any LLM or tool exception is re-raised.
        """
        history = history or []
        return _loop.run(self, user_input, history, max_iterations)

    def stream(
        self,
        user_input: str,
        history: list[dict] | None = None,
    ) -> Iterator[str]:
        """Execute the agentic loop, yielding NDJSON status events.

        Args:
            user_input: The user's latest message.
            history: Prior conversation turns as raw dicts.

        Yields:
            JSON-encoded event strings, each terminated with ``\\n``.

        Raises:
            Exception: Re-raised after yielding an error event.
        """
        history = history or []
        return _stream.stream(self, user_input, history)
