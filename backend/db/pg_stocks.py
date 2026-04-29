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
from backend.db.models.sentiment_dormant import (
    SentimentDormant,
)

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


# ── Sentiment dormancy ──────────────────────────────────


# Capped exponential cooldown (days). Index = number of
# consecutive empty fetches; floor 1 → 2 days, cap at 30.
_DORMANT_COOLDOWN_DAYS = (2, 4, 8, 16, 30)


def _compute_next_retry(
    consecutive_empty: int,
    *,
    now: datetime | None = None,
) -> datetime:
    """Return next retry timestamp for a given streak."""
    streak = max(1, consecutive_empty)
    idx = min(streak - 1, len(_DORMANT_COOLDOWN_DAYS) - 1)
    days = _DORMANT_COOLDOWN_DAYS[idx]
    base = now or datetime.now(timezone.utc)
    return base + timedelta(days=days)


async def get_dormant_tickers(
    session: AsyncSession,
) -> set[str]:
    """Return tickers whose retry window hasn't lifted.

    Used by ``execute_run_sentiment`` to skip per-ticker
    headline fetches for tickers known to return zero
    headlines.
    """
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(SentimentDormant.ticker).where(
            SentimentDormant.next_retry_at.isnot(None),
            SentimentDormant.next_retry_at > now,
        )
    )
    return {r[0] for r in result.all()}


async def get_dormant_eligible_for_probe(
    session: AsyncSession,
    limit: int | None = None,
) -> list[str]:
    """Dormant tickers ordered by oldest last_checked_at.

    Used for the periodic re-discovery probe. Limit caps
    the sample size; ``None`` returns all.
    """
    stmt = (
        select(SentimentDormant.ticker)
        .where(
            SentimentDormant.next_retry_at.isnot(None),
        )
        .order_by(SentimentDormant.last_checked_at.asc())
    )
    if limit is not None and limit > 0:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return [r[0] for r in result.all()]


async def record_empty_fetch(
    session: AsyncSession,
    ticker: str,
) -> None:
    """Bump consecutive_empty + reschedule next retry."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(SentimentDormant).where(
            SentimentDormant.ticker == ticker,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        new_count = 1
        row = SentimentDormant(
            ticker=ticker,
            consecutive_empty=new_count,
            last_checked_at=now,
            next_retry_at=_compute_next_retry(
                new_count, now=now,
            ),
            last_headline_count=0,
        )
        session.add(row)
    else:
        row.consecutive_empty = (
            row.consecutive_empty + 1
        )
        row.last_checked_at = now
        row.next_retry_at = _compute_next_retry(
            row.consecutive_empty, now=now,
        )
    await session.commit()


async def record_successful_fetch(
    session: AsyncSession,
    ticker: str,
    headline_count: int,
) -> None:
    """Clear dormancy state on a successful fetch."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(SentimentDormant).where(
            SentimentDormant.ticker == ticker,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        # Nothing to clear; only insert if the row would
        # carry useful diagnostics. Skip to keep table
        # tight (we only persist when there's dormancy).
        return
    row.consecutive_empty = 0
    row.last_checked_at = now
    row.next_retry_at = None
    row.last_headline_count = headline_count
    row.last_seen_headlines_at = now
    await session.commit()


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


