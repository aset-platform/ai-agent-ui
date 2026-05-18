"""Seed the Weekly Long-Tail Iceberg Maintenance pipeline
(ASETPLTFRM-422).

Six tables get < 10 commits/day each but accumulate snapshot
metadata over time — by 2026-05-16 the worst offenders had
*1,682×* (``stocks.data_gaps``) and *881×*
(``stocks.registry``) disk-files-to-active-files bloat ratios.
The daily Iceberg maintenance sweep skips them on purpose
(scoped payloads only include hot tables); a weekly pass keeps
the long tail healthy without burning daily compute.

Scope (per CLAUDE.md §4.3 #22 universal rule, "low-write" branch):

* ``stocks.llm_pricing``           — handful of model rows, refreshed monthly
* ``stocks.portfolio_transactions``— PG-mirrored ledger, append-on-trade
* ``stocks.chat_audit_log``        — 1 row per chat turn
* ``stocks.query_log``             — 1 row per LLM tool call
* ``stocks.data_gaps``             — gap-fill heartbeats
* ``stocks.registry``              — universe-membership log

Cron: Sundays 03:00 IST. Single ``iceberg_maintenance`` step
with the table list in the payload, so it reuses the existing
job + the same observability hooks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from db.engine import get_session_factory
from sqlalchemy import text

_logger = logging.getLogger(__name__)

# Share the seed-script UUID namespace so all pipeline IDs in
# this codebase live in a single deterministic space.
_NS = uuid.UUID("e2c3a4b5-d6e7-4f89-a012-3456789abcde")
PIPELINE_NAME = "Weekly Long-Tail Iceberg Maintenance"
PIPELINE_ID = str(uuid.uuid5(_NS, PIPELINE_NAME))

LONGTAIL_TABLES = [
    "stocks.llm_pricing",
    "stocks.portfolio_transactions",
    "stocks.chat_audit_log",
    "stocks.query_log",
    "stocks.data_gaps",
    "stocks.registry",
]

STEPS = [
    {
        # Tiered retention for algo.events MUST run before the
        # compaction step so the latter is the one that actually
        # rewrites the surviving rows into bigger parquet files.
        # Otherwise we'd compact, then immediately delete most of
        # what we just compacted.
        "step_order": 1,
        "job_type": "algo_events_retention",
        "job_name": "Trim algo.events (tiered retention)",
        "payload": {},
    },
    {
        "step_order": 2,
        "job_type": "iceberg_maintenance",
        "job_name": "Compact + Backup Long-Tail Iceberg",
        "payload": {
            "tables": LONGTAIL_TABLES + ["algo.events"],
        },
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
                "        'sun', '03:00', "
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
            _logger.info(
                "step %d: %s (%s)",
                step["step_order"],
                step["job_name"],
                step["job_type"],
            )
        await session.commit()
    _logger.info(
        "%s seeded with %d step(s)",
        PIPELINE_NAME,
        len(STEPS),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(seed())
