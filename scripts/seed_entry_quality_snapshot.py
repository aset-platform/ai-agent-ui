"""Seed the scheduled_jobs row for the daily
``entry_quality_snapshot`` job.

Idempotent — uses ON CONFLICT (name) DO UPDATE so re-running
adjusts the schedule but doesn't duplicate.

Usage::

    docker compose exec backend python \
        scripts/seed_entry_quality_snapshot.py
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from db.engine import get_session_factory
from sqlalchemy import text

_logger = logging.getLogger(__name__)

# Stable UUID namespace so re-runs target the same job_id row.
_NS = uuid.UUID("f3d4b5c6-e7f8-4a9b-b123-4567890abcde")

_JOB = {
    "name": "Entry Quality Snapshot (QM Score + ESS)",
    "job_type": "entry_quality_snapshot",
    # 16:00 IST, sits between market close (15:30 IST) and the
    # 16:30 IST closed-trades-rollup job, with adequate margin for
    # fills to settle. Mon-Fri only (no trading on weekends).
    "cron_days": "mon,tue,wed,thu,fri",
    "cron_time": "16:00",
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
