"""Seed scheduled_jobs rows for the v3 epic jobs.

Idempotent — uses ON CONFLICT (name) DO UPDATE so re-running adjusts
the schedule but doesn't duplicate. UUID job_ids are deterministic
via uuid5(NAMESPACE, name) so re-runs target the same row.

Usage::

    docker compose exec backend python scripts/seed_v3_scheduled_jobs.py
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import text

from db.engine import get_session_factory

_logger = logging.getLogger(__name__)

# Per spec §5.1 + each slice's @register_job comment block.
V3_JOBS = [
    {
        "name": "Regime Classifier - Daily",
        "job_type": "regime_classifier_daily",
        "cron_days": "mon,tue,wed,thu,fri,sat,sun",
        "cron_time": "22:30",
        "cron_dates": None,
        "scope": None,
    },
    {
        "name": "Regime Change Notifier - Daily",
        "job_type": "regime_change_notifier",
        "cron_days": "mon,tue,wed,thu,fri,sat,sun",
        "cron_time": "22:35",
        "cron_dates": None,
        "scope": None,
    },
    {
        "name": "Compute Daily Factors",
        "job_type": "compute_daily_factors",
        "cron_days": "mon,tue,wed,thu,fri,sat,sun",
        "cron_time": "23:00",
        "cron_dates": None,
        "scope": None,
    },
    {
        "name": "Attribution Daily Brinson",
        "job_type": "attribution_daily_brinson",
        "cron_days": "mon,tue,wed,thu,fri",
        "cron_time": "15:30",
        "cron_dates": None,
        "scope": None,
    },
    {
        "name": "Attribution Monthly Regression",
        "job_type": "attribution_monthly_regression",
        "cron_days": None,
        "cron_time": "04:00",
        "cron_dates": "1",  # 1st of every month
        "scope": None,
    },
    {
        "name": "Universe Snapshot Monthly",
        "job_type": "universe_snapshot_monthly",
        "cron_days": None,
        "cron_time": "03:00",
        "cron_dates": "1",
        "scope": None,
    },
]

# Stable UUID namespace so re-runs target the same job_id row.
_NS = uuid.UUID("e2c3a4b5-d6e7-4f89-a012-3456789abcde")


async def seed() -> None:
    factory = get_session_factory()
    async with factory() as session:
        for j in V3_JOBS:
            jid = str(uuid.uuid5(_NS, j["name"]))
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
                    "name": j["name"],
                    "jt": j["job_type"],
                    "cd": j["cron_days"],
                    "ct": j["cron_time"],
                    "cdates": j["cron_dates"],
                    "scope": j["scope"],
                },
            )
            _logger.info("seeded %s -> %s", j["name"], j["job_type"])
        await session.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())
    _logger.info("v3 scheduled_jobs seed complete (%d rows)",
                 len(V3_JOBS))


if __name__ == "__main__":
    main()
