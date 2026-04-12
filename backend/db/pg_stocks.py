"""PostgreSQL-backed stock registry + scheduler operations."""
import logging
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.pipeline import (
    Pipeline,
    PipelineStep,
)
from backend.db.models.registry import StockRegistry
from backend.db.models.scheduler import ScheduledJob
from backend.db.models.scheduler_run import SchedulerRun

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


# -------------------------------------------------------
# Pipelines
# -------------------------------------------------------

async def get_pipelines(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Return all pipelines with their steps."""
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(Pipeline).options(
            selectinload(Pipeline.steps),
        )
    )
    pipelines = []
    for p in result.scalars().all():
        d = {
            c.name: getattr(p, c.name)
            for c in p.__table__.columns
        }
        d["steps"] = [
            {
                "step_order": s.step_order,
                "job_type": s.job_type,
                "job_name": s.job_name,
            }
            for s in sorted(
                p.steps, key=lambda s: s.step_order,
            )
        ]
        pipelines.append(d)
    return pipelines


async def upsert_pipeline(
    session: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Insert or update a pipeline + steps."""
    from sqlalchemy.orm import selectinload

    pid = data["pipeline_id"]
    result = await session.execute(
        select(Pipeline)
        .options(selectinload(Pipeline.steps))
        .where(Pipeline.pipeline_id == pid)
    )
    existing = result.scalar_one_or_none()

    if existing:
        for key in (
            "name", "scope", "enabled",
            "cron_days", "cron_time", "cron_dates",
        ):
            if key in data:
                setattr(existing, key, data[key])
        if "steps" in data:
            for s in list(existing.steps):
                await session.delete(s)
            await session.flush()
            for s in data["steps"]:
                session.add(PipelineStep(
                    pipeline_id=pid,
                    step_order=s["step_order"],
                    job_type=s["job_type"],
                    job_name=s["job_name"],
                ))
    else:
        p = Pipeline(
            pipeline_id=pid,
            name=data["name"],
            scope=data.get("scope", "all"),
            enabled=data.get("enabled", True),
            cron_days=data.get("cron_days"),
            cron_time=data.get("cron_time"),
            cron_dates=data.get("cron_dates"),
        )
        session.add(p)
        await session.flush()
        for s in data.get("steps", []):
            session.add(PipelineStep(
                pipeline_id=pid,
                step_order=s["step_order"],
                job_type=s["job_type"],
                job_name=s["job_name"],
            ))

    await session.commit()
    log.info("Upserted pipeline: %s", pid)


async def delete_pipeline(
    session: AsyncSession,
    pipeline_id: str,
) -> None:
    """Delete pipeline (cascades to steps)."""
    result = await session.execute(
        select(Pipeline).where(
            Pipeline.pipeline_id == pipeline_id,
        )
    )
    p = result.scalar_one_or_none()
    if p:
        await session.delete(p)
        await session.commit()
        log.info("Deleted pipeline: %s", pipeline_id)


# ── Scheduler Runs ──────────────────────────────────


def _run_to_dict(r: SchedulerRun) -> dict:
    """Convert ORM row to dict with ISO timestamps."""
    d: dict[str, Any] = {}
    for c in r.__table__.columns:
        v = getattr(r, c.name)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        d[c.name] = v
    return d


async def insert_scheduler_run(
    session: AsyncSession,
    run: dict[str, Any],
) -> None:
    """Insert a single scheduler run record."""
    obj = SchedulerRun(
        **{
            k: v
            for k, v in run.items()
            if hasattr(SchedulerRun, k)
        }
    )
    session.add(obj)
    await session.commit()


async def update_scheduler_run_pg(
    session: AsyncSession,
    run_id: str,
    updates: dict[str, Any],
) -> None:
    """Update fields on an existing run."""
    result = await session.execute(
        select(SchedulerRun).where(
            SchedulerRun.run_id == run_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        log.warning(
            "update_scheduler_run: %s not found",
            run_id,
        )
        return
    for k, v in updates.items():
        if hasattr(row, k):
            setattr(row, k, v)
    await session.commit()


async def get_scheduler_runs_pg(
    session: AsyncSession,
    days: int = 7,
    job_type: str | None = None,
    status: str | None = None,
    pipeline_run_id: str | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> tuple[list[dict], int]:
    """Return scheduler runs with filters.

    Returns (rows, total_count).
    """
    utc = timezone.utc
    cutoff = datetime.now(utc) - timedelta(days=days)

    filters = [SchedulerRun.started_at >= cutoff]
    if job_type:
        filters.append(
            SchedulerRun.job_type == job_type,
        )
    if status:
        filters.append(
            SchedulerRun.status == status,
        )
    if pipeline_run_id:
        filters.append(
            SchedulerRun.pipeline_run_id
            == pipeline_run_id,
        )

    # Total count.
    cnt_q = select(
        func.count(SchedulerRun.run_id),
    ).where(*filters)
    total = (await session.execute(cnt_q)).scalar() or 0

    # Paginated rows.
    q = (
        select(SchedulerRun)
        .where(*filters)
        .order_by(SchedulerRun.started_at.desc())
        .offset(offset)
    )
    if limit:
        q = q.limit(limit)
    result = await session.execute(q)
    rows = [_run_to_dict(r) for r in result.scalars()]
    return rows, total


async def get_scheduler_run_stats_pg(
    session: AsyncSession,
) -> dict:
    """Aggregate stats for the dashboard."""
    utc = timezone.utc
    cutoff = datetime.now(utc) - timedelta(days=1)
    base = select(SchedulerRun).where(
        SchedulerRun.started_at >= cutoff,
    )
    result = await session.execute(base)
    runs = result.scalars().all()
    total = len(runs)
    success = sum(
        1 for r in runs if r.status == "success"
    )
    failed = sum(
        1 for r in runs if r.status == "failed"
    )
    running = sum(
        1 for r in runs if r.status == "running"
    )
    return {
        "runs_today": total,
        "runs_today_success": success,
        "runs_today_failed": failed,
        "runs_today_running": running,
    }


async def get_pipeline_run_status_pg(
    session: AsyncSession,
    pipeline_run_id: str,
) -> list[dict]:
    """Return all runs for a pipeline_run_id."""
    result = await session.execute(
        select(SchedulerRun)
        .where(
            SchedulerRun.pipeline_run_id
            == pipeline_run_id,
        )
        .order_by(SchedulerRun.started_at.asc())
    )
    return [_run_to_dict(r) for r in result.scalars()]


async def get_last_pipeline_run_id_pg(
    session: AsyncSession,
    pipeline_id: str,
) -> str | None:
    """Get latest pipeline_run_id for a pipeline."""
    result = await session.execute(
        select(SchedulerRun.pipeline_run_id)
        .where(
            SchedulerRun.job_id == pipeline_id,
            SchedulerRun.pipeline_run_id.isnot(None),
        )
        .order_by(SchedulerRun.started_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return str(row) if row else None


async def get_last_run_for_job_pg(
    session: AsyncSession,
    job_id: str,
) -> dict | None:
    """Return the most recent run for a job."""
    result = await session.execute(
        select(SchedulerRun)
        .where(SchedulerRun.job_id == job_id)
        .order_by(SchedulerRun.started_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return _run_to_dict(row) if row else None


# ── Recommendation Engine ──────────────────────────────


def _rec_run_to_dict(r) -> dict:
    """Convert RecommendationRun ORM row to dict."""
    d: dict[str, Any] = {}
    for c in r.__table__.columns:
        v = getattr(r, c.name)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        d[c.name] = v
    return d


def _rec_to_dict(r) -> dict:
    """Convert Recommendation ORM row to dict."""
    d: dict[str, Any] = {}
    for c in r.__table__.columns:
        v = getattr(r, c.name)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        d[c.name] = v
    return d


async def insert_recommendation_run(
    session: AsyncSession,
    data: dict,
) -> str:
    """Insert a recommendation run row, return run_id."""
    from backend.db.models.recommendation import (
        RecommendationRun,
    )

    run_id = data.get("run_id", str(_uuid.uuid4()))
    obj = RecommendationRun(
        run_id=run_id,
        **{
            k: v
            for k, v in data.items()
            if k != "run_id"
            and hasattr(RecommendationRun, k)
        },
    )
    session.add(obj)
    await session.commit()
    log.info("Inserted recommendation run: %s", run_id)
    return run_id


async def insert_recommendations(
    session: AsyncSession,
    run_id: str,
    recs: list[dict],
) -> int:
    """Bulk insert recommendations for a run."""
    from backend.db.models.recommendation import (
        Recommendation,
    )

    count = 0
    for rec in recs:
        rec_id = rec.get("id", str(_uuid.uuid4()))
        obj = Recommendation(
            id=rec_id,
            run_id=run_id,
            **{
                k: v
                for k, v in rec.items()
                if k not in ("id", "run_id")
                and hasattr(Recommendation, k)
            },
        )
        session.add(obj)
        count += 1
    await session.commit()
    log.info(
        "Inserted %d recommendations for run %s",
        count, run_id,
    )
    return count


async def get_latest_recommendation_run(
    session: AsyncSession,
    user_id: str,
    scope: str = "all",
) -> dict | None:
    """Return the most recent run for a user.

    When *scope* is ``"india"`` or ``"us"``, only
    returns runs matching that scope.  ``"all"``
    returns the latest regardless of scope.
    """
    from backend.db.models.recommendation import (
        RecommendationRun,
    )

    stmt = (
        select(RecommendationRun)
        .where(RecommendationRun.user_id == user_id)
    )
    if scope != "all":
        stmt = stmt.where(
            RecommendationRun.scope == scope,
        )
    stmt = stmt.order_by(
        RecommendationRun.run_date.desc(),
    ).limit(1)

    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return _rec_run_to_dict(row) if row else None


async def get_recommendations_for_run(
    session: AsyncSession,
    run_id: str,
) -> list[dict]:
    """Return all recommendations for a run."""
    from backend.db.models.recommendation import (
        Recommendation,
    )

    result = await session.execute(
        select(Recommendation)
        .where(Recommendation.run_id == run_id)
        .order_by(Recommendation.tier, Recommendation.action)
    )
    return [_rec_to_dict(r) for r in result.scalars()]


async def get_recommendation_history(
    session: AsyncSession,
    user_id: str,
    months_back: int = 6,
) -> list[dict]:
    """Return runs with rec counts for a user."""
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationRun,
    )

    cutoff = date.today() - timedelta(
        days=months_back * 30,
    )
    result = await session.execute(
        select(
            RecommendationRun,
            func.count(Recommendation.id).label(
                "rec_count",
            ),
        )
        .outerjoin(
            Recommendation,
            Recommendation.run_id
            == RecommendationRun.run_id,
        )
        .where(
            RecommendationRun.user_id == user_id,
            RecommendationRun.run_date >= cutoff,
        )
        .group_by(RecommendationRun.run_id)
        .order_by(RecommendationRun.run_date.desc())
    )
    rows = []
    for run, cnt in result.all():
        d = _rec_run_to_dict(run)
        d["rec_count"] = cnt
        rows.append(d)
    return rows


async def get_recommendation_stats(
    session: AsyncSession,
    user_id: str,
) -> dict:
    """Aggregate stats: total runs, recs, hit rates."""
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationOutcome,
        RecommendationRun,
    )

    # Total runs
    run_cnt = await session.execute(
        select(func.count(RecommendationRun.run_id))
        .where(RecommendationRun.user_id == user_id)
    )
    total_runs = run_cnt.scalar() or 0

    # Total recs via subquery on user's runs
    rec_cnt = await session.execute(
        select(func.count(Recommendation.id))
        .join(
            RecommendationRun,
            Recommendation.run_id
            == RecommendationRun.run_id,
        )
        .where(RecommendationRun.user_id == user_id)
    )
    total_recs = rec_cnt.scalar() or 0

    # Outcome stats (hit = positive excess return)
    outcome_q = await session.execute(
        select(
            func.count(RecommendationOutcome.id),
            func.avg(
                RecommendationOutcome.return_pct,
            ),
            func.avg(
                RecommendationOutcome.excess_return_pct,
            ),
            func.sum(
                func.cast(
                    RecommendationOutcome
                    .excess_return_pct > 0,
                    Integer,
                )
            ),
        )
        .join(
            Recommendation,
            RecommendationOutcome.recommendation_id
            == Recommendation.id,
        )
        .join(
            RecommendationRun,
            Recommendation.run_id
            == RecommendationRun.run_id,
        )
        .where(RecommendationRun.user_id == user_id)
    )
    row = outcome_q.one()
    total_outcomes = row[0] or 0
    avg_return = round(float(row[1] or 0), 2)
    avg_excess = round(float(row[2] or 0), 2)
    hits = row[3] or 0
    hit_rate = (
        round(hits / total_outcomes * 100, 1)
        if total_outcomes
        else 0.0
    )

    return {
        "total_runs": total_runs,
        "total_recs": total_recs,
        "total_outcomes": total_outcomes,
        "avg_return_pct": avg_return,
        "avg_excess_return_pct": avg_excess,
        "hit_rate_pct": hit_rate,
    }


async def get_recommendations_due_for_outcome(
    session: AsyncSession,
    today: date,
) -> list[dict]:
    """Find recs due for 30/60/90-day outcome check."""
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationOutcome,
    )
    from sqlalchemy import and_

    results: list[dict] = []
    for days in (30, 60, 90):
        cutoff = today - timedelta(days=days)
        # Subquery: already-checked recs for this day
        checked = (
            select(
                RecommendationOutcome.recommendation_id,
            )
            .where(
                RecommendationOutcome.days_elapsed == days,
            )
            .correlate(Recommendation)
            .exists()
        )
        q = (
            select(Recommendation)
            .where(
                and_(
                    Recommendation.status == "active",
                    func.date(
                        Recommendation.created_at,
                    ) <= cutoff,
                    ~checked,
                ),
            )
        )
        rows = await session.execute(q)
        for r in rows.scalars():
            d = _rec_to_dict(r)
            d["days_due"] = days
            results.append(d)
    return results


async def insert_recommendation_outcome(
    session: AsyncSession,
    rec_id: str,
    check_date: date,
    days: int,
    price: float,
    ret: float,
    bench: float,
    excess: float,
    label: str,
) -> None:
    """Insert an outcome row for a recommendation."""
    from backend.db.models.recommendation import (
        RecommendationOutcome,
    )

    obj = RecommendationOutcome(
        id=str(_uuid.uuid4()),
        recommendation_id=rec_id,
        check_date=check_date,
        days_elapsed=days,
        actual_price=price,
        return_pct=ret,
        benchmark_return_pct=bench,
        excess_return_pct=excess,
        outcome_label=label,
    )
    session.add(obj)
    await session.commit()


async def update_recommendation_status(
    session: AsyncSession,
    user_id: str,
    ticker: str,
    actions: list[str],
    new_status: str,
) -> int:
    """Match active recs by user+ticker+action list.

    Returns count of updated rows.
    """
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationRun,
    )

    # Get run_ids for this user
    run_ids_q = (
        select(RecommendationRun.run_id)
        .where(RecommendationRun.user_id == user_id)
    )
    result = await session.execute(
        select(Recommendation)
        .where(
            Recommendation.run_id.in_(run_ids_q),
            Recommendation.ticker == ticker,
            Recommendation.action.in_(actions),
            Recommendation.status == "active",
        )
    )
    rows = result.scalars().all()
    count = 0
    for row in rows:
        row.status = new_status
        row.acted_on_date = date.today()
        count += 1
    if count:
        await session.commit()
        log.info(
            "Updated %d recs for %s/%s -> %s",
            count, user_id, ticker, new_status,
        )
    return count


async def expire_old_recommendations(
    session: AsyncSession,
    user_id: str,
    current_run_id: str,
) -> int:
    """Expire active recs from prior runs for user."""
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationRun,
    )

    run_ids_q = (
        select(RecommendationRun.run_id)
        .where(
            RecommendationRun.user_id == user_id,
            RecommendationRun.run_id != current_run_id,
        )
    )
    result = await session.execute(
        select(Recommendation)
        .where(
            Recommendation.run_id.in_(run_ids_q),
            Recommendation.status == "active",
        )
    )
    rows = result.scalars().all()
    count = 0
    for row in rows:
        row.status = "expired"
        count += 1
    if count:
        await session.commit()
        log.info(
            "Expired %d old recs for user %s",
            count, user_id,
        )
    return count


async def expire_stale_recommendations(
    session: AsyncSession,
    today: date,
) -> int:
    """Expire active recs older than 90 days."""
    from backend.db.models.recommendation import (
        Recommendation,
    )

    cutoff = today - timedelta(days=90)
    result = await session.execute(
        select(Recommendation)
        .where(
            Recommendation.status == "active",
            func.date(
                Recommendation.created_at,
            ) <= cutoff,
        )
    )
    rows = result.scalars().all()
    count = 0
    for row in rows:
        row.status = "expired"
        count += 1
    if count:
        await session.commit()
        log.info("Expired %d stale recs (>90d)", count)
    return count
