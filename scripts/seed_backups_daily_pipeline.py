"""Seed the Backups Daily pipeline.

One step, one job — ``backups_daily`` — scheduled at 00:30
IST every day. Replaces the per-pipeline backup loop
introduced in ASETPLTFRM-418.

Idempotent: re-running upserts the pipeline + replaces the
steps.

Usage::

    docker compose exec backend python \\
        scripts/seed_backups_daily_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from db.engine import get_session_factory
from sqlalchemy import text

_logger = logging.getLogger(__name__)

_NS = uuid.UUID("e2c3a4b5-d6e7-4f89-a012-3456789abcde")
PIPELINE_NAME = "Backups Daily"
PIPELINE_ID = str(uuid.uuid5(_NS, PIPELINE_NAME))

STEPS = [
    {
        "step_order": 1,
        "job_type": "backups_daily",
        "job_name": "Full Iceberg warehouse snapshot",
        "payload": {},
    },
]


async def seed() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO pipelines "
                "(pipeline_id, name, scope, cron_days, "
                " cron_time, cron_dates, enabled) "
                "VALUES (:pid, :name, 'india', "
                "        'mon,tue,wed,thu,fri,sat,sun',"
                "        '00:30', NULL, TRUE) "
                "ON CONFLICT (name) DO UPDATE SET "
                "  scope = EXCLUDED.scope, "
                "  cron_days = EXCLUDED.cron_days, "
                "  cron_time = EXCLUDED.cron_time, "
                "  cron_dates = EXCLUDED.cron_dates, "
                "  enabled = EXCLUDED.enabled, "
                "  updated_at = NOW()"
            ),
            {"pid": PIPELINE_ID, "name": PIPELINE_NAME},
        )
        await session.execute(
            text(
                "DELETE FROM pipeline_steps "
                "WHERE pipeline_id = :pid"
            ),
            {"pid": PIPELINE_ID},
        )
        for step in STEPS:
            await session.execute(
                text(
                    "INSERT INTO pipeline_steps "
                    "(pipeline_id, step_order, "
                    " job_type, job_name, payload) "
                    "VALUES (:pid, :order, :jt, :name,"
                    "        CAST(:payload AS jsonb))"
                ),
                {
                    "pid": PIPELINE_ID,
                    "order": step["step_order"],
                    "jt": step["job_type"],
                    "name": step["job_name"],
                    "payload": json.dumps(
                        step.get("payload") or {},
                    ),
                },
            )
        await session.commit()
    _logger.info(
        "%s seeded — cron 00:30 IST daily",
        PIPELINE_NAME,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(seed())
