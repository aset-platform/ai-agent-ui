"""Reconciliation scheduler job — V2-3.

Runs every 5 minutes during NSE market hours (09:15–15:30 IST,
Mon–Fri).  For each user that has at least one open position,
compares Kite broker positions against ``algo.positions`` and
emits drift events.

Registered in ``backend/jobs/executor.py`` via::

    @register_job("algo_reconciliation")
    async def _job_algo_reconciliation(payload=None): ...

This module only contains the *async* implementation logic;
the ``@register_job`` entry point lives in ``executor.py``
because that module owns the ``JOB_EXECUTORS`` registry.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.algo.live.reconciliation import (
    is_market_open_ist,
    reconcile_user,
)
from backend.db.engine import disposable_pg_session

_logger = logging.getLogger(__name__)

UTC = timezone.utc


async def _users_with_open_positions() -> list[UUID]:
    """Return distinct user_ids that have open positions today."""
    async with disposable_pg_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT DISTINCT r.user_id "
                    "FROM algo.positions p "
                    "JOIN algo.runs r ON r.id = p.run_id "
                    "WHERE p.closed_at IS NULL"
                ),
            )
        ).all()
    return [UUID(str(r[0])) for r in rows]


async def run_reconciliation_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Entry point for the scheduler.

    1. Sweep budget reservations FIRST — runs every tick,
       independent of market hours or open positions, because
       PENDING (>120s) and SUBMITTED (>5min) timeouts must
       release reserved capital and surface stuck orders
       even when markets are closed.
    2. Skip per-user drift reconcile if market is not open
       (09:15–15:30 IST, Mon–Fri).
    3. Find all users with open positions.
    4. Reconcile each user's positions against the broker.

    Returns a summary dict.
    """
    # Budget reservation reconciliation — unconditional, once
    # per tick, user-id agnostic (scans the entire
    # algo.budget_reservations active set). Must run BEFORE
    # market-hours and no-open-positions early returns so the
    # 120s PENDING + 300s SUBMITTED timeouts cannot stall
    # outside RTH.
    budget_summary: dict | None = None
    try:
        from backend.algo.live.budget_reconciliation import (
            reconcile as budget_reconcile,
        )
        await budget_reconcile()
        budget_summary = {"ok": True}
    except Exception as exc:
        _logger.warning(
            "algo_reconciliation: budget reconcile failed: %s",
            exc, exc_info=True,
        )
        budget_summary = {"ok": False, "error": str(exc)}

    if not is_market_open_ist():
        _logger.debug(
            "algo_reconciliation: market closed — skip",
        )
        return {
            "skipped": True,
            "reason": "market_closed",
            "budget": budget_summary,
        }

    users = await _users_with_open_positions()
    if not users:
        _logger.debug(
            "algo_reconciliation: no users with open positions",
        )
        return {
            "skipped": False,
            "users_reconciled": 0,
            "summaries": [],
            "budget": budget_summary,
        }

    summaries: list[dict] = []
    for user_id in users:
        try:
            summary = await reconcile_user(user_id)
            summaries.append(summary)
        except Exception as exc:
            _logger.warning(
                "algo_reconciliation: user=%s error=%s",
                user_id,
                exc,
                exc_info=True,
            )
            summaries.append({
                "user_id": str(user_id),
                "error": str(exc),
            })

    _logger.info(
        "algo_reconciliation: reconciled %d users",
        len(users),
    )
    return {
        "skipped": False,
        "users_reconciled": len(users),
        "summaries": summaries,
        "budget": budget_summary,
    }
