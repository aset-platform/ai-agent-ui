"""Seed the scheduled_jobs row for the daily
``algo_closed_trades_rollup`` job.

Idempotent — uses ON CONFLICT (name) DO UPDATE so re-running
adjusts the schedule but doesn't duplicate.

Usage::

    docker compose exec backend python \
        scripts/seed_closed_trades_rollup.py
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import text

from db.engine import get_session_factory

_logger = logging.getLogger(__name__)

# Stable UUID namespace so re-runs target the same job_id row.
_NS = uuid.UUID("f3d4b5c6-e7f8-4a9b-b123-4567890abcde")

_JOB = {
    "name": "Algo Closed Trades Rollup - Daily",
    "job_type": "algo_closed_trades_rollup",
    # 16:30 IST, well after market close (15:30 IST) and the
    # 15:45 IST budget reconciliation job, so postback fills have
    # settled. Mon-Fri only (no trading on weekends).
    "cron_days": "mon,tue,wed,thu,fri",
    "cron_time": "16:30",
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
            _JOB["name"], _JOB["job_type"], jid,
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())


if __name__ == "__main__":
    main()
