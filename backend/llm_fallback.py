"""Groq-first / Anthropic-fallback LLM wrapper.

Provides :class:`FallbackLLM`, a duck-typed LLM that tries Groq first
and falls back to Anthropic on rate-limit or connection errors.  When
``GROQ_API_KEY`` is not set, Groq is skipped entirely and Anthropic is
used as the sole provider.
"""

import logging
import os
from typing import Any, List, Optional

from langchain_anthropic import ChatAnthropic

# Module-level logger; kept here as logging.getLogger is the standard pattern.
_logger = logging.getLogger(__name__)

# Groq imports are optional — only needed when GROQ_API_KEY is set.
try:
    from groq import APIConnectionError, RateLimitError
    from langchain_groq import ChatGroq

    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False


class FallbackLLM:
    """Groq-first chat model that falls back to Anthropic on transient errors.

    When ``GROQ_API_KEY`` is not set (or the ``groq`` package is not
    installed), Groq is skipped entirely and all requests go directly to
    Anthropic.

    Implements the duck-typed subset of
    :class:`~langchain_core.language_models.BaseChatModel` used by
    :class:`~agents.base.BaseAgent`: :meth:`bind_tools` and :meth:`invoke`.

    Attributes:
        _groq_llm: Raw :class:`~langchain_groq.ChatGroq` instance, or
            ``None`` when Groq is unavailable.
        _anthropic_llm: Raw ChatAnthropic instance.
        _groq_bound: Groq LLM with tools bound via :meth:`bind_tools`.
        _anthropic_bound: Anthropic LLM with tools bound via
            :meth:`bind_tools`.
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

        Groq is only instantiated when ``GROQ_API_KEY`` is present in the
        environment.  Otherwise, all calls go directly to Anthropic.

        Args:
            groq_model: Model name for ChatGroq.
            anthropic_model: Model name for ChatAnthropic.
            temperature: Sampling temperature shared by both inner LLMs.
            agent_id: Agent identifier for log messages.
        """
        self._groq_llm: Optional[Any] = None
        self._groq_bound: Optional[Any] = None

        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if _GROQ_AVAILABLE and groq_key:
            self._groq_llm = ChatGroq(
                model=groq_model,
                temperature=temperature,
            )
            self._groq_bound = self._groq_llm
            _logger.info("FallbackLLM: Groq enabled (agent=%s)", agent_id)
        else:
            _logger.info(
                "FallbackLLM: Groq unavailable (no GROQ_API_KEY) — "
                "using Anthropic only (agent=%s)",
                agent_id,
            )

        self._anthropic_llm = ChatAnthropic(
            model=anthropic_model, temperature=temperature
        )
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
            **kwargs: Extra keyword arguments forwarded to
                the inner ``bind_tools`` calls.

        Returns:
            This :class:`FallbackLLM` instance with both inner LLMs re-bound.
        """
        if self._groq_llm is not None:
            self._groq_bound = self._groq_llm.bind_tools(tools, **kwargs)
        self._anthropic_bound = self._anthropic_llm.bind_tools(tools, **kwargs)
        return self

    def invoke(self, messages: List[Any], **kwargs: Any) -> Any:
        """Invoke Groq; fall back to Anthropic on rate-limit or connection errors.

        When Groq is not configured, Anthropic is called directly without
        any fallback attempt.

        Args:
            messages: Ordered list of LangChain
                :class:`~langchain_core.messages.BaseMessage` objects.
            **kwargs: Extra keyword arguments forwarded to
                the inner ``invoke`` call.

        Returns:
            An :class:`~langchain_core.messages.AIMessage` from whichever
            provider responded successfully.

        Raises:
            Exception: Re-raised if both Groq and Anthropic fail.
        """
        if self._groq_bound is not None:
            try:
                return self._groq_bound.invoke(messages, **kwargs)
            except (RateLimitError, APIConnectionError) as exc:
                _logger.warning(
                    "Groq failed (%s), falling back to Anthropic — agent=%s",
                    exc,
                    self._agent_id,
                )
        return self._anthropic_bound.invoke(messages, **kwargs)
