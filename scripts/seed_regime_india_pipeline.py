"""Seed the India Regime Daily Pipeline.

Chains the 4 daily India-only regime jobs into a single pipeline
(matches the existing India Daily Pipeline pattern):

  1. regime_classifier_daily      — produces regime_label + stress
  2. regime_change_notifier       — emits banner event on flip
  3. compute_daily_factors        — uses regime context for breadth
  4. attribution_daily_brinson    — Brinson per active strategy

Idempotency: each step's executor wrapper (see
``backend.algo.regime.pipeline_steps``) skips if today's row
already exists in the relevant Iceberg/PG table. Pipeline force=True
re-runs every step with pre-delete.

Schedule: 23:30 IST mon-fri (post-close + after the existing
22:00 sentiment + 23:00 daily-factors-as-standalone).

Idempotent — re-running this script updates the pipeline row +
rebuilds steps but does NOT duplicate.

Usage::

    docker compose exec backend python scripts/seed_regime_india_pipeline.py
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import text

from db.engine import get_session_factory

_logger = logging.getLogger(__name__)

# Stable UUID so re-runs target the same pipeline row.
_NS = uuid.UUID("e2c3a4b5-d6e7-4f89-a012-3456789abcde")
PIPELINE_NAME = "India Regime Daily Pipeline"
PIPELINE_ID = str(uuid.uuid5(_NS, PIPELINE_NAME))

STEPS = [
    {
        "step_order": 1,
        "job_type": "regime_classifier_daily",
        "job_name": "Regime Classifier - India",
    },
    {
        "step_order": 2,
        "job_type": "regime_change_notifier",
        "job_name": "Regime Change Notifier - India",
    },
    {
        "step_order": 3,
        "job_type": "compute_daily_factors",
        "job_name": "Compute Daily Factors - India",
    },
    {
        "step_order": 4,
        "job_type": "attribution_daily_brinson",
        "job_name": "Attribution Daily Brinson - India",
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
                "        'mon,tue,wed,thu,fri', '23:30', "
                "        NULL, TRUE) "
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
                    " job_type, job_name) "
                    "VALUES (:pid, :order, :jt, :name)"
                ),
                {
                    "pid": PIPELINE_ID,
                    "order": step["step_order"],
                    "jt": step["job_type"],
                    "name": step["job_name"],
                },
            )
            _logger.info(
                "step %d: %s (%s)",
                step["step_order"],
                step["job_name"],
                step["job_type"],
            )
        await session.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())
    _logger.info(
        "%s seeded with %d steps", PIPELINE_NAME, len(STEPS),
    )


if __name__ == "__main__":
    main()