async def get_scheduler_run_by_id(
    session: AsyncSession,
    run_id: str,
) -> dict | None:
    """Return a single scheduler run by run_id."""
    result = await session.execute(
        select(SchedulerRun).where(
            SchedulerRun.run_id == run_id,
        )
    )
    row = result.scalar_one_or_none()
    return _run_to_dict(row) if row else None


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
    exclude_test: bool = True,
) -> dict | None:
    """Return the most recent run for a user.

    When *scope* is ``"india"`` or ``"us"``, only
    returns runs matching that scope.  ``"all"``
    returns the latest regardless of scope.

    When *exclude_test* is True (the default), rows
    with ``run_type='admin_test'`` are filtered out —
    those are superuser-only test runs and must never
    surface in user-facing consumers.
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
    if exclude_test:
        stmt = stmt.where(
            RecommendationRun.run_type != "admin_test",
        )
    stmt = stmt.order_by(
        RecommendationRun.created_at.desc(),
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
    exclude_test: bool = True,
    scope: str | None = None,
) -> list[dict]:
    """Return runs with rec counts for a user.

    When *exclude_test* is True (the default),
    ``run_type='admin_test'`` rows are filtered out so
    user-facing history never reveals superuser test
    runs.  Each row also reports ``acted_on_count``
    (recs with a non-null ``acted_on_date``).

    When *scope* is ``"india"`` or ``"us"`` the rows
    are restricted to that scope so the History sub-tab
    can match the Performance sub-tab's filter.
    """
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationRun,
    )

    cutoff = date.today() - timedelta(
        days=months_back * 30,
    )
    acted_sum = func.sum(
        func.cast(
            Recommendation.acted_on_date.isnot(None),
            Integer,
        )
    ).label("acted_on_count")
    stmt = (
        select(
            RecommendationRun,
            func.count(Recommendation.id).label(
                "rec_count",
            ),
            acted_sum,
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
    )
    if exclude_test:
        stmt = stmt.where(
            RecommendationRun.run_type != "admin_test",
        )
    if scope in ("india", "us"):
        stmt = stmt.where(
            RecommendationRun.scope == scope,
        )
    stmt = stmt.group_by(
        RecommendationRun.run_id,
    ).order_by(
        RecommendationRun.created_at.desc(),
        RecommendationRun.run_date.desc(),
    )
    result = await session.execute(stmt)
    rows = []
    for run, cnt, acted in result.all():
        d = _rec_run_to_dict(run)
        d["rec_count"] = cnt
        d["acted_on_count"] = int(acted or 0)
        rows.append(d)
    return rows


async def get_recommendation_stats(
    session: AsyncSession,
    user_id: str,
    scope: str | None = None,
) -> dict:
    """Aggregate stats: total runs, recs, hit rates.

    When *scope* is ``"india"`` or ``"us"`` the stats
    are restricted to that scope.  Admin test runs are
    always excluded so user-facing dashboards never
    reflect superuser scratch data.
    """
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationOutcome,
        RecommendationRun,
    )

    def _scoped(stmt):
        stmt = stmt.where(
            RecommendationRun.user_id == user_id,
            RecommendationRun.run_type != "admin_test",
        )
        if scope in ("india", "us"):
            stmt = stmt.where(
                RecommendationRun.scope == scope,
            )
        return stmt

    # Total runs
    run_cnt = await session.execute(
        _scoped(
            select(
                func.count(RecommendationRun.run_id)
            )
        )
    )
    total_runs = run_cnt.scalar() or 0

    # Total recs via subquery on user's runs
    rec_cnt = await session.execute(
        _scoped(
            select(func.count(Recommendation.id))
            .join(
                RecommendationRun,
                Recommendation.run_id
                == RecommendationRun.run_id,
            )
        )
    )
    total_recs = rec_cnt.scalar() or 0

    # Outcome stats (hit = positive excess return)
    outcome_q = await session.execute(
        _scoped(
            select(
                func.count(RecommendationOutcome.id),
                func.avg(
                    RecommendationOutcome.return_pct,
                ),
                func.avg(
                    RecommendationOutcome
                    .excess_return_pct,
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
        )
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

    # Total recs acted on (non-null acted_on_date).
    acted_q = await session.execute(
        _scoped(
            select(func.count(Recommendation.id))
            .join(
                RecommendationRun,
                Recommendation.run_id
                == RecommendationRun.run_id,
            )
            .where(
                Recommendation.acted_on_date.isnot(
                    None,
                ),
            )
        )
    )
    total_acted_on = acted_q.scalar() or 0

    return {
        "total_runs": total_runs,
        "total_recs": total_recs,
        "total_outcomes": total_outcomes,
        "total_acted_on": total_acted_on,
        "avg_return_pct": avg_return,
        "avg_excess_return_pct": avg_excess,
        "hit_rate_pct": hit_rate,
    }


_PERF_BUCKET_SQL = """
WITH bucketed AS (
    SELECT
        date_trunc(
            :granularity,
            r.created_at AT TIME ZONE 'Asia/Kolkata'
        )::date AS bucket_start,
        r.id   AS rec_id,
        r.acted_on_date,
        (
            (NOW() - r.created_at)
            < (INTERVAL '1 day' * :pending_days)
        ) AS is_pending
    FROM stocks.recommendations r
    JOIN stocks.recommendation_runs run
      ON r.run_id = run.run_id
    WHERE run.user_id = :user_id
      AND run.run_type != 'admin_test'
      AND r.created_at >= (
          NOW() - (INTERVAL '1 month' * :months_back)
      )
      AND (
          CAST(:scope AS VARCHAR) IS NULL
          OR run.scope = CAST(:scope AS VARCHAR)
      )
      AND (
          NOT CAST(:acted_on_only AS BOOLEAN)
          OR r.acted_on_date IS NOT NULL
      )
),
-- Threshold for a rec to count as "pending":
-- younger than the primary horizon for the chosen
-- granularity. Weekly = 7d, monthly = 30d,
-- quarterly = 90d. Aligned with the horizon the
-- frontend emphasises in the chart.
totals AS (
    SELECT
        bucket_start,
        COUNT(*)::int AS total_recs,
        SUM(
            CASE WHEN acted_on_date IS NOT NULL
                 THEN 1 ELSE 0 END
        )::int AS acted_on_count,
        SUM(
            CASE WHEN is_pending THEN 1 ELSE 0 END
        )::int AS pending_count
    FROM bucketed
    GROUP BY bucket_start
),
outcomes_by_horizon AS (
    SELECT
        b.bucket_start,
        o.days_elapsed,
        AVG(o.return_pct)             AS avg_return,
        AVG(o.excess_return_pct)      AS avg_excess,
        (
            SUM(
                CASE WHEN o.excess_return_pct > 0
                     THEN 1 ELSE 0 END
            )::float
            / NULLIF(COUNT(*), 0) * 100
        ) AS hit_rate
    FROM bucketed b
    JOIN stocks.recommendation_outcomes o
      ON o.recommendation_id = b.rec_id
    WHERE o.days_elapsed IN (7, 30, 60, 90)
    GROUP BY b.bucket_start, o.days_elapsed
)
SELECT
    t.bucket_start,
    t.total_recs,
    t.acted_on_count,
    t.pending_count,
    o7.avg_return   AS avg_return_7d,
    o7.avg_excess   AS avg_excess_7d,
    o7.hit_rate     AS hit_rate_7d,
    o30.avg_return  AS avg_return_30d,
    o30.avg_excess  AS avg_excess_30d,
    o30.hit_rate    AS hit_rate_30d,
    o60.avg_return  AS avg_return_60d,
    o60.avg_excess  AS avg_excess_60d,
    o60.hit_rate    AS hit_rate_60d,
    o90.avg_return  AS avg_return_90d,
    o90.avg_excess  AS avg_excess_90d,
    o90.hit_rate    AS hit_rate_90d
