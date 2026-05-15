"""Seed the scheduled_jobs row for the FE-5.1 EOD flush job
(ASETPLTFRM-417).

Standalone scheduled job — runs Mon-Fri 15:30 IST. Drains the
Redis ``algo:live:snapshots:*:{trading_date}`` LISTs that the
live runtime populates throughout the day and writes ONE
Iceberg commit per user to
``stocks.trade_feature_snapshots``.

NOT a pipeline step — the Intraday Bars Daily Pipeline runs at
15:45 IST (15 min later) and writes to different Iceberg
tables, so the two pipelines never compete on the same commit
lock.

Idempotent — uses ``ON CONFLICT (name) DO UPDATE`` and a stable
UUID derived from the job name so re-runs adjust the schedule
in place rather than duplicating rows.

Usage::

    docker compose exec backend python \\
        scripts/seed_trade_feature_snapshots_eod_flush.py
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import text

from db.engine import get_session_factory

_logger = logging.getLogger(__name__)

_NS = uuid.UUID("e2c3a4b5-d6e7-4f89-a012-3456789abcde")

JOB = {
    "name": "Trade Feature Snapshots EOD Flush",
    "job_type": "trade_feature_snapshots_eod_flush",
    "cron_days": "mon,tue,wed,thu,fri",
    "cron_time": "15:30",
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
                "(job_id, name, job_type, cron_days, "
                " cron_time, cron_dates, scope, "
                " enabled, force) "
                "VALUES (:jid, :name, :jt, :cd, :ct, "
                "        :cdates, :scope, TRUE, FALSE) "
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
        _logger.info(
            "seeded %s -> %s (%s %s)",
            JOB["name"],
            JOB["job_type"],
            JOB["cron_days"],
            JOB["cron_time"],
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())
    _logger.info("FE-5.1 EOD flush schedule seed complete")


if __name__ == "__main__":
    main()
