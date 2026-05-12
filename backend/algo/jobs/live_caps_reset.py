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

ASETPLTFRM-375 — startup catch-up: on backend boot, if the
scheduled 09:00 IST trigger was missed (e.g. backend restarted at
10:00 IST), ``run_if_missed_today()`` replays the reset once so
caps reflect a fresh day. Per CLAUDE.md §4.5 #33,
``scheduler_catchup_enabled=False`` by design — that prevents
mid-day pulls but means the reset job never replays without this
explicit hook.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

_logger = logging.getLogger(__name__)

UTC = timezone.utc
_IST_OFFSET = timedelta(hours=5, minutes=30)

# Redis key storing the IST date (YYYY-MM-DD) of the last
# successful reset. Used by ``run_if_missed_today`` to decide
# whether the scheduled 09:00 IST trigger ran.
_LAST_RESET_KEY = "algo:caps_last_reset_date"
# Trading-day start in IST. Resets only run after this wall-clock.
_RESET_HOUR_IST = 9


def _ist_now() -> datetime:
    """IST is UTC+5:30."""
    return datetime.now(UTC) + _IST_OFFSET


def is_market_day_ist() -> bool:
    """True on Mon–Fri IST."""
    return _ist_now().weekday() < 5  # Sat=5, Sun=6


def _today_ist_iso() -> str:
    """Today's IST date as ISO YYYY-MM-DD."""
    return _ist_now().strftime("%Y-%m-%d")


def _get_redis_sync() -> Any | None:
    """Return a sync redis client or None on any failure.

    Mirrors the defensive pattern in
    ``backend/algo/broker/kite_client.py::resolve_dry_run_for_user``.
    """
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        return None
    try:
        from auth.token_store import get_redis_client
        return get_redis_client(redis_url)
    except Exception:  # noqa: BLE001
        return None


async def run_live_caps_daily_reset(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reset daily counters on all live_caps rows.

    Only runs on Mon–Fri (market days in IST).  Safe to re-run —
    resetting to 0 is idempotent if the market hasn't opened yet.

    Stamps ``_LAST_RESET_KEY`` with today's IST date on success so
    ``run_if_missed_today`` can skip the boot replay.

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

    # Stamp last-reset date for startup catch-up. Best-effort; a
    # Redis hiccup must not fail the reset itself.
    client = _get_redis_sync()
    if client is not None:
        try:
            client.set(_LAST_RESET_KEY, _today_ist_iso())
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "live_caps_daily_reset: stamp last-reset failed: %s",
                exc,
            )

    return {
        "skipped": False,
        "rows_reset": rows_reset,
    }


async def run_if_missed_today() -> dict[str, Any]:
    """Startup catch-up — run the daily reset once if missed.

    Triggers iff ALL of:
    - Market day (Mon–Fri IST).
    - ``now_ist >= 09:00 IST`` (the scheduled trigger wall-clock).
    - ``_LAST_RESET_KEY`` in Redis is missing OR holds a date <
      today's IST date.

    Idempotent: re-running on the same day after a successful
    earlier reset is a no-op (the Redis stamp matches today, so
    we skip).

    Returns summary dict with keys: ``skipped`` (bool), ``reason``
    (str when skipped), plus the inner reset's ``rows_reset`` when
    we did fire.
    """
    now = _ist_now()
    today_iso = now.strftime("%Y-%m-%d")

    if not is_market_day_ist():
        _logger.debug(
            "run_if_missed_today: %s is weekend — skip", today_iso,
        )
        return {"skipped": True, "reason": "weekend"}

    if now.hour < _RESET_HOUR_IST:
        _logger.debug(
            "run_if_missed_today: pre-09:00 IST (%s) — scheduler "
            "will handle the trigger normally",
            now.strftime("%H:%M"),
        )
        return {"skipped": True, "reason": "before_reset_hour"}

    client = _get_redis_sync()
    last_reset: str | None = None
    if client is not None:
        try:
            raw = client.get(_LAST_RESET_KEY)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            last_reset = (
                str(raw).strip() if raw is not None else None
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "run_if_missed_today: redis read failed (%s) — "
                "falling through to reset", exc,
            )
            last_reset = None

    if last_reset == today_iso:
        _logger.debug(
            "run_if_missed_today: already reset today (%s) — skip",
            today_iso,
        )
        return {"skipped": True, "reason": "already_reset"}

    _logger.info(
        "run_if_missed_today: replaying missed reset "
        "(last_reset=%s, today=%s)",
        last_reset, today_iso,
    )
    result = await run_live_caps_daily_reset()
    result["catchup"] = True
    return result