FROM totals t
LEFT JOIN outcomes_by_horizon o7
       ON o7.bucket_start = t.bucket_start
      AND o7.days_elapsed = 7
LEFT JOIN outcomes_by_horizon o30
       ON o30.bucket_start = t.bucket_start
      AND o30.days_elapsed = 30
LEFT JOIN outcomes_by_horizon o60
       ON o60.bucket_start = t.bucket_start
      AND o60.days_elapsed = 60
LEFT JOIN outcomes_by_horizon o90
       ON o90.bucket_start = t.bucket_start
      AND o90.days_elapsed = 90
ORDER BY t.bucket_start ASC
"""


def _bucket_label(bucket_start: date, granularity: str) -> str:
    """Human-friendly label for a bucket start date.

    week → ``2026-W17`` (ISO week)
    month → ``Apr 2026``
    quarter → ``Q2 2026``
    """
    if granularity == "week":
        iso_year, iso_week, _ = bucket_start.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if granularity == "month":
        return bucket_start.strftime("%b %Y")
    if granularity == "quarter":
        q = (bucket_start.month - 1) // 3 + 1
        return f"Q{q} {bucket_start.year}"
    return bucket_start.isoformat()


async def get_recommendation_performance_buckets(
    session: AsyncSession,
    user_id: str,
    *,
    granularity: str,
    months_back: int = 14,
    scope: str | None = None,
    acted_on_only: bool = False,
) -> dict:
    """Return cohort-bucketed performance for the user.

    Cohort axis: a bucket groups recommendations by
    when they were *issued* (``recommendations.created_at``
    truncated to week / month / quarter in IST). Outcome
    metrics for each bucket aggregate the 30 / 60 / 90-day
    post-issuance checks already persisted in
    ``recommendation_outcomes``.

    Hit rate convention matches ``get_recommendation_stats``
    (``excess_return_pct > 0``) so the new tab's KPIs are
    directly comparable with the existing stats KPIs.

    Args:
        granularity: ``"week"``, ``"month"``, or ``"quarter"``.
        months_back: 1..14 months window.
        scope: Optional ``"india"`` / ``"us"`` filter.
        acted_on_only: Restrict cohort to recs the user
            actually acted on (``acted_on_date`` non-null).

    Returns:
        ``{"buckets": [...], "summary": {...}}`` — buckets
        oldest-to-newest. Empty buckets are omitted (no
        recs issued in that period).
    """
    from sqlalchemy import text

    if granularity not in ("week", "month", "quarter"):
        raise ValueError(
            "granularity must be week|month|quarter"
        )
    months_back = max(1, min(int(months_back), 14))
    if scope is not None and scope not in ("india", "us"):
        scope = None

    # pending_count threshold matches the primary
    # horizon the frontend emphasises for the chosen
    # granularity. A rec younger than this is "pending"
    # — its outcome at the primary horizon hasn't been
    # computed yet.
    pending_days = {
        "week": 7,
        "month": 30,
        "quarter": 90,
    }[granularity]

    result = await session.execute(
        text(_PERF_BUCKET_SQL),
        {
            "user_id": user_id,
            "granularity": granularity,
            "months_back": months_back,
            "scope": scope,
            "acted_on_only": acted_on_only,
            "pending_days": pending_days,
        },
    )
    rows = result.mappings().all()

    buckets: list[dict] = []
    s_total = 0
    s_acted = 0
    s_pending = 0
    # Running sums for summary hit-rate / returns. We
    # cannot just average per-bucket averages (Simpson's
    # paradox); compute on raw outcomes via a follow-up
    # cheap aggregate.
    for r in rows:
        bs: date = r["bucket_start"]
        buckets.append({
            "bucket_start": bs.isoformat(),
            "bucket_label": _bucket_label(
                bs, granularity,
            ),
            "total_recs": int(r["total_recs"] or 0),
            "acted_on_count": int(
                r["acted_on_count"] or 0,
            ),
            "pending_count": int(
                r["pending_count"] or 0,
            ),
            "hit_rate_7d": (
                round(float(r["hit_rate_7d"]), 1)
                if r["hit_rate_7d"] is not None
                else None
            ),
            "hit_rate_30d": (
                round(float(r["hit_rate_30d"]), 1)
                if r["hit_rate_30d"] is not None
                else None
            ),
            "hit_rate_60d": (
                round(float(r["hit_rate_60d"]), 1)
                if r["hit_rate_60d"] is not None
                else None
            ),
            "hit_rate_90d": (
                round(float(r["hit_rate_90d"]), 1)
                if r["hit_rate_90d"] is not None
                else None
            ),
            "avg_return_7d": (
                round(float(r["avg_return_7d"]), 2)
                if r["avg_return_7d"] is not None
                else None
            ),
            "avg_return_30d": (
                round(float(r["avg_return_30d"]), 2)
                if r["avg_return_30d"] is not None
                else None
            ),
            "avg_return_60d": (
                round(float(r["avg_return_60d"]), 2)
                if r["avg_return_60d"] is not None
                else None
            ),
            "avg_return_90d": (
                round(float(r["avg_return_90d"]), 2)
                if r["avg_return_90d"] is not None
                else None
            ),
            "avg_excess_7d": (
                round(float(r["avg_excess_7d"]), 2)
                if r["avg_excess_7d"] is not None
                else None
            ),
            "avg_excess_30d": (
                round(float(r["avg_excess_30d"]), 2)
                if r["avg_excess_30d"] is not None
                else None
            ),
            "avg_excess_60d": (
                round(float(r["avg_excess_60d"]), 2)
                if r["avg_excess_60d"] is not None
                else None
            ),
            "avg_excess_90d": (
                round(float(r["avg_excess_90d"]), 2)
                if r["avg_excess_90d"] is not None
                else None
            ),
        })
        s_total += int(r["total_recs"] or 0)
        s_acted += int(r["acted_on_count"] or 0)
        s_pending += int(r["pending_count"] or 0)

    summary = {
        "total_recs": s_total,
        "acted_on_count": s_acted,
        "pending_count": s_pending,
        # Roll-up hit-rate / return / excess across all
        # buckets must be re-aggregated from raw outcomes.
        "hit_rate_7d": None,
        "hit_rate_30d": None,
        "hit_rate_60d": None,
        "hit_rate_90d": None,
        "avg_return_7d": None,
        "avg_excess_7d": None,
        "avg_return_30d": None,
        "avg_excess_30d": None,
        "avg_return_60d": None,
        "avg_excess_60d": None,
        "avg_return_90d": None,
        "avg_excess_90d": None,
    }

    if s_total:
        # Single follow-up query — same filters, no
        # bucket grouping. Avoids Simpson's-paradox in
        # cross-bucket averages.
        result2 = await session.execute(
            text(
                """
                WITH bucketed AS (
                    SELECT r.id AS rec_id
                    FROM stocks.recommendations r
                    JOIN stocks.recommendation_runs run
                      ON r.run_id = run.run_id
                    WHERE run.user_id = :user_id
                      AND run.run_type != 'admin_test'
                      AND r.created_at >= (
                          NOW()
                          - (
                              INTERVAL '1 month'
                              * :months_back
                          )
                      )
                      AND (
                          CAST(:scope AS VARCHAR)
                              IS NULL
                          OR run.scope =
                             CAST(:scope AS VARCHAR)
                      )
                      AND (
                          NOT CAST(
                              :acted_on_only AS BOOLEAN
                          )
                          OR r.acted_on_date IS NOT NULL
                      )
                )
                SELECT
                    o.days_elapsed,
                    AVG(o.return_pct) AS avg_return,
                    AVG(o.excess_return_pct)
                        AS avg_excess,
                    (
                        SUM(
                            CASE WHEN
                                o.excess_return_pct > 0
                            THEN 1 ELSE 0 END
                        )::float
                        / NULLIF(COUNT(*), 0)
                        * 100
                    ) AS hit_rate
                FROM bucketed b
                JOIN stocks.recommendation_outcomes o
                  ON o.recommendation_id = b.rec_id
                WHERE o.days_elapsed IN (7, 30, 60, 90)
                GROUP BY o.days_elapsed
                """,
            ),
            {
                "user_id": user_id,
                "months_back": months_back,
                "scope": scope,
                "acted_on_only": acted_on_only,
            },
        )
        for r in result2.mappings().all():
            d = int(r["days_elapsed"])
            hr = r["hit_rate"]
            if hr is not None:
                summary[f"hit_rate_{d}d"] = round(
                    float(hr), 1,
                )
            if r["avg_return"] is not None:
                summary[f"avg_return_{d}d"] = round(
                    float(r["avg_return"]), 2,
                )
            if r["avg_excess"] is not None:
                summary[f"avg_excess_{d}d"] = round(
                    float(r["avg_excess"]), 2,
                )

    return {"buckets": buckets, "summary": summary}


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
    """Expire active recs from prior runs for user.

    Scoped by ``(user_id, scope)`` so that an India run
    never expires US recs (and vice versa).  Scope is
    read off the current run so callers don't need to
    pass it explicitly.
    """
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationRun,
    )

    # Look up the current run's scope.  If the row is
    # missing (shouldn't happen in the persist flow),
    # fall back to no-scope (whole-user) to preserve
    # prior behavior.
    cur_scope_q = await session.execute(
        select(RecommendationRun.scope).where(
            RecommendationRun.run_id == current_run_id,
        )
    )
    cur_scope = cur_scope_q.scalar_one_or_none()

    run_filter = [
        RecommendationRun.user_id == user_id,
        RecommendationRun.run_id != current_run_id,
    ]
    if cur_scope:
        run_filter.append(
            RecommendationRun.scope == cur_scope,
        )
    run_ids_q = (
        select(RecommendationRun.run_id)
        .where(*run_filter)
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
            "Expired %d old recs for user %s "
            "scope=%s",
            count, user_id, cur_scope or "*",
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


async def get_all_recommendations(
    session: AsyncSession,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """Return recommendations across all users with
    joined user email/name and run metadata.

    Used by the admin Recommendations tab. Ordered by
    ``created_at DESC``. Joins:

    ``recommendations`` → ``recommendation_runs``
    → ``auth.users``.

    Note: ``users.user_id`` is VARCHAR while
    ``recommendation_runs.user_id`` is UUID — cast
    both to text for the join.
    """
    from sqlalchemy import String, cast

    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationRun,
    )
    from backend.db.models.user import User

    result = await session.execute(
        select(
            Recommendation,
            RecommendationRun.scope,
            RecommendationRun.run_type,
            RecommendationRun.run_date,
            RecommendationRun.user_id,
            User.email,
            User.full_name,
        )
        .join(
            RecommendationRun,
            Recommendation.run_id
            == RecommendationRun.run_id,
        )
        .outerjoin(
            User,
            User.user_id
            == cast(
                RecommendationRun.user_id, String,
            ),
        )
        .order_by(Recommendation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows: list[dict] = []
    for rec, scope, run_type, run_date, uid, email, name in result.all():
        d = _rec_to_dict(rec)
        d["scope"] = scope
        d["run_type"] = run_type
        d["run_date"] = (
            run_date.isoformat() if run_date else None
        )
        d["user_id"] = uid
        d["email"] = email
        d["full_name"] = name
        rows.append(d)
    return rows


async def delete_recommendation_run(
    session: AsyncSession,
    run_id: str,
) -> int:
    """Hard-delete a whole run.

    The FK chain
    ``recommendation_runs → recommendations →
    recommendation_outcomes`` uses
    ``ondelete=CASCADE`` at every hop, so a single
    DELETE removes the entire tree.

    Returns 1 if the run existed, 0 otherwise.
    """
    from backend.db.models.recommendation import (
        RecommendationRun,
    )

    result = await session.execute(
        select(RecommendationRun).where(
            RecommendationRun.run_id == run_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return 0
    await session.delete(row)
    await session.commit()
    log.info(
        "Deleted recommendation run %s (cascade)",
        run_id,
    )
    return 1


async def delete_recommendation(
    session: AsyncSession,
    rec_id: str,
) -> int:
    """Hard-delete one recommendation by id.

    The ``recommendations → recommendation_outcomes``
    FK uses ``ondelete=CASCADE`` so outcomes are
    removed automatically at the DB level.

    Returns the number of rows deleted (0 or 1).
    """
    from backend.db.models.recommendation import (
        Recommendation,
    )

    result = await session.execute(
        select(Recommendation).where(
            Recommendation.id == rec_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return 0
    await session.delete(row)
    await session.commit()
    log.info("Deleted recommendation %s", rec_id)
    return 1
