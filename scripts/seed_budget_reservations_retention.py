"""Seed the scheduled_jobs row for the weekly
``algo_budget_reservations_retention`` job.

Idempotent — uses ON CONFLICT (name) DO UPDATE so re-running
adjusts the schedule but doesn't duplicate.

Usage::

    docker compose exec backend python \
        scripts/seed_budget_reservations_retention.py
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import text

from db.engine import get_session_factory

_logger = logging.getLogger(__name__)

# Stable UUID namespace so re-runs target the same job_id row.
_NS = uuid.UUID("e2c3a4b5-d6e7-4f89-a012-3456789abcde")

_JOB = {
    "name": "Budget Reservations Retention - Weekly",
    "job_type": "algo_budget_reservations_retention",
    # Run weekly on Sunday at 02:00 IST (off-hours, well clear of
    # market open at 09:15). Sunday is outside trading days so there
    # is zero risk of touching an active PENDING/SUBMITTED/PARTIAL row
    # from a live session.
    "cron_days": "sun",
    "cron_time": "02:00",
    "cron_dates": None,
    "scope": None,
}


async def seed() -> None:
    factory = get_session_factory()
    async with factory() as session:
        jid = str(uuid.uuid5(_NS, _JOB["name"]))
        await session.execute(
            text(
                "INSERT INTO scheduled_jobs "
                "(job_id, name, job_type, cron_days, cron_time, "
                " cron_dates, scope, enabled, force) "
                "VALUES (:jid, :name, :jt, :cd, :ct, :cdates, "
                "        :scope, TRUE, FALSE) "
                "ON CONFLICT (name) DO UPDATE SET "
                "  job_type = EXCLUDED.job_type, "
                "  cron_days = EXCLUDED.cron_days, "
                "  cron_time = EXCLUDED.cron_time, "
                "  cron_dates = EXCLUDED.cron_dates, "
                "  updated_at = NOW()"
            ),
            {
                "jid": jid,
                "name": _JOB["name"],
                "jt": _JOB["job_type"],
                "cd": _JOB["cron_days"],
                "ct": _JOB["cron_time"],
                "cdates": _JOB["cron_dates"],
                "scope": _JOB["scope"],
            },
        )
        await session.commit()
        _logger.info(
            "seeded %s -> %s (job_id=%s)",
            _JOB["name"],
            _JOB["job_type"],
            jid,
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())


if __name__ == "__main__":
    main()
