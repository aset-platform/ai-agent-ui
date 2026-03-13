"""LLM cascade observability metrics collector.

Tracks per-model request counts, cascade events, and
compression triggers for the admin observability dashboard.

Optionally persists events to the ``stocks.llm_usage``
Iceberg table and loads pricing from
``stocks.llm_pricing`` for cost estimation.

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

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from datetime import date, datetime, timezone
from typing import Any

_logger = logging.getLogger(__name__)

# Keep last 1000 cascade events in memory.
_MAX_EVENTS = 1_000

# Window for per-minute request rate tracking.
_MINUTE = 60

# Health assessment window — 5 minutes.
_HEALTH_WINDOW = 300

# Thresholds for tier health classification.
_DEGRADED_FAILURES = 1  # ≥1 failure → degraded
_DOWN_FAILURES = 4  # ≥4 failures → down

# Latency sliding window size per model.
_LATENCY_WINDOW = 100

# Batch size for Iceberg writes.
_FLUSH_INTERVAL = 30  # seconds
_FLUSH_BATCH = 50  # max events per flush


class ObservabilityCollector:
    """Thread-safe cascade and request metrics collector.

    Attributes:
        _lock: Guard for all mutable state.
        _requests_by_model: Cumulative request count
            per model.
        _cascade_count: Total cascade events.
        _compression_count: Compression trigger count.
        _cascade_log: Recent cascade events (bounded).
        _requests_minute: Sliding-window per-model RPM.
        _pricing: Current pricing dict keyed by
            ``(provider, model)``.
        _pending_events: Buffer for Iceberg writes.
    """

    def __init__(self, repo=None) -> None:
        """Initialise with empty or seeded metrics.

        Args:
            repo: Optional
                :class:`~stocks.repository.StockRepository`
                for Iceberg persistence. If ``None``,
                events are tracked in-memory only.
        """
        self._lock = threading.Lock()
        self._requests_by_model: dict[str, int] = {}
        self._cascade_count: int = 0
        self._compression_count: int = 0
        self._cascade_log: deque = deque(
            maxlen=_MAX_EVENTS,
        )
        self._requests_minute: dict[str, deque] = {}
        self._repo = repo
        self._pricing: dict[tuple, dict] = {}
        self._pending_events: list[dict] = []
        self._flush_timer: threading.Timer | None = None

        # Per-tier health tracking.
        self._failures_by_model: dict[str, deque] = {}
        self._successes_by_model: dict[str, deque] = {}
        self._cascades_by_model: dict[str, int] = {}
        self._latency_by_model: dict[str, deque] = {}
        self._disabled_tiers: set[str] = set()

        if repo is not None:
            self._load_pricing()
            self._seed_from_iceberg()
            self._schedule_flush()

    def _load_pricing(self) -> None:
        """Load current pricing rates from Iceberg."""
        try:
            df = self._repo.get_current_pricing()
            if df.empty:
                _logger.info(
                    "No LLM pricing data in Iceberg.",
                )
                return
            for _, row in df.iterrows():
                key = (row["provider"], row["model"])
                self._pricing[key] = {
                    "input_cost_per_1m": row["input_cost_per_1m"],
                    "output_cost_per_1m": row["output_cost_per_1m"],
                }
            _logger.info(
                "Loaded %d LLM pricing rates.",
                len(self._pricing),
            )
        except Exception:
            _logger.warning(
                "Failed to load LLM pricing" " — cost estimation disabled.",
                exc_info=True,
            )

    def _seed_from_iceberg(self) -> None:
        """Seed in-memory totals from Iceberg."""
        try:
            totals = self._repo.get_usage_totals()
            with self._lock:
                self._cascade_count = totals["cascade_count"]
                self._compression_count = totals["compression_count"]
                # Seed requests_total into a
                # synthetic "_lifetime" key.
                rt = totals["requests_total"]
                if rt > 0:
                    self._requests_by_model["_lifetime"] = rt
            _logger.info(
                "Seeded from Iceberg: %d requests,"
                " %d cascades, %d compressions.",
                totals["requests_total"],
                totals["cascade_count"],
                totals["compression_count"],
            )
        except Exception:
            _logger.warning(
                "Failed to seed from Iceberg" " — starting from zero.",
                exc_info=True,
            )

    def reload_pricing(self) -> None:
        """Reload pricing from Iceberg (after CRUD)."""
        self._pricing.clear()
        self._load_pricing()

    def get_pricing(self, provider: str, model: str) -> dict | None:
        """Look up current pricing for a model.

        Args:
            provider: ``"groq"`` or ``"anthropic"``.
            model: Full model name.

        Returns:
            Dict with ``input_cost_per_1m`` and
            ``output_cost_per_1m``, or ``None``.
        """
        return self._pricing.get((provider, model))

    def _estimate_cost(
        self,
        provider: str,
        model: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> tuple:
        """Estimate cost and return snapshot rates.

        Returns:
            ``(input_rate, output_rate, cost)`` or
            ``(None, None, None)`` if pricing unknown.
        """
        pricing = self.get_pricing(provider, model)
        if pricing is None:
            return None, None, None
        in_rate = pricing["input_cost_per_1m"]
        out_rate = pricing["output_cost_per_1m"]
        pt = prompt_tokens or 0
        ct = completion_tokens or 0
        cost = (pt * in_rate + ct * out_rate) / 1_000_000
        return in_rate, out_rate, cost

    # ----------------------------------------------------------
    # Event recording
    # ----------------------------------------------------------

    def record_request(
        self,
        model: str,
        provider: str = "",
        agent_id: str = "",
        tier_index: int = 0,
        user_id: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Record a successful LLM request.

        Args:
            model: Model name that handled the request.
            provider: ``"groq"`` or ``"anthropic"``.
            agent_id: Agent that made the request.
            tier_index: 0-based cascade position.
            user_id: User who triggered the request.
            prompt_tokens: Input token count.
            completion_tokens: Output token count.
            latency_ms: Request duration in ms.
        """
        now = time.monotonic()
        with self._lock:
            self._requests_by_model[model] = (
                self._requests_by_model.get(model, 0) + 1
            )
            if model not in self._requests_minute:
                self._requests_minute[model] = deque()
            self._requests_minute[model].append(now)

            # Track success for health assessment.
            if model not in self._successes_by_model:
                self._successes_by_model[model] = deque(
                    maxlen=_LATENCY_WINDOW,
                )
            self._successes_by_model[model].append(now)

            # Track latency.
            if latency_ms is not None:
                if model not in self._latency_by_model:
                    self._latency_by_model[model] = deque(
                        maxlen=_LATENCY_WINDOW,
                    )
                self._latency_by_model[model].append(
                    (now, latency_ms),
                )

        if self._repo is not None:
            in_r, out_r, cost = self._estimate_cost(
                provider,
                model,
                prompt_tokens,
                completion_tokens,
            )
            total = ((prompt_tokens or 0) + (completion_tokens or 0)) or None
            self._enqueue_event(
                {
                    "event_type": "request",
                    "model": model,
                    "provider": provider or "unknown",
                    "agent_id": agent_id or "unknown",
                    "tier_index": tier_index,
                    "user_id": user_id,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": (completion_tokens),
                    "total_tokens": total,
                    "input_cost_per_1m": in_r,
                    "output_cost_per_1m": out_r,
                    "estimated_cost_usd": cost,
                    "latency_ms": latency_ms,
                    "success": True,
                }
            )

    def record_cascade(
        self,
        from_model: str,
        reason: str,
        to_model: str = "",
        provider: str = "",
        agent_id: str = "",
        tier_index: int = 0,
        user_id: str | None = None,
    ) -> None:
        """Record a cascade event.

        Args:
            from_model: Model that was skipped.
            reason: Why the cascade happened.
            to_model: Model cascaded to (if known).
            provider: Provider of skipped model.
            agent_id: Agent that made the request.
            tier_index: Tier index of skipped model.
            user_id: User who triggered the request.
        """
        now = time.monotonic()
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

            # Track per-model cascade count.
            self._cascades_by_model[from_model] = (
                self._cascades_by_model.get(
                    from_model,
                    0,
                )
                + 1
            )

            # Track failure timestamp for health.
            if from_model not in self._failures_by_model:
                self._failures_by_model[from_model] = deque(
                    maxlen=_LATENCY_WINDOW
                )
            self._failures_by_model[from_model].append(
                now,
            )

        if self._repo is not None:
            self._enqueue_event(
                {
                    "event_type": "cascade",
                    "model": from_model,
                    "provider": provider or "unknown",
                    "agent_id": agent_id or "unknown",
                    "tier_index": tier_index,
                    "user_id": user_id,
                    "cascade_reason": reason,
                    "cascade_from_model": from_model,
                    "success": False,
                }
            )

    def record_compression(
        self,
        agent_id: str = "",
        user_id: str | None = None,
    ) -> None:
        """Record a progressive compression trigger.

        Args:
            agent_id: Agent that triggered compression.
            user_id: User who triggered the request.
        """
        with self._lock:
            self._compression_count += 1

        if self._repo is not None:
            self._enqueue_event(
                {
                    "event_type": "compression",
                    "model": "n/a",
                    "provider": "n/a",
                    "agent_id": agent_id or "unknown",
                    "tier_index": 0,
                    "user_id": user_id,
                    "success": True,
                }
            )

    # ----------------------------------------------------------
    # Iceberg persistence (batched background flush)
    # ----------------------------------------------------------

    def _enqueue_event(self, event: dict) -> None:
        """Add an event to the pending buffer.

        Args:
            event: Partial event dict (common fields
                added here).
        """
        now = datetime.now(timezone.utc).replace(
            tzinfo=None,
        )
        event["usage_id"] = str(uuid.uuid4())
        event["request_date"] = date.today()
        event["timestamp"] = now
        with self._lock:
            self._pending_events.append(event)

    def _schedule_flush(self) -> None:
        """Schedule the next background flush."""
        self._flush_timer = threading.Timer(
            _FLUSH_INTERVAL,
            self._flush_events,
        )
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _flush_events(self) -> None:
        """Write pending events to Iceberg."""
        with self._lock:
            batch = self._pending_events[:_FLUSH_BATCH]
            self._pending_events = self._pending_events[_FLUSH_BATCH:]
        if batch:
            try:
                self._repo.append_llm_usage(batch)
                _logger.debug(
                    "Flushed %d LLM usage events" " to Iceberg.",
                    len(batch),
                )
            except Exception:
                _logger.warning(
                    "Failed to flush %d LLM usage" " events — re-queuing.",
                    len(batch),
                    exc_info=True,
                )
                with self._lock:
                    self._pending_events = batch + self._pending_events
        # Reschedule.
        self._schedule_flush()

    def flush_sync(self) -> None:
        """Flush all pending events synchronously.

        Call during shutdown to avoid data loss.
        """
        if self._flush_timer is not None:
            self._flush_timer.cancel()
        with self._lock:
            batch = list(self._pending_events)
            self._pending_events.clear()
        if batch and self._repo is not None:
            try:
                self._repo.append_llm_usage(batch)
                _logger.info(
                    "Shutdown flush: %d events" " written.",
                    len(batch),
                )
            except Exception:
                _logger.error(
                    "Shutdown flush failed for" " %d events.",
                    len(batch),
                    exc_info=True,
                )

    # ----------------------------------------------------------
    # Tier health
    # ----------------------------------------------------------

    def disable_tier(self, model: str) -> None:
        """Mark a tier as manually disabled.

        Args:
            model: Groq model identifier.
        """
        with self._lock:
            self._disabled_tiers.add(model)
        _logger.info("Tier disabled: %s", model)

    def enable_tier(self, model: str) -> None:
        """Re-enable a manually disabled tier.

        Args:
            model: Groq model identifier.
        """
        with self._lock:
            self._disabled_tiers.discard(model)
        _logger.info("Tier enabled: %s", model)

    def is_tier_disabled(self, model: str) -> bool:
        """Check if a tier is manually disabled.

        Args:
            model: Groq model identifier.

        Returns:
            ``True`` if the tier is manually disabled.
        """
        with self._lock:
            return model in self._disabled_tiers

    def _classify_health(
        self,
        model: str,
        now: float,
    ) -> str:
        """Classify a tier's health status.

        Uses a sliding window of failures in the last
        :data:`_HEALTH_WINDOW` seconds.

        Args:
            model: Model identifier.
            now: Current monotonic time.

        Returns:
            ``"disabled"``, ``"down"``, ``"degraded"``,
            or ``"healthy"``.
        """
        if model in self._disabled_tiers:
            return "disabled"

        cutoff = now - _HEALTH_WINDOW
        failures = self._failures_by_model.get(model)
        recent_failures = 0
        if failures:
            recent_failures = sum(1 for t in failures if t >= cutoff)

        if recent_failures >= _DOWN_FAILURES:
            return "down"
        if recent_failures >= _DEGRADED_FAILURES:
            return "degraded"
        return "healthy"

    def _model_latency_stats(
        self,
        model: str,
        now: float,
    ) -> dict[str, int | None]:
        """Compute latency stats for a model.

        Args:
            model: Model identifier.
            now: Current monotonic time.

        Returns:
            Dict with ``avg_ms`` and ``p95_ms``.
        """
        dq = self._latency_by_model.get(model)
        if not dq:
            return {"avg_ms": None, "p95_ms": None}
        cutoff = now - _HEALTH_WINDOW
        recent = [ms for t, ms in dq if t >= cutoff]
        if not recent:
            # Fall back to all stored latencies.
            recent = [ms for _, ms in dq]
        if not recent:
            return {"avg_ms": None, "p95_ms": None}
        recent.sort()
        avg = int(sum(recent) / len(recent))
        p95_idx = int(len(recent) * 0.95)
        p95 = recent[min(p95_idx, len(recent) - 1)]
        return {"avg_ms": avg, "p95_ms": p95}

    def get_tier_health(
        self,
        tier_models: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return per-tier health status.

        Args:
            tier_models: Ordered list of configured tier
                model names. If ``None``, returns health for
                all models that have recorded activity.

        Returns:
            Dict with ``tiers`` list and ``summary``.
        """
        now = time.monotonic()
        with self._lock:
            models = tier_models or list(
                set(
                    list(self._requests_by_model.keys())
                    + list(self._cascades_by_model.keys())
                )
                - {"_lifetime"}
            )
            tiers = []
            for model in models:
                status = self._classify_health(
                    model,
                    now,
                )
                latency = self._model_latency_stats(
                    model,
                    now,
                )
                # Count recent failures.
                cutoff = now - _HEALTH_WINDOW
                failures = self._failures_by_model.get(
                    model,
                )
                recent_f = 0
                if failures:
                    recent_f = sum(1 for t in failures if t >= cutoff)

                # Count recent successes.
                successes = self._successes_by_model.get(
                    model,
                )
                recent_s = 0
                if successes:
                    recent_s = sum(1 for t in successes if t >= cutoff)

                tiers.append(
                    {
                        "model": model,
                        "status": status,
                        "failures_5m": recent_f,
                        "successes_5m": recent_s,
                        "cascade_count": (
                            self._cascades_by_model.get(
                                model,
                                0,
                            )
                        ),
                        "latency": latency,
                    }
                )

            # Summary counts.
            healthy = sum(1 for t in tiers if t["status"] == "healthy")
            degraded = sum(1 for t in tiers if t["status"] == "degraded")
            down = sum(1 for t in tiers if t["status"] == "down")
            disabled = sum(1 for t in tiers if t["status"] == "disabled")

        return {
            "tiers": tiers,
            "summary": {
                "total": len(tiers),
                "healthy": healthy,
                "degraded": degraded,
                "down": down,
                "disabled": disabled,
            },
        }

    # ----------------------------------------------------------
    # Stats
    # ----------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return current observability metrics.

        Returns:
            Dict with ``requests_total``,
            ``requests_by_model``, ``cascade_count``,
            ``compression_count``, ``cascade_log``
            (last 50), and ``rpm_by_model``.
        """
        now = time.monotonic()
        with self._lock:
            total = sum(self._requests_by_model.values())
            rpm: dict[str, int] = {}
            cutoff = now - _MINUTE
            for model, dq in self._requests_minute.items():
                while dq and dq[0] < cutoff:
                    dq.popleft()
                rpm[model] = len(dq)
            log_tail = list(self._cascade_log)[-50:]
            # Exclude synthetic _lifetime key from
            # per-model breakdown.
            by_model = {
                k: v
                for k, v in (self._requests_by_model.items())
                if k != "_lifetime"
            }
            return {
                "requests_total": total,
                "requests_by_model": by_model,
                "cascade_count": self._cascade_count,
                "compression_count": (self._compression_count),
                "cascade_log": log_tail,
                "rpm_by_model": rpm,
            }
