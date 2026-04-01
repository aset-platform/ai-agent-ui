"""PostgreSQL-backed stock registry + scheduler operations."""
import logging
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.registry import StockRegistry
from backend.db.models.scheduler import ScheduledJob

log = logging.getLogger(__name__)


async def get_registry(
    session: AsyncSession,
    ticker: str | None = None,
) -> dict | pd.DataFrame | None:
    """Get registry entry by ticker or all entries."""
    if ticker:
        result = await session.execute(
            select(StockRegistry).where(
                StockRegistry.ticker == ticker
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return {
            c.name: getattr(row, c.name)
            for c in row.__table__.columns
        }

    result = await session.execute(select(StockRegistry))
    rows = [
        {
            c.name: getattr(r, c.name)
            for c in r.__table__.columns
        }
        for r in result.scalars().all()
    ]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


async def upsert_registry(
    session: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Insert or update registry entry by ticker."""
    ticker = data["ticker"]
    result = await session.execute(
        select(StockRegistry).where(
            StockRegistry.ticker == ticker
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        for key, value in data.items():
            if key != "ticker" and hasattr(existing, key):
                setattr(existing, key, value)
    else:
        session.add(StockRegistry(**{
            k: v for k, v in data.items()
            if hasattr(StockRegistry, k)
        }))

    await session.commit()
    log.info("Upserted registry: %s", ticker)


async def get_scheduled_jobs(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Return all scheduled job definitions."""
    result = await session.execute(select(ScheduledJob))
    return [
        {
            c.name: getattr(j, c.name)
            for c in j.__table__.columns
        }
        for j in result.scalars().all()
    ]


async def upsert_scheduled_job(
    session: AsyncSession,
    job: dict[str, Any],
) -> None:
    """Insert or update scheduled job by job_id."""
    job_id = job["job_id"]
    result = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.job_id == job_id
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        for key, value in job.items():
            if key != "job_id" and hasattr(existing, key):
                setattr(existing, key, value)
    else:
        session.add(ScheduledJob(**{
            k: v for k, v in job.items()
            if hasattr(ScheduledJob, k)
        }))

    await session.commit()
    log.info("Upserted job: %s", job_id)


async def delete_scheduled_job(
    session: AsyncSession,
    job_id: str,
) -> None:
    """Delete scheduled job by job_id."""
    result = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.job_id == job_id
        )
    )
    job = result.scalar_one_or_none()
    if job:
        await session.delete(job)
        await session.commit()
        log.info("Deleted job: %s", job_id)
