"""N-tier Groq/Anthropic LLM cascade with budget-aware routing.

Provides :class:`FallbackLLM`, a duck-typed LLM that routes
requests through an ordered list of Groq models, cascading on
budget exhaustion or API errors, with Anthropic as the final
paid fallback.

Tier order (default)::

    1. llama-3.3-70b-versatile   (12K TPM, parallel tools)
    2. kimi-k2-instruct          (10K TPM, parallel tools)
    3. gpt-oss-120b              (8K TPM, quality)
    4. llama-4-scout-17b         (30K TPM, fast)
    5. claude-sonnet-4-6         (paid, unlimited)

When ``GROQ_API_KEY`` is not set, all Groq tiers are skipped
and requests go directly to Anthropic.

Typical usage::

    from token_budget import TokenBudget
    from message_compressor import MessageCompressor
    from llm_fallback import FallbackLLM

    budget = TokenBudget()
    compressor = MessageCompressor()
    llm = FallbackLLM(
        groq_models=[
            "llama-3.3-70b-versatile",
            "moonshotai/kimi-k2-instruct",
            "openai/gpt-oss-120b",
            "meta-llama/llama-4-scout-17b-16e-instruct",
        ],
        anthropic_model="claude-sonnet-4-6",
        temperature=0.0,
        agent_id="stock",
        token_budget=budget,
        compressor=compressor,
    )
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, List

from langchain_anthropic import ChatAnthropic
from message_compressor import MessageCompressor
from observability import ObservabilityCollector
from token_budget import TokenBudget

_logger = logging.getLogger(__name__)

# Groq imports are optional — only needed when GROQ_API_KEY is set.
try:
    from groq import (
        APIConnectionError,
        APIStatusError,
        RateLimitError,
    )
    from langchain_groq import ChatGroq

    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False


class FallbackLLM:
    """N-tier LLM cascade: Groq models → Anthropic.

    Each Groq model is tried in order.  If a model's budget is
    exhausted, progressive compression is attempted.  On API
    errors (429, 413, connection), the next tier is tried.
    Anthropic is the final fallback.

    Attributes:
        _groq_tiers: Ordered list of ``(name, raw_llm, bound_llm)``
            tuples for each Groq model.
        _anthropic_llm: Raw ChatAnthropic instance.
        _anthropic_bound: Tools-bound ChatAnthropic instance.
        _budget: Shared :class:`TokenBudget` tracker.
        _compressor: Shared :class:`MessageCompressor`.
        _agent_id: Agent identifier for log messages.
    """

    def __init__(
        self,
        groq_models: List[str],
        anthropic_model: str | None,
        temperature: float,
        agent_id: str,
        token_budget: TokenBudget,
        compressor: MessageCompressor,
        obs_collector: ObservabilityCollector | None = None,
        cascade_profile: str = "tool",
    ) -> None:
        """Construct Groq + Anthropic LLM instances.

        Args:
            groq_models: Ordered list of Groq model names,
                tried from first to last.
            anthropic_model: Anthropic model name (final
                fallback).  ``None`` disables Anthropic
                (used by test profile).
            temperature: Sampling temperature for all LLMs.
            agent_id: Agent identifier for logs.
            token_budget: Shared sliding-window budget tracker.
            compressor: Shared message compressor.
            obs_collector: Optional observability metrics
                collector for cascade tracking.
            cascade_profile: Profile name for observability
                (``"tool"``, ``"synthesis"``, ``"test"``).
        """
        self._agent_id = agent_id
        self._budget = token_budget
        self._compressor = compressor
        self._obs = obs_collector
        self._cascade_profile = cascade_profile

        # Groq tiers: [(model_name, raw_llm, bound_llm), ...]
        self._groq_tiers: List[tuple] = []

        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if _GROQ_AVAILABLE and groq_key:
            for model in groq_models:
                llm = ChatGroq(
                    model=model,
                    temperature=temperature,
                    max_retries=0,
                )
                self._groq_tiers.append((model, llm, llm))
            _logger.info(
                "FallbackLLM [%s]: Groq %d tiers: "
                "%s (agent=%s)",
                cascade_profile,
                len(self._groq_tiers),
                [t[0] for t in self._groq_tiers],
                agent_id,
            )
        else:
            _logger.info(
                "FallbackLLM [%s]: Groq unavailable "
                "(no GROQ_API_KEY) — Anthropic only "
                "(agent=%s)",
                cascade_profile,
                agent_id,
            )

        # Anthropic fallback (None = disabled for test).
        self._anthropic_model = anthropic_model
        if anthropic_model:
            self._anthropic_llm = ChatAnthropic(
                model=anthropic_model,
                temperature=temperature,
            )
            self._anthropic_bound: Any = self._anthropic_llm
        else:
            self._anthropic_llm = None
            self._anthropic_bound = None

    def bind_tools(self, tools: List[Any], **kwargs: Any) -> "FallbackLLM":
        """Bind tools to all inner LLMs and return *self*.

        Args:
            tools: LangChain tool objects to bind.
            **kwargs: Extra keyword arguments forwarded to
                each inner ``bind_tools`` call.

        Returns:
            This :class:`FallbackLLM` instance.
        """
        for i, (name, raw, _bound) in enumerate(self._groq_tiers):
            bound = raw.bind_tools(tools, **kwargs)
            self._groq_tiers[i] = (name, raw, bound)

        if self._anthropic_llm is not None:
            self._anthropic_bound = (
                self._anthropic_llm.bind_tools(tools, **kwargs)
            )
        return self

    def invoke(
        self,
        messages: List[Any],
        *,
        iteration: int = 1,
        **kwargs: Any,
    ) -> Any:
        """Route to the best available model with compression.

        Decision flow:

        1. Compress messages (default 3 stages).
        2. Estimate tokens.
        3. For each Groq tier in order: if default compression
           exceeds budget, apply progressive compression
           targeting 70 %% of the model's TPM.  Invoke if
           affordable; on API error, cascade to next tier.
        4. Anthropic as final fallback.

        Args:
            messages: Ordered list of LangChain BaseMessage
                objects.
            iteration: Current agentic loop iteration
                (1-based).
            **kwargs: Extra keyword arguments forwarded to
                the inner ``invoke`` call.

        Returns:
            An AIMessage from whichever provider responded.

        Raises:
            Exception: Re-raised if all providers fail.
        """
        # Step 1: Compress messages (default 3 stages).
        compressed = self._compressor.compress(
            messages,
            iteration,
        )

        # Step 2: Estimate tokens.
        est = self._budget.estimate_tokens(compressed)

        # Step 3: Try each Groq tier in order.
        from tools._ticker_linker import (
            get_current_user,
        )

        _user = get_current_user()
        for idx, (model_name, _raw, bound_llm) in enumerate(
            self._groq_tiers,
        ):
            cur_compressed = compressed
            cur_est = est

            # If default compression exceeds budget, try
            # progressive compression targeting 70% of TPM.
            if not self._budget.can_afford(model_name, cur_est):
                tpm = self._budget.get_tpm(model_name)
                if tpm is not None:
                    target = int(tpm * 0.70)
                    cur_compressed = self._compressor.compress(
                        messages,
                        iteration,
                        target_tokens=target,
                    )
                    cur_est = self._budget.estimate_tokens(cur_compressed)
                    if self._obs:
                        self._obs.record_compression(
                            agent_id=self._agent_id,
                            user_id=_user,
                        )
                    _logger.info(
                        "Progressive compress for %s: "
                        "%d → %d tokens (agent=%s)",
                        model_name,
                        est,
                        cur_est,
                        self._agent_id,
                    )

            if not self._budget.can_afford(model_name, cur_est):
                if self._obs:
                    self._obs.record_cascade(
                        model_name,
                        "budget_exhausted",
                        provider="groq",
                        agent_id=self._agent_id,
                        tier_index=idx,
                        user_id=_user,
                    )
                _logger.info(
                    "Skip %s: budget exhausted " "(est=%d, agent=%s)",
                    model_name,
                    cur_est,
                    self._agent_id,
                )
                continue

            _t0 = time.monotonic()
            try:
                result = bound_llm.invoke(cur_compressed, **kwargs)
                _ms = int(
                    (time.monotonic() - _t0) * 1000,
                )
                self._budget.record(model_name, cur_est)
                # Extract token usage from response.
                _pt = _ct = None
                _umeta = getattr(result, "usage_metadata", None)
                if _umeta:
                    _pt = _umeta.get("input_tokens")
                    _ct = _umeta.get("output_tokens")
                if self._obs:
                    self._obs.record_request(
                        model_name,
                        provider="groq",
                        agent_id=self._agent_id,
                        tier_index=idx,
                        user_id=_user,
                        prompt_tokens=_pt,
                        completion_tokens=_ct,
                        latency_ms=_ms,
                    )
                _logger.info(
                    "Route → %s | iter=%d " "tokens≈%d (agent=%s)",
                    model_name,
                    iteration,
                    cur_est,
                    self._agent_id,
                )
                return result
            except Exception as exc:
                if _GROQ_AVAILABLE and isinstance(
                    exc,
                    (
                        RateLimitError,
                        APIConnectionError,
                        APIStatusError,
                    ),
                ):
                    if self._obs:
                        self._obs.record_cascade(
                            model_name,
                            "api_error",
                            provider="groq",
                            agent_id=self._agent_id,
                            tier_index=idx,
                            user_id=_user,
                        )
                    _logger.warning(
                        "Groq %s failed (%s), " "cascading — agent=%s",
                        model_name,
                        exc,
                        self._agent_id,
                    )
                    continue
                raise

        # Step 4: Anthropic fallback (disabled in test profile).
        if self._anthropic_bound is None:
            _logger.error(
                "All Groq tiers exhausted and Anthropic "
                "disabled [%s] (agent=%s)",
                self._cascade_profile,
                self._agent_id,
            )
            raise RuntimeError(
                f"All free-tier models exhausted "
                f"(profile={self._cascade_profile}). "
                f"No paid fallback available."
            )

        _t0 = time.monotonic()
        result = self._anthropic_bound.invoke(compressed, **kwargs)
        _ms = int((time.monotonic() - _t0) * 1000)
        _pt = _ct = None
        _umeta = getattr(result, "usage_metadata", None)
        if _umeta:
            _pt = _umeta.get("input_tokens")
            _ct = _umeta.get("output_tokens")
        if self._obs:
            self._obs.record_request(
                self._anthropic_model,
                provider="anthropic",
                agent_id=self._agent_id,
                tier_index=len(self._groq_tiers),
                user_id=_user,
                prompt_tokens=_pt,
                completion_tokens=_ct,
                latency_ms=_ms,
            )
        _logger.warning(
            "All Groq tiers exhausted → Anthropic "
            "[%s] | iter=%d tokens≈%d (agent=%s)",
            self._cascade_profile,
            iteration,
            est,
            self._agent_id,
        )
        return result
