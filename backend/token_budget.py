"""Sliding-window token and request budget tracker for Groq models.

Tracks tokens-per-minute (TPM), requests-per-minute (RPM),
tokens-per-day (TPD), and requests-per-day (RPD) using
:class:`collections.deque`-backed sliding windows.  Thread-safe
via per-model :class:`threading.Lock`.

Typical usage::

    from token_budget import TokenBudget

    budget = TokenBudget()
    est = budget.estimate_tokens(messages)
    if budget.reserve("my-model", est):
        try:
            result = llm.invoke(messages)
        except Exception:
            budget.release("my-model", est)
            raise
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from langsmith import traceable

_logger = logging.getLogger(__name__)

# Window durations in seconds.
_MINUTE = 60
_DAY = 86_400

# Pre-emptive threshold — request budget at 80 % capacity.
_THRESHOLD = 0.80


@dataclass(frozen=True)
class ModelLimits:
    """Rate limits for a single Groq model.

    Attributes:
        rpm: Requests per minute.
        tpm: Tokens per minute.
        rpd: Requests per day.
        tpd: Tokens per day.
    """

    rpm: int
    tpm: int
    rpd: int
    tpd: int


# Hardcoded free-tier defaults (March 2026).
_DEFAULT_LIMITS: dict[str, ModelLimits] = {
    "meta-llama/llama-4-scout-17b-16e-instruct": ModelLimits(
        rpm=30,
        tpm=30_000,
        rpd=1_000,
        tpd=500_000,
    ),
    "openai/gpt-oss-120b": ModelLimits(
        rpm=30,
        tpm=8_000,
        rpd=1_000,
        tpd=200_000,
    ),
    "llama-3.3-70b-versatile": ModelLimits(
        rpm=30,
        tpm=12_000,
        rpd=1_000,
        tpd=100_000,
    ),
    "llama-3.1-8b-instant": ModelLimits(
        rpm=30,
        tpm=6_000,
        rpd=14_400,
        tpd=500_000,
    ),
    "qwen/qwen3-32b": ModelLimits(
        rpm=60,
        tpm=6_000,
        rpd=1_000,
        tpd=500_000,
    ),
    "moonshotai/kimi-k2-instruct": ModelLimits(
        rpm=60,
        tpm=10_000,
        rpd=1_000,
        tpd=300_000,
    ),
}

# Safety margin multiplier applied to token estimates.
_ESTIMATE_MARGIN = 1.20


@dataclass
class _ModelState:
    """Per-model sliding-window state (internal).

    Each deque is paired with a running total so that
    :meth:`TokenBudget._window_total` can return O(1)
    instead of summing the deque on every call.
    """

    minute_tokens: deque = field(
        default_factory=deque,
    )
    minute_tokens_total: int = 0
    minute_requests: deque = field(
        default_factory=deque,
    )
    minute_requests_total: int = 0
    day_tokens: deque = field(default_factory=deque)
    day_tokens_total: int = 0
    day_requests: deque = field(default_factory=deque)
    day_requests_total: int = 0
    lock: threading.Lock = field(
        default_factory=threading.Lock,
    )


class TokenBudget:
    """Sliding-window rate tracker for multiple Groq models.

    All public methods are thread-safe.

    Args:
        limits: Optional per-model limits override.  Models not
            present fall back to :data:`_DEFAULT_LIMITS`.
    """

    def __init__(
        self,
        limits: dict[str, ModelLimits] | None = None,
    ) -> None:
        self._limits: dict[str, ModelLimits] = {
            **_DEFAULT_LIMITS,
            **(limits or {}),
        }
        # Pre-allocate state for all known models.
        self._state: dict[str, _ModelState] = {
            model: _ModelState() for model in self._limits
        }

    def get_tpm(self, model: str) -> int | None:
        """Return the TPM limit for *model*, or ``None``.

        Args:
            model: Groq model identifier.

        Returns:
            Tokens-per-minute limit, or ``None`` if unknown.
        """
        lim = self._limits.get(model)
        return lim.tpm if lim is not None else None

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_tokens(
        messages: list[Any],
    ) -> int:
        """Estimate token count for a message list.

        Uses the heuristic ``len(text) // 4`` with a 20 %
        safety margin.  Accurate within ~15 % for English.

        Args:
            messages: LangChain BaseMessage objects.

        Returns:
            Estimated token count (int).
        """
        total_chars = 0
        for msg in messages:
            content = getattr(msg, "content", "") or ""
            if isinstance(content, str):
                total_chars += len(content)
            # AIMessage may carry tool_calls as structured data.
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    args = tc.get("args", {})
                    total_chars += len(str(args))
        raw = total_chars // 4
        return int(raw * _ESTIMATE_MARGIN)

    # ------------------------------------------------------------------
    # Budget queries
    # ------------------------------------------------------------------

    def can_afford(self, model: str, estimated_tokens: int) -> bool:
        """Check whether *model* has budget for *estimated_tokens*.

        Uses an 80 % threshold on every limit dimension so we
        leave headroom and avoid 429s.

        Args:
            model: Groq model identifier.
            estimated_tokens: Pre-computed token estimate.

        Returns:
            ``True`` if all four limits have room.
        """
        lim = self._limits.get(model)
        if lim is None:
            # Unknown model — assume no budget info, allow it.
            return True

        state = self._get_state(model)
        with state.lock:
            now = time.monotonic()
            tpm_used, exp = self._window_total(
                state.minute_tokens,
                _MINUTE,
                now,
                state.minute_tokens_total,
            )
            state.minute_tokens_total = tpm_used
            rpm_used, exp = self._window_total(
                state.minute_requests,
                _MINUTE,
                now,
                state.minute_requests_total,
            )
            state.minute_requests_total = rpm_used
            tpd_used, exp = self._window_total(
                state.day_tokens,
                _DAY,
                now,
                state.day_tokens_total,
            )
            state.day_tokens_total = tpd_used
            rpd_used, exp = self._window_total(
                state.day_requests,
                _DAY,
                now,
                state.day_requests_total,
            )
            state.day_requests_total = rpd_used

        if tpm_used + estimated_tokens > lim.tpm * _THRESHOLD:
            return False
        if rpm_used + 1 > lim.rpm * _THRESHOLD:
            return False
        if tpd_used + estimated_tokens > lim.tpd * _THRESHOLD:
            return False
        if rpd_used + 1 > lim.rpd * _THRESHOLD:
            return False
        return True

    @traceable(name="TokenBudget.reserve")
    def reserve(
        self,
        model: str,
        estimated_tokens: int,
    ) -> bool:
        """Atomically check budget AND record a tentative spend.

        Eliminates the TOCTOU race between separate
        :meth:`can_afford` / :meth:`record` calls by holding the
        lock for both operations.  On LLM failure, call
        :meth:`release` to roll back.

        Args:
            model: Groq model identifier.
            estimated_tokens: Pre-computed token estimate.

        Returns:
            ``True`` if the reservation succeeded.
        """
        lim = self._limits.get(model)
        if lim is None:
            return True

        state = self._get_state(model)
        with state.lock:
            now = time.monotonic()

            tpm_used, _ = self._window_total(
                state.minute_tokens,
                _MINUTE,
                now,
                state.minute_tokens_total,
            )
            state.minute_tokens_total = tpm_used

            rpm_used, _ = self._window_total(
                state.minute_requests,
                _MINUTE,
                now,
                state.minute_requests_total,
            )
            state.minute_requests_total = rpm_used

            tpd_used, _ = self._window_total(
                state.day_tokens,
                _DAY,
                now,
                state.day_tokens_total,
            )
            state.day_tokens_total = tpd_used

            rpd_used, _ = self._window_total(
                state.day_requests,
                _DAY,
                now,
                state.day_requests_total,
            )
            state.day_requests_total = rpd_used

            # Budget check (same thresholds as can_afford).
            if tpm_used + estimated_tokens > lim.tpm * _THRESHOLD:
                return False
            if rpm_used + 1 > lim.rpm * _THRESHOLD:
                return False
            if tpd_used + estimated_tokens > lim.tpd * _THRESHOLD:
                return False
            if rpd_used + 1 > lim.rpd * _THRESHOLD:
                return False

            # Tentatively record the spend while still
            # holding the lock — no other thread can
            # double-spend.
            state.minute_tokens.append(
                (now, estimated_tokens),
            )
            state.minute_tokens_total += estimated_tokens
            state.minute_requests.append((now, 1))
            state.minute_requests_total += 1
            state.day_tokens.append(
                (now, estimated_tokens),
            )
            state.day_tokens_total += estimated_tokens
            state.day_requests.append((now, 1))
            state.day_requests_total += 1

        return True

    def release(
        self,
        model: str,
        estimated_tokens: int,
    ) -> None:
        """Roll back a prior :meth:`reserve` on LLM failure.

        Subtracts the tentative spend so the budget is available
        for the next tier.  Safe to call even if the model has
        no limits entry.

        Args:
            model: Groq model identifier.
            estimated_tokens: Same value passed to
                :meth:`reserve`.
        """
        if model not in self._limits:
            return

        state = self._get_state(model)
        with state.lock:
            state.minute_tokens_total -= estimated_tokens
            state.minute_requests_total -= 1
            state.day_tokens_total -= estimated_tokens
            state.day_requests_total -= 1

    def record(self, model: str, tokens_used: int) -> None:
        """Record a completed request for *model*.

        Args:
            model: Groq model identifier.
            tokens_used: Tokens consumed (estimate is fine).
        """
        state = self._get_state(model)
        now = time.monotonic()
        with state.lock:
            state.minute_tokens.append((now, tokens_used))
            state.minute_tokens_total += tokens_used
            state.minute_requests.append((now, 1))
            state.minute_requests_total += 1
            state.day_tokens.append((now, tokens_used))
            state.day_tokens_total += tokens_used
            state.day_requests.append((now, 1))
            state.day_requests_total += 1

    def best_available(
        self,
        estimated_tokens: int,
        prefer: str,
        fallbacks: list[str] | None = None,
    ) -> str | None:
        """Return the first model that can afford *estimated_tokens*.

        Tries *prefer* first, then each model in *fallbacks*.

        Args:
            estimated_tokens: Token estimate for this call.
            prefer: Preferred model name.
            fallbacks: Ordered fallback model names.

        Returns:
            A model name, or ``None`` if all are exhausted.
        """
        candidates = [prefer] + (fallbacks or [])
        for model in candidates:
            if self.can_afford(model, estimated_tokens):
                return model
        return None

    def get_status(self) -> dict[str, dict[str, str]]:
        """Return human-readable utilization per model.

        Returns:
            ``{model: {tpm: "1234/8000", ...}}``.
        """
        result: dict[str, dict[str, str]] = {}
        now = time.monotonic()
        for model, lim in self._limits.items():
            state = self._get_state(model)
            with state.lock:
                tpm, _ = self._window_total(
                    state.minute_tokens,
                    _MINUTE,
                    now,
                    state.minute_tokens_total,
                )
                state.minute_tokens_total = tpm
                rpm, _ = self._window_total(
                    state.minute_requests,
                    _MINUTE,
                    now,
                    state.minute_requests_total,
                )
                state.minute_requests_total = rpm
                tpd, _ = self._window_total(
                    state.day_tokens,
                    _DAY,
                    now,
                    state.day_tokens_total,
                )
                state.day_tokens_total = tpd
                rpd, _ = self._window_total(
                    state.day_requests,
                    _DAY,
                    now,
                    state.day_requests_total,
                )
                state.day_requests_total = rpd
            result[model] = {
                "tpm": f"{tpm}/{lim.tpm}",
                "rpm": f"{rpm}/{lim.rpm}",
                "tpd": f"{tpd}/{lim.tpd}",
                "rpd": f"{rpd}/{lim.rpd}",
            }
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_state(self, model: str) -> _ModelState:
        """Lazily create per-model state."""
        if model not in self._state:
            self._state[model] = _ModelState()
        return self._state[model]

    @staticmethod
    def _window_total(
        log: deque,
        window_seconds: int,
        now: float,
        running_total: int = 0,
    ) -> tuple[int, int]:
        """Prune expired entries and return the running total.

        Returns the updated running total in O(k) where k is
        the number of expired entries (amortised O(1)).

        Args:
            log: Deque of ``(timestamp, count)`` tuples.
            window_seconds: Window size in seconds.
            now: Current monotonic time.
            running_total: Pre-computed running sum.

        Returns:
            ``(current_total, expired_amount)`` tuple.
        """
        cutoff = now - window_seconds
        expired = 0
        while log and log[0][0] < cutoff:
            _, count = log.popleft()
            expired += count
        return running_total - expired, expired
