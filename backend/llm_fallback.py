import logging
from typing import Any, List

from groq import APIConnectionError, RateLimitError
from langchain_anthropic import ChatAnthropic
from langchain_groq import ChatGroq

# Module-level logger; kept here as logging.getLogger is the standard pattern.
_logger = logging.getLogger(__name__)


class FallbackLLM:
    """Groq-first chat model that falls back to Anthropic on transient errors.

    Implements the duck-typed subset of
    :class:`~langchain_core.language_models.BaseChatModel` used by
    :class:`~agents.base.BaseAgent`: :meth:`bind_tools` and :meth:`invoke`.

    Attributes:
        _groq_llm: Raw :class:`~langchain_groq.ChatGroq` instance.
        _anthropic_llm: Raw :class:`~langchain_anthropic.ChatAnthropic` instance.
        _groq_bound: Groq LLM with tools bound (set by :meth:`bind_tools`).
        _anthropic_bound: Anthropic LLM with tools bound (set by :meth:`bind_tools`).
        _agent_id: Agent identifier used in warning log messages.
    """

    def __init__(
        self,
        groq_model: str,
        anthropic_model: str,
        temperature: float,
        agent_id: str,
    ) -> None:
        """Construct both inner LLMs.

        Args:
            groq_model: Model name passed to :class:`~langchain_groq.ChatGroq`.
            anthropic_model: Model name passed to :class:`~langchain_anthropic.ChatAnthropic`.
            temperature: Sampling temperature shared by both inner LLMs.
            agent_id: Agent identifier used in warning log messages.
        """
        self._groq_llm = ChatGroq(model=groq_model, temperature=temperature)
        self._anthropic_llm = ChatAnthropic(
            model=anthropic_model, temperature=temperature
        )
        self._groq_bound: Any = self._groq_llm
        self._anthropic_bound: Any = self._anthropic_llm
        self._agent_id = agent_id

    def bind_tools(self, tools: List[Any], **kwargs: Any) -> "FallbackLLM":
        """Bind tools to both inner LLMs and return *self*.

        :class:`~agents.base.BaseAgent` assigns the return value of
        ``llm.bind_tools(tools)`` to ``self.llm_with_tools``.  Returning
        ``self`` ensures that the same :class:`FallbackLLM` instance is used
        for invocation, preserving the fallback behaviour.

        Args:
            tools: LangChain tool objects to bind.
            **kwargs: Extra keyword arguments forwarded to both inner
                :meth:`~langchain_core.language_models.BaseChatModel.bind_tools` calls.

        Returns:
            This :class:`FallbackLLM` instance with both inner LLMs re-bound.
        """
        self._groq_bound = self._groq_llm.bind_tools(tools, **kwargs)
        self._anthropic_bound = self._anthropic_llm.bind_tools(tools, **kwargs)
        return self

    def invoke(self, messages: List[Any], **kwargs: Any) -> Any:
        """Invoke Groq; fall back to Anthropic on rate-limit or connection errors.

        Args:
            messages: Ordered list of LangChain
                :class:`~langchain_core.messages.BaseMessage` objects.
            **kwargs: Extra keyword arguments forwarded to the inner
                :meth:`~langchain_core.language_models.BaseChatModel.invoke` call.

        Returns:
            An :class:`~langchain_core.messages.AIMessage` from whichever
            provider responded successfully.

        Raises:
            Exception: Re-raised if both Groq and Anthropic fail.
        """
        try:
            return self._groq_bound.invoke(messages, **kwargs)
        except (RateLimitError, APIConnectionError) as exc:
            _logger.warning(
                "Groq failed (%s), falling back to Anthropic — agent=%s",
                exc,
                self._agent_id,
            )
            return self._anthropic_bound.invoke(messages, **kwargs)
