"""Live caps daily counter reset — V2-5.

Resets ``cumulative_inr_today`` and ``orders_count_today`` on
``algo.live_caps`` at market open (09:00 IST, Mon–Fri).

This matches Kite's day boundary: Zerodha resets brokerage and
margin counters at the start of each trading day (after 09:00 IST
pre-open session). Resetting at 09:00 IST keeps our counters aligned
with the broker's view — midnight reset would allow a second set of
orders before the market opens.

Wired in ``backend/jobs/executor.py`` via::

    @register_job("algo_live_caps_daily_reset")
    async def _job_algo_live_caps_daily_reset(payload=None): ...
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

_logger = logging.getLogger(__name__)

UTC = timezone.utc


def _ist_now() -> datetime:
    """IST is UTC+5:30."""
    return datetime.now(UTC) + timedelta(hours=5, minutes=30)


def is_market_day_ist() -> bool:
    """True on Mon–Fri IST."""
    return _ist_now().weekday() < 5  # Sat=5, Sun=6


async def run_live_caps_daily_reset(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reset daily counters on all live_caps rows.

    Only runs on Mon–Fri (market days in IST).  Safe to re-run —
    resetting to 0 is idempotent if the market hasn't opened yet.

    Returns summary dict.
    """
    if not is_market_day_ist():
        _logger.debug(
            "live_caps_daily_reset: weekend — skip",
        )
        return {
            "skipped": True,
            "reason": "weekend",
            "rows_reset": 0,
        }

    from backend.algo.live.caps_repo import CapsRepo
    repo = CapsRepo()
    rows_reset = await repo.reset_daily_counters(user_id=None)
    _logger.info(
        "live_caps_daily_reset: reset %d rows", rows_reset,
    )
    return {
        "skipped": False,
        "rows_reset": rows_reset,
    }
