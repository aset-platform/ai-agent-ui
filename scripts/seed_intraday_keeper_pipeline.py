"""Seed the Intraday Bars Daily Pipeline
(ASETPLTFRM-400 slice 1d → pipeline).

Chains the slice-1d Nifty-500 daily keeper with the existing
``iceberg_maintenance`` job so each daily run finishes with a
backup + compaction on every hot Iceberg table. Mirrors the
``India Regime Daily Pipeline`` shape (``scripts/
seed_regime_india_pipeline.py``).

Steps (mon-fri 15:45 IST):

  1. Pull Nifty 500 Intraday Bars   — intraday_bars_daily_ingest
  2. Compact + Backup Iceberg       — iceberg_maintenance

Idempotency:
  - Step 1: NaN-replaceable upsert keyed on ``(ticker, bar_date,
    interval_sec)``; re-running the same window overwrites
    cleanly. Per-ticker fetch failures are logged with
    ``exc_info=True`` and the run continues (best-effort).
  - Step 2: ``execute_iceberg_maintenance`` is already
    idempotent — orphan sweep is near-zero work on a freshly
    swept table, snapshot expiry only acts on snapshots beyond
    the keep window, backup is the fail-closed step 0.

Schedule: mon-fri 15:45 IST (15 min after NSE close, after Kite
republishes today's bars). Idempotent — re-running this script
updates the pipeline row + rebuilds the step list but does NOT
duplicate.

Usage::

    docker compose exec backend python scripts/seed_intraday_keeper_pipeline.py

This script supersedes ``seed_intraday_keeper_job.py``. The
standalone ``scheduled_jobs`` row that older script created
becomes redundant once this pipeline is seeded (the pipeline
schedule fires the same job_type) — operators should disable or
delete the standalone row after migrating to the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from db.engine import get_session_factory
from sqlalchemy import text

_logger = logging.getLogger(__name__)

# Same UUID namespace as the other algo-tier seed scripts so all
# seeded pipeline/job IDs share a stable space.
_NS = uuid.UUID("e2c3a4b5-d6e7-4f89-a012-3456789abcde")
PIPELINE_NAME = "Intraday Bars Daily Pipeline"
PIPELINE_ID = str(uuid.uuid5(_NS, PIPELINE_NAME))

STEPS = [
    {
        "step_order": 1,
        "job_type": "intraday_bars_daily_ingest",
        "job_name": "Pull Nifty 500 Intraday Bars",
    },
    {
        "step_order": 2,
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
                "        'mon,tue,wed,thu,fri', '15:45', "
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
            text("DELETE FROM pipeline_steps " "WHERE pipeline_id = :pid"),
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
        "%s seeded with %d steps",
        PIPELINE_NAME,
        len(STEPS),
    )


if __name__ == "__main__":
    main()
