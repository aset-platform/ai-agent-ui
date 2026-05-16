"""Seed the Intraday Bars Daily Pipeline
(ASETPLTFRM-400 slice 1d → pipeline).

Chains the slice-1d Nifty-500 daily keeper with the existing
``iceberg_maintenance`` job so each daily run finishes with a
backup + compaction on every hot Iceberg table. Mirrors the
``India Regime Daily Pipeline`` shape (``scripts/
seed_regime_india_pipeline.py``).

Steps (mon-fri 15:45 IST):

  1. Pull Nifty 500 Intraday Bars   — intraday_bars_daily_ingest
  2. Fetch Index Intraday Bars      — index_intraday_bars_daily_ingest
  3. Compute Intraday Features      — intraday_features_daily_compute
  4. Trim Bars Older Than 4 Years   — intraday_bars_retention
  5. Compact + Backup Iceberg       — iceberg_maintenance

Step 2 (FE-6 — first slice of Phase 2) pulls NSE index OHLCV
(NIFTY 50 + sector indices) into ``stocks.index_intraday_bars``.
Placed BEFORE feature compute because FE-8 (Phase 2)
cross-sectional features (RS-vs-NIFTY, sector rotation) will read
both the per-ticker bars (step 1 output) AND the index bars (this
step) from the same daily compute. FE-6 only adds the data
surface; the FE-8 consumer wires the index bars into the compute
job in a later slice — until then this step is a no-op for
downstream consumers but pre-positions the universe so the
cutover is a config flip rather than a data backfill.

Step 3 (FE-3) reads the freshly-ingested bars and writes the
centralized feature engine's panel into ``stocks.intraday_features``
so daily-cadence consumers see today's features without re-running
the compute inline. Placed AFTER ingest (depends on the new rows)
and BEFORE retention (logical ordering — features land same-day so
there's no data-race concern, but keeping the order consistent
helps when operators replay a single step).

Step 4 maintains a rolling 4-year window so the table doesn't
grow unboundedly — Backtest's max window is ASETPLTFRM-400's
``2022-05-13 → today`` and there's no reason to retain
pre-window history. Placed between feature compute and maintenance
so the same maintenance run that compacts also reclaims the
tombstones from the deleted rows.

Idempotency:
  - Step 1: NaN-replaceable upsert keyed on ``(ticker, bar_date,
    interval_sec)``; re-running the same window overwrites
    cleanly. Per-ticker fetch failures are logged with
    ``exc_info=True`` and the run continues (best-effort).
  - Step 2: ``tbl.delete(LessThan("bar_date", cutoff_iso))`` —
    re-running after the cutoff has already been applied
    matches zero rows.
  - Step 3: ``execute_iceberg_maintenance`` is already
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
import json
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
        "job_name": "Fetch Intraday Bars (Nifty 500)",
    },
    {
        "step_order": 2,
        "job_type": "index_intraday_bars_daily_ingest",
        "job_name": "Fetch Intraday Bars (Indices)",
    },
    {
        "step_order": 3,
        "job_type": "intraday_features_daily_compute",
        "job_name": "Compute Intraday Features",
    },
    {
        "step_order": 4,
        "job_type": "intraday_bars_retention",
        "job_name": "Trim to 4-Year Retention",
    },
    {
        "step_order": 5,
        "job_type": "iceberg_maintenance",
        "job_name": "Compact + Backup Iceberg",
        # ASETPLTFRM-418: scope maintenance to the
        # tables this pipeline actually writes (intraday
        # family only). Avoids the full-warehouse rsync
        # + 14-table compact loop when only ~4 tables
        # need attention.
        # ASETPLTFRM-421: include the algo namespace
        # tables that the LiveRuntime writes during the
        # session (paper + live event log, resampled
        # bars from the tick stream). algo.events
        # ballooned to 11 GB on 2026-05-12 the LAST time
        # it was missing from a maintenance scope; both
        # are also in _HOT_ICEBERG_TABLES + ALL_TABLES
        # but the scoped pipeline run is what catches
        # them inside the same daily compact + backup
        # window.
        "payload": {
            "tables": [
                "stocks.intraday_bars",
                "stocks.index_intraday_bars",
                "stocks.intraday_features",
                "stocks.trade_feature_snapshots",
                "algo.events",
                "algo.intraday_bars",
            ],
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
