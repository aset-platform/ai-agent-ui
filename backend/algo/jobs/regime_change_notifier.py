"""Daily regime-change notifier (22:35 IST) — REGIME-3.

Diffs today's vs yesterday's regime label from
``stocks.regime_history``.  On flip, writes a single
``regime_changed`` event to ``algo.events``.  Frontend
``RegimeChangeBanner`` polls ``useRegimeCurrent`` and shows the
amber banner via localStorage diff — no per-user fan-out here.

System events use a sentinel UUID for ``user_id`` because the
``algo.events`` Iceberg schema marks ``user_id`` non-nullable.  The
Iceberg ``strategy_id`` column is nullable so we leave it as
``None``.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from uuid import UUID, uuid4

from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.regime.repo import get_regime_history

_logger = logging.getLogger(__name__)

# Sentinel for system-emitted events (algo.events.user_id is
# non-nullable).  All zeros is the canonical "system" UUID we use
# elsewhere for similar fan-out-less broadcasts.
_SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


def _get_regime_for_date(d: date) -> str | None:
    """Pull a single day's regime label, ``None`` if missing."""
    rows = get_regime_history(start=d, end=d)
    if not rows:
        return None
    return rows[0].regime_label


def _emit_event(*, payload: dict) -> None:
    row = event_row(
        session_id=uuid4(),
        user_id=_SYSTEM_USER_ID,
        strategy_id=None,
        mode="system",
        type_="regime_changed",
        payload=payload,
    )
    flush_events([row])


def run_notifier(as_of: date | None = None) -> dict | None:
    """Detect a regime flip vs yesterday and emit one event.

    Returns the payload dict on emit, ``None`` when no flip (or
    when either day's regime is missing).
    """
    if as_of is None:
        as_of = date.today()
    today = _get_regime_for_date(as_of)
    yesterday = _get_regime_for_date(as_of - timedelta(days=1))
    if today is None or yesterday is None:
        _logger.info(
            "regime_changed: skip — today=%s yesterday=%s",
            today, yesterday,
        )
        return None
    if today == yesterday:
        return None
    payload = {
        "from_regime": yesterday,
        "to_regime": today,
        "bar_date": as_of.isoformat(),
    }
    _emit_event(payload=payload)
    _logger.info("regime_changed event emitted: %s", payload)
    return payload
