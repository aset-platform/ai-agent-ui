"""LLM cascade observability metrics collector.

Tracks per-model request counts, cascade events, and
compression triggers for the admin observability dashboard.

Thread-safe — all mutations use a :class:`threading.Lock`.

Typical usage::

    from observability import ObservabilityCollector

    collector = ObservabilityCollector()
    collector.record_request("llama-3.3-70b-versatile")
    collector.record_cascade(
        "llama-3.3-70b-versatile",
        "budget_exhausted",
    )
    print(collector.get_stats())
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Dict

_logger = logging.getLogger(__name__)

# Keep last 1000 cascade events in memory.
_MAX_EVENTS = 1_000

# Window for per-minute request rate tracking.
_MINUTE = 60


class ObservabilityCollector:
    """Thread-safe cascade and request metrics collector.

    Attributes:
        _lock: Guard for all mutable state.
        _requests_by_model: Cumulative request count per model.
        _cascade_count: Total cascade events.
        _compression_count: Compression trigger count.
        _cascade_log: Recent cascade events (bounded deque).
        _requests_minute: Sliding-window per-model RPM.
    """

    def __init__(self) -> None:
        """Initialise with empty metrics."""
        self._lock = threading.Lock()
        self._requests_by_model: Dict[str, int] = {}
        self._cascade_count: int = 0
        self._compression_count: int = 0
        self._cascade_log: deque = deque(maxlen=_MAX_EVENTS)
        self._requests_minute: Dict[str, deque] = {}

    def record_request(self, model: str) -> None:
        """Record a successful LLM request.

        Args:
            model: Model name that handled the request.
        """
        with self._lock:
            self._requests_by_model[model] = (
                self._requests_by_model.get(model, 0) + 1
            )
            if model not in self._requests_minute:
                self._requests_minute[model] = deque()
            self._requests_minute[model].append(
                time.monotonic(),
            )

    def record_cascade(
        self,
        from_model: str,
        reason: str,
        to_model: str = "",
    ) -> None:
        """Record a cascade event.

        Args:
            from_model: Model that was skipped.
            reason: Why the cascade happened
                (e.g. ``"budget_exhausted"``,
                ``"api_error"``).
            to_model: Model cascaded to (if known).
        """
        with self._lock:
            self._cascade_count += 1
            self._cascade_log.append(
                {
                    "timestamp": time.time(),
                    "from_model": from_model,
                    "to_model": to_model,
                    "reason": reason,
                }
            )

    def record_compression(self) -> None:
        """Record a progressive compression trigger."""
        with self._lock:
            self._compression_count += 1

    def get_stats(self) -> Dict[str, Any]:
        """Return current observability metrics.

        Returns:
            Dict with ``requests_total``,
            ``requests_by_model``, ``cascade_count``,
            ``compression_count``, ``cascade_log`` (last
            50), and ``rpm_by_model``.
        """
        now = time.monotonic()
        with self._lock:
            total = sum(self._requests_by_model.values())
            rpm: Dict[str, int] = {}
            cutoff = now - _MINUTE
            for model, dq in self._requests_minute.items():
                while dq and dq[0] < cutoff:
                    dq.popleft()
                rpm[model] = len(dq)
            log_tail = list(self._cascade_log)[-50:]
            return {
                "requests_total": total,
                "requests_by_model": dict(
                    self._requests_by_model,
                ),
                "cascade_count": self._cascade_count,
                "compression_count": (self._compression_count),
                "cascade_log": log_tail,
                "rpm_by_model": rpm,
            }
