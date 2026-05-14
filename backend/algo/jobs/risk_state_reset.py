"""Daily IST-midnight risk-state reset.

For every user with an active row in algo.kill_switch OR a row
in today's algo.risk_state, zero the daily P&L counters.
Idempotent — safe to re-run.

Wired via @register_job("algo_risk_state_reset") in
backend/jobs/executor.py.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.algo.paper.risk_state_repo import RiskStateRepo
from backend.db.engine import disposable_pg_session

_logger = logging.getLogger(__name__)


def _ist_today() -> date:
    """IST is UTC+5:30. Compute the IST-local date."""
    now_ist = datetime.now(timezone.utc) + timedelta(
        hours=5, minutes=30,
    )
    return now_ist.date()


async def run_risk_state_reset_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reset daily P&L counters for every user that has either:
      - an algo.risk_state row for today (in case of partial-day
        replay), OR
      - any row at all in algo.kill_switch (active or not — these
        are the users known to the algo system).
    """
    repo = RiskStateRepo()
    today = _ist_today()
    count = 0

    async with disposable_pg_session() as session:
        result = await session.execute(
            text(
                "SELECT DISTINCT user_id FROM ("
                "  SELECT user_id FROM algo.kill_switch "
                "  UNION "
                "  SELECT user_id FROM algo.risk_state "
                "    WHERE day_date = :td"
                ") u"
            ),
            {"td": today},
        )
        user_ids = [
            UUID(str(r["user_id"])) for r in result.mappings().all()
        ]
        for uid in user_ids:
            await repo.reset_for_day(
                session, user_id=uid, day_date=today,
            )
            count += 1
        await session.commit()

    _logger.info("risk_state_reset: reset %d users for %s", count, today)
    return {
        "reset_users": count,
        "day_date": today.isoformat(),
    }
