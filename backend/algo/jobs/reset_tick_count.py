"""Daily IST-midnight reset of KiteWsMultiplexer.tick_count_today.

The counter is process-local — each backend process keeps its own
WS registry and its own tick counters — so this job simply walks
the in-process registry and zeros every entry. Idempotent.

Wired via ``@register_job("algo_ws_tick_count_reset")`` in
``backend/jobs/executor.py`` and scheduled at 00:00 IST.
"""
from __future__ import annotations

import logging
from typing import Any

from backend.algo.broker import ws_registry

_logger = logging.getLogger(__name__)


async def run_reset_tick_count_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Zero ``tick_count_today`` on every active multiplexer.

    ``last_tick_at`` is intentionally preserved — a stale wall-
    clock is more useful for the dashboard dot than a hole.
    """
    count = 0
    for mux in list(ws_registry._registry.values()):
        try:
            mux.reset_tick_count()
            count += 1
        except Exception:
            _logger.warning(
                "ws_tick_count_reset: reset failed for mux=%s",
                mux,
                exc_info=True,
            )
    _logger.info(
        "ws_tick_count_reset: reset %d multiplexers", count,
    )
    return {"reset_count": count}
