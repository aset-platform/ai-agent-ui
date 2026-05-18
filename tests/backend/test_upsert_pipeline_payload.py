"""Regression test for ASETPLTFRM-428.

The PATCH /admin/scheduler/pipelines/{id} route flowed an update
through ``upsert_pipeline()`` that wrote ``payload={}`` whenever
the incoming ``steps[*]`` lacked an explicit payload field.  The
admin UI's ``PipelineForm`` never sent the payload at all, so
every save through the UI silently erased scoped
``iceberg_maintenance`` table lists — turning a fast scoped
3-min run into the full-warehouse 24-min sweep.

The fix in ``backend/db/pg_stocks.py::upsert_pipeline`` snapshots
the existing ``(job_type, step_order) → payload`` map before the
delete+reinsert cycle, then PRESERVES payloads on steps that
don't carry one in the incoming body.  Explicit
``payload: {}`` or ``payload: null`` from the caller still wipes
(escape hatch).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.db.pg_stocks import (
    get_pipelines,
    upsert_pipeline,
)


@pytest_asyncio.fixture
async def session_factory():
    """Fresh PG session per call — mirrors production where each
    FastAPI request handler gets its own session.  Sharing a
    session across multiple ``upsert_pipeline`` invocations
    triggers SQLAlchemy identity-map caching that doesn't occur
    in the real request lifecycle.

    ``pipeline_steps.payload`` is JSONB which SQLite can't
    render, so we use the local dev PG via
    ``disposable_pg_session``.
    """
    from contextlib import asynccontextmanager

    from backend.db.engine import disposable_pg_session

    @asynccontextmanager
    async def make():
        async with disposable_pg_session() as s:
            yield s

    return make


@pytest_asyncio.fixture
async def seeded_pipeline(session_factory):
    """Pipeline with a step that carries a scoped maintenance
    payload — analogous to the production Intraday Bars Daily
    Pipeline.
    """
    pid = str(uuid.uuid4())
    data = {
        "pipeline_id": pid,
        "name": "Test Intraday Pipeline",
        "scope": "india",
        "enabled": True,
        "cron_days": "mon,tue,wed,thu,fri",
        "cron_time": "15:45",
        "cron_dates": "",
        "steps": [
            {
                "step_order": 1,
                "job_type": "intraday_bars_daily_ingest",
                "job_name": "Fetch Bars",
            },
            {
                "step_order": 2,
                "job_type": "iceberg_maintenance",
                "job_name": "Compact + Backup",
                "payload": {
                    "tables": [
                        "stocks.intraday_bars",
                        "algo.events",
                        "algo.intraday_bars",
                    ],
                },
            },
        ],
    }
    async with session_factory() as s:
        await upsert_pipeline(s, data)
    yield pid
    # Cleanup — cascade deletes pipeline_steps.
    async with session_factory() as s:
        await s.execute(
            text(
                "DELETE FROM pipelines WHERE pipeline_id = :pid",
            ),
            {"pid": pid},
        )
        await s.commit()


# ---------------------------------------------------------------
# The bug it must NOT regress to
# ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_without_payload_preserves_existing(
    session_factory, seeded_pipeline,
) -> None:
    """The admin UI's PipelineForm round-trips steps without
    ``payload``.  Under the old behaviour, this PATCH wiped the
    scoped table list to ``{}``.  The fix MUST preserve the
    prior payload when the incoming step has no ``payload`` key.
    """
    update_data = {
        "pipeline_id": seeded_pipeline,
        "name": "Test Intraday Pipeline (edited)",
        "steps": [
            # NOTE: no ``payload`` key — exactly what
            # PipelineForm.tsx sends today.
            {
                "step_order": 1,
                "job_type": "intraday_bars_daily_ingest",
                "job_name": "Fetch Bars",
            },
            {
                "step_order": 2,
                "job_type": "iceberg_maintenance",
                "job_name": "Compact + Backup",
            },
        ],
    }
    async with session_factory() as s:
        await upsert_pipeline(s, update_data)

    async with session_factory() as s:
        pipelines = await get_pipelines(s)
    p = next(
        x for x in pipelines
        if x["pipeline_id"] == seeded_pipeline
    )
    maint_step = next(
        s for s in p["steps"]
        if s["job_type"] == "iceberg_maintenance"
    )
    assert maint_step["payload"] == {
        "tables": [
            "stocks.intraday_bars",
            "algo.events",
            "algo.intraday_bars",
        ],
    }, (
        "Scoped payload was wiped on PATCH — ASETPLTFRM-428 "
        "regression"
    )
    # Side-effect: name update DID land.
    assert p["name"] == "Test Intraday Pipeline (edited)"


# ---------------------------------------------------------------
# The opposite direction — caller can still update payload
# ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_with_explicit_payload_overrides(
    session_factory, seeded_pipeline,
) -> None:
    """When a caller (seed script, future API consumer) sends a
    new payload, it MUST replace the existing one.  Don't trade
    one silent bug for another.
    """
    new_tables = ["stocks.ohlcv", "stocks.analysis_summary"]
    update_data = {
        "pipeline_id": seeded_pipeline,
        "steps": [
            {
                "step_order": 1,
                "job_type": "intraday_bars_daily_ingest",
                "job_name": "Fetch Bars",
            },
            {
                "step_order": 2,
                "job_type": "iceberg_maintenance",
                "job_name": "Compact + Backup",
                "payload": {"tables": new_tables},
            },
        ],
    }
    async with session_factory() as s:
        await upsert_pipeline(s, update_data)

    async with session_factory() as s:
        pipelines = await get_pipelines(s)
    maint = next(
        s for p in pipelines if p["pipeline_id"] == seeded_pipeline
        for s in p["steps"]
        if s["job_type"] == "iceberg_maintenance"
    )
    assert maint["payload"] == {"tables": new_tables}


@pytest.mark.asyncio
async def test_patch_explicit_empty_payload_wipes(
    session_factory, seeded_pipeline,
) -> None:
    """Explicit ``payload: {}`` is an escape hatch — caller is
    saying "I really mean empty".  Preserve that intent.  Only
    *absent* payload triggers the preservation logic.
    """
    update_data = {
        "pipeline_id": seeded_pipeline,
        "steps": [
            {
                "step_order": 1,
                "job_type": "intraday_bars_daily_ingest",
                "job_name": "Fetch Bars",
            },
            {
                "step_order": 2,
                "job_type": "iceberg_maintenance",
                "job_name": "Compact + Backup",
                "payload": {},
            },
        ],
    }
    async with session_factory() as s:
        await upsert_pipeline(s, update_data)

    async with session_factory() as s:
        pipelines = await get_pipelines(s)
    maint = next(
        s for p in pipelines if p["pipeline_id"] == seeded_pipeline
        for s in p["steps"]
        if s["job_type"] == "iceberg_maintenance"
    )
    assert maint["payload"] == {}
