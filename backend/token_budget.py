"""Sliding-window token and request budget tracker for Groq models.

Tracks tokens-per-minute (TPM), requests-per-minute (RPM),
tokens-per-day (TPD), and requests-per-day (RPD) using
:class:`collections.deque`-backed sliding windows.  Thread-safe
via per-model :class:`threading.Lock`.

Typical usage::

    from token_budget import TokenBudget

    budget = TokenBudget()
    est = budget.estimate_tokens(messages)
    if budget.can_afford("my-model", est):
        # ... invoke LLM ...
        budget.record("my-model", est)
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List

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
_DEFAULT_LIMITS: Dict[str, ModelLimits] = {
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
    """Per-model sliding-window state (internal)."""

    minute_tokens: deque = field(
        default_factory=deque,
    )
    minute_requests: deque = field(
        default_factory=deque,
    )
    day_tokens: deque = field(default_factory=deque)
    day_requests: deque = field(default_factory=deque)
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
        limits: Dict[str, ModelLimits] | None = None,
    ) -> None:
        self._limits: Dict[str, ModelLimits] = {
            **_DEFAULT_LIMITS,
            **(limits or {}),
        }
        self._state: Dict[str, _ModelState] = {}

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
        messages: List[Any],
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
            tpm_used = self._window_total(
                state.minute_tokens,
                _MINUTE,
                now,
            )
            rpm_used = self._window_total(
                state.minute_requests,
                _MINUTE,
                now,
            )
            tpd_used = self._window_total(
                state.day_tokens,
                _DAY,
                now,
            )
            rpd_used = self._window_total(
                state.day_requests,
                _DAY,
                now,
            )

        if tpm_used + estimated_tokens > lim.tpm * _THRESHOLD:
            return False
        if rpm_used + 1 > lim.rpm * _THRESHOLD:
            return False
        if tpd_used + estimated_tokens > lim.tpd * _THRESHOLD:
            return False
        if rpd_used + 1 > lim.rpd * _THRESHOLD:
            return False
        return True

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
            state.minute_requests.append((now, 1))
            state.day_tokens.append((now, tokens_used))
            state.day_requests.append((now, 1))

    def best_available(
        self,
        estimated_tokens: int,
        prefer: str,
        fallbacks: List[str] | None = None,
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

    def get_status(self) -> Dict[str, Dict[str, str]]:
        """Return human-readable utilization per model.

        Returns:
            ``{model: {tpm: "1234/8000", ...}}``.
        """
        result: Dict[str, Dict[str, str]] = {}
        now = time.monotonic()
        for model, lim in self._limits.items():
            state = self._get_state(model)
            with state.lock:
                tpm = self._window_total(
                    state.minute_tokens,
                    _MINUTE,
                    now,
                )
                rpm = self._window_total(
                    state.minute_requests,
                    _MINUTE,
                    now,
                )
                tpd = self._window_total(
                    state.day_tokens,
                    _DAY,
                    now,
                )
                rpd = self._window_total(
                    state.day_requests,
                    _DAY,
                    now,
                )
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
    ) -> int:
        """Sum values within the sliding window.

        Prunes expired entries from the left of *log*.

        Args:
            log: Deque of ``(timestamp, count)`` tuples.
            window_seconds: Window size in seconds.
            now: Current monotonic time.

        Returns:
            Total count within the window.
        """
        cutoff = now - window_seconds
        while log and log[0][0] < cutoff:
            log.popleft()
        return sum(count for _, count in log)
