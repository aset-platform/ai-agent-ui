"""Seed the India Regime Daily Pipeline.

Chains all India-only regime jobs into a single dependency-ordered
pipeline (matches the existing India / USA Daily Pipeline pattern):

  Daily steps (run mon-fri 23:30 IST):
  1. Detect Market Regime          — regime_classifier_daily
  2. Notify Regime Change          — regime_change_notifier
  3. Compute Daily Factors         — compute_daily_factors
  4. Daily Brinson Attribution     — attribution_daily_brinson

  Monthly steps (skip daily, fire on 1st of month only):
  5. Refresh Top-200 Universe      — universe_snapshot_monthly
  6. Run Factor Regression         — attribution_monthly_regression

  Maintenance (every run):
  7. Compact + Backup Iceberg      — iceberg_maintenance

Idempotency:
  - Each daily step's wrapper skips if today's row already exists
    in the relevant table.
  - Each monthly step skips when today != 1st of month, AND skips
    when the month's row already exists.
  - ``force=True`` pre-deletes today's (or this-month's) row(s)
    before re-running.

The maintenance step runs unconditionally — it compacts every
hot Iceberg table including the new v3 ones (stocks.regime_history,
stocks.daily_factors, stocks.universe_snapshot, stocks.regime_hmm_state)
plus the existing stocks.ohlcv / sentiment / company_info /
analysis_summary, and takes a backup before touching anything.

Schedule: mon-fri 23:30 IST (post-close + after existing
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
    # Daily ----------------------------------------------------
    {
        "step_order": 1,
        "job_type": "regime_classifier_daily",
        "job_name": "Detect Market Regime",
    },
    {
        "step_order": 2,
        "job_type": "regime_change_notifier",
        "job_name": "Notify Regime Change",
    },
    {
        "step_order": 3,
        "job_type": "compute_daily_factors",
        "job_name": "Compute Daily Factors",
    },
    {
        "step_order": 4,
        "job_type": "attribution_daily_brinson",
        "job_name": "Daily Brinson Attribution",
    },
    # Monthly (skip on non-1st days) ---------------------------
    {
        "step_order": 5,
        "job_type": "universe_snapshot_monthly",
        "job_name": "Refresh Top-200 Universe",
    },
    {
        "step_order": 6,
        "job_type": "attribution_monthly_regression",
        "job_name": "Run Factor Regression",
    },
    # Maintenance ----------------------------------------------
    {
        "step_order": 7,
        "job_type": "iceberg_maintenance",
        "job_name": "Compact + Backup Iceberg",
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
