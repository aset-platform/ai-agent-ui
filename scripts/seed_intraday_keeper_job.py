"""Seed the ``scheduled_jobs`` row for ``intraday_bars_daily_ingest``
(ASETPLTFRM-400 slice 1d).

Idempotent — uses ON CONFLICT (name) DO UPDATE so re-running just
nudges the schedule but doesn't duplicate. The job_id is a stable
UUID5 so re-runs target the same row.

Usage::

    docker compose exec backend python scripts/seed_intraday_keeper_job.py
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from db.engine import get_session_factory
from sqlalchemy import text

_logger = logging.getLogger(__name__)

# Same namespace as scripts/seed_v3_scheduled_jobs.py so all
# algo-tier seed jobs share a stable UUID space.
_NS = uuid.UUID("e2c3a4b5-d6e7-4f89-a012-3456789abcde")

JOB = {
    "name": "Intraday Bars Daily Keeper",
    "job_type": "intraday_bars_daily_ingest",
    # 15:45 IST = 15 min after NSE close; Mon-Fri only because the
    # job pulls Kite intraday data and Kite returns nothing on
    # weekends.
    "cron_days": "mon,tue,wed,thu,fri",
    "cron_time": "15:45",
    "cron_dates": None,
    "scope": None,
}


async def seed() -> None:
    factory = get_session_factory()
    async with factory() as session:
        jid = str(uuid.uuid5(_NS, JOB["name"]))
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
                "name": JOB["name"],
                "jt": JOB["job_type"],
                "cd": JOB["cron_days"],
                "ct": JOB["cron_time"],
                "cdates": JOB["cron_dates"],
                "scope": JOB["scope"],
            },
        )
        await session.commit()
        _logger.info("seeded %s -> %s", JOB["name"], JOB["job_type"])


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    asyncio.run(seed())
