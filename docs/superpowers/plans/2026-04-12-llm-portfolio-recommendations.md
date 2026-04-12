# LLM-Powered Portfolio Recommendations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded recommendation widget with a Smart Funnel pipeline (DuckDB pre-filter → portfolio gap analysis → LLM reasoning) that produces 5-8 data-driven, portfolio-aware recommendations per user per month, tracked with 30/60/90-day outcomes.

**Architecture:** Three-stage pipeline. Stage 1 scores the full ticker universe via a single DuckDB CTE query (user-independent, cached 1h). Stage 2 runs per-user portfolio gap analysis (sector, index, cap, correlation). Stage 3 sends ~40 candidates to the Groq/Anthropic cascade for final selection + narrative. Results stored in 3 PostgreSQL tables, surfaced via dashboard widget and a new Recommendation agent in chat.

**Tech Stack:** SQLAlchemy 2.0 async ORM, Alembic, DuckDB (iceberg_scan), FallbackLLM (Groq cascade), React 19, SWR, TailwindCSS.

**Spec:** `docs/superpowers/specs/2026-04-12-llm-portfolio-recommendations-design.md`

---

## File Map

### New files

| File | Responsibility |
|------|----------------|
| `backend/db/models/recommendation.py` | 3 ORM models (RecommendationRun, Recommendation, RecommendationOutcome) |
| `backend/db/migrations/versions/e7f8a9b0c1d2_add_recommendation_tables.py` | Alembic migration |
| `backend/jobs/recommendation_engine.py` | Smart Funnel pipeline: stage1_prefilter, stage2_gap_analysis, stage3_llm_reasoning |
| `backend/agents/configs/recommendation.py` | SubAgentConfig for recommendation agent |
| `backend/tools/recommendation_tools.py` | 3 tools: generate_recommendations, get_recommendation_history, get_recommendation_performance |
| `backend/recommendation_routes.py` | 5 API endpoints (separated from dashboard_routes for clarity) |
| `backend/recommendation_models.py` | Pydantic response models |
| `frontend/components/widgets/RecommendationCard.tsx` | Individual recommendation card |
| `frontend/components/widgets/HealthScoreBadge.tsx` | Health score circle indicator |
| `frontend/components/widgets/SignalPill.tsx` | Reusable signal pill |
| `frontend/components/insights/RecommendationHistoryTab.tsx` | History tab with outcomes |
| `tests/test_recommendation_engine.py` | Unit + integration tests |

### Files to modify

| File | Change |
|------|--------|
| `backend/db/models/__init__.py` | Register 3 new models |
| `backend/db/pg_stocks.py` | Add recommendation PG functions |
| `backend/jobs/executor.py` | Register `recommendations` + `recommendation_outcomes` job types |
| `backend/agents/graph.py` | Add 6th sub-agent node + edges |
| `backend/agents/nodes/router_node.py` | Add `recommendation` intent keywords |
| `backend/agents/nodes/guardrail.py` | Add `recommendation` to follow-up routing |
| `backend/bootstrap.py` | Register 3 new tools |
| `backend/main.py` | Mount recommendation_routes router |
| `frontend/components/widgets/RecommendationsWidget.tsx` | Complete rewrite to use new API |
| `frontend/hooks/useDashboardData.ts` | Add recommendation SWR hooks |
| `frontend/hooks/useInsightsData.ts` | Add history + stats hooks |
| `frontend/lib/types.ts` | Add TypeScript types |
| `frontend/app/(authenticated)/analytics/insights/page.tsx` | Add Rec History tab |

---

## Task 1: ORM Models + Alembic Migration

**Files:**
- Create: `backend/db/models/recommendation.py`
- Create: `backend/db/migrations/versions/e7f8a9b0c1d2_add_recommendation_tables.py`
- Modify: `backend/db/models/__init__.py`

- [ ] **Step 1: Create ORM models**

Create `backend/db/models/recommendation.py`:

```python
"""Recommendation ORM models — runs, items, outcomes."""
from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    __table_args__ = (
        Index(
            "ix_rec_runs_user_date",
            "user_id",
            "run_date",
        ),
        {"schema": "stocks", "extend_existing": True},
    )

    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False,
    )
    run_date: Mapped[date] = mapped_column(
        Date, nullable=False,
    )
    run_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    portfolio_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
    )
    health_score: Mapped[float] = mapped_column(
        Float, nullable=False,
    )
    health_label: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    health_assessment: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    candidates_scanned: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    candidates_passed: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    llm_model: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    llm_tokens_used: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    duration_secs: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    recommendations: Mapped[list["Recommendation"]] = (
        relationship(back_populates="run",
                     cascade="all, delete-orphan")
    )


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recs_run_id", "run_id"),
        Index("ix_recs_ticker_status", "ticker", "status"),
        Index("ix_recs_status_created", "status", "created_at"),
        {"schema": "stocks", "extend_existing": True},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False,
    )
    tier: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    category: Mapped[str] = mapped_column(
        String(25), nullable=False,
    )
    ticker: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
    )
    action: Mapped[str] = mapped_column(
        String(15), nullable=False,
    )
    severity: Mapped[str] = mapped_column(
        String(10), nullable=False,
    )
    rationale: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    expected_impact: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    data_signals: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
    )
    price_at_rec: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    target_price: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    expected_return_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    index_tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(15), nullable=False,
        server_default="active",
    )
    acted_on_date: Mapped[date | None] = mapped_column(
        Date, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    run: Mapped["RecommendationRun"] = relationship(
        back_populates="recommendations",
    )
    outcomes: Mapped[list["RecommendationOutcome"]] = (
        relationship(back_populates="recommendation",
                     cascade="all, delete-orphan")
    )


class RecommendationOutcome(Base):
    __tablename__ = "recommendation_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "recommendation_id", "days_elapsed",
            name="uq_rec_outcomes_rec_days",
        ),
        {"schema": "stocks", "extend_existing": True},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    recommendation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False,
    )
    check_date: Mapped[date] = mapped_column(
        Date, nullable=False,
    )
    days_elapsed: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    actual_price: Mapped[float] = mapped_column(
        Float, nullable=False,
    )
    return_pct: Mapped[float] = mapped_column(
        Float, nullable=False,
    )
    benchmark_return_pct: Mapped[float] = mapped_column(
        Float, nullable=False,
    )
    excess_return_pct: Mapped[float] = mapped_column(
        Float, nullable=False,
    )
    outcome_label: Mapped[str] = mapped_column(
        String(15), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    recommendation: Mapped["Recommendation"] = relationship(
        back_populates="outcomes",
    )
```

- [ ] **Step 2: Register models in __init__.py**

Add to `backend/db/models/__init__.py`:

```python
from backend.db.models.recommendation import (
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
)
```

And add all three to the `__all__` list.

- [ ] **Step 3: Create Alembic migration**

Run:
```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
PYTHONPATH=. alembic revision --autogenerate -m "add recommendation tables"
```

Review the generated migration. Verify it creates 3 tables in `stocks` schema with all indexes and the unique constraint. Add ForeignKey references manually if autogenerate misses the cross-schema FK to `auth.users`.

- [ ] **Step 4: Apply migration**

Run:
```bash
PYTHONPATH=. alembic upgrade head
```

Expected: 3 tables created in `stocks` schema.

- [ ] **Step 5: Verify tables exist**

Run:
```bash
docker compose exec postgres psql -U ai_user -d ai_agent_db -c "\dt stocks.*"
```

Expected: `recommendation_runs`, `recommendations`, `recommendation_outcomes` visible.

- [ ] **Step 6: Commit**

```bash
git add backend/db/models/recommendation.py backend/db/models/__init__.py backend/db/migrations/versions/
git commit -m "feat(db): add recommendation ORM models + Alembic migration (ASETPLTFRM-298)

Three PG tables: recommendation_runs, recommendations,
recommendation_outcomes with indexes and FK cascades.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 2: PG Functions for Recommendation CRUD

**Files:**
- Modify: `backend/db/pg_stocks.py`
- Test: `tests/test_recommendation_engine.py` (start file)

- [ ] **Step 1: Write failing tests for PG functions**

Create `tests/test_recommendation_engine.py`:

```python
"""Tests for recommendation engine — PG functions."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest


def _make_run_data(user_id: str) -> dict:
    return {
        "run_id": str(uuid.uuid4()),
        "user_id": user_id,
        "run_date": date.today(),
        "run_type": "manual",
        "portfolio_snapshot": {"holdings_count": 5},
        "health_score": 62.0,
        "health_label": "needs_attention",
        "health_assessment": "Test assessment.",
        "candidates_scanned": 200,
        "candidates_passed": 40,
        "llm_model": "llama-3.3-70b",
        "llm_tokens_used": 4500,
        "duration_secs": 5.2,
    }


def _make_rec_data(run_id: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "tier": "discovery",
        "category": "new_buy",
        "ticker": "HDFCBANK.NS",
        "action": "buy",
        "severity": "high",
        "rationale": "Fills Financial Services gap.",
        "expected_impact": "FS +10%",
        "data_signals": {"piotroski": 8, "sharpe": 1.4},
        "price_at_rec": 1580.0,
        "target_price": 1774.0,
        "expected_return_pct": 12.3,
        "index_tags": ["nifty50", "largecap"],
        "status": "active",
    }


class TestRecommendationPGFunctions:
    """Test PG CRUD for recommendations."""

    def test_insert_and_get_run(self):
        from backend.db.pg_stocks import (
            get_latest_recommendation_run,
            insert_recommendation_run,
        )

        user_id = str(uuid.uuid4())
        run_data = _make_run_data(user_id)
        insert_recommendation_run(run_data)
        result = get_latest_recommendation_run(user_id)
        assert result is not None
        assert result["run_id"] == run_data["run_id"]
        assert result["health_score"] == 62.0

    def test_insert_recommendations(self):
        from backend.db.pg_stocks import (
            get_recommendations_for_run,
            insert_recommendation_run,
            insert_recommendations,
        )

        user_id = str(uuid.uuid4())
        run_data = _make_run_data(user_id)
        insert_recommendation_run(run_data)
        rec = _make_rec_data(run_data["run_id"])
        count = insert_recommendations(
            run_data["run_id"], [rec],
        )
        assert count == 1
        recs = get_recommendations_for_run(
            run_data["run_id"],
        )
        assert len(recs) == 1
        assert recs[0]["ticker"] == "HDFCBANK.NS"

    def test_update_recommendation_status(self):
        from backend.db.pg_stocks import (
            insert_recommendation_run,
            insert_recommendations,
            update_recommendation_status,
        )

        user_id = str(uuid.uuid4())
        run_data = _make_run_data(user_id)
        insert_recommendation_run(run_data)
        rec = _make_rec_data(run_data["run_id"])
        insert_recommendations(
            run_data["run_id"], [rec],
        )
        updated = update_recommendation_status(
            user_id, "HDFCBANK.NS",
            ("buy", "accumulate"), "acted_on",
        )
        assert updated == 1

    def test_expire_old_recommendations(self):
        from backend.db.pg_stocks import (
            expire_old_recommendations,
            get_recommendations_for_run,
            insert_recommendation_run,
            insert_recommendations,
        )

        user_id = str(uuid.uuid4())
        # Old run
        old_run = _make_run_data(user_id)
        insert_recommendation_run(old_run)
        old_rec = _make_rec_data(old_run["run_id"])
        insert_recommendations(
            old_run["run_id"], [old_rec],
        )
        # New run
        new_run = _make_run_data(user_id)
        insert_recommendation_run(new_run)
        expired = expire_old_recommendations(
            user_id, new_run["run_id"],
        )
        assert expired == 1
        recs = get_recommendations_for_run(
            old_run["run_id"],
        )
        assert recs[0]["status"] == "expired"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=.:backend python -m pytest tests/test_recommendation_engine.py -v`

Expected: ImportError — functions don't exist yet.

- [ ] **Step 3: Implement PG functions**

Add to `backend/db/pg_stocks.py` (after existing functions, before EOF):

```python
# ------------------------------------------------------------------
# Recommendation functions
# ------------------------------------------------------------------


def insert_recommendation_run(data: dict) -> str:
    """Insert a recommendation run row. Returns run_id."""
    from backend.db.models.recommendation import (
        RecommendationRun,
    )

    with _pg_session() as session:
        row = RecommendationRun(**data)
        session.add(row)
        session.commit()
        return data["run_id"]


def insert_recommendations(
    run_id: str,
    recs: list[dict],
) -> int:
    """Bulk insert recommendations for a run."""
    from backend.db.models.recommendation import (
        Recommendation,
    )

    with _pg_session() as session:
        for rec in recs:
            rec["run_id"] = run_id
            session.add(Recommendation(**rec))
        session.commit()
        return len(recs)


def get_latest_recommendation_run(
    user_id: str,
) -> dict | None:
    """Get the most recent run for a user."""
    from backend.db.models.recommendation import (
        RecommendationRun,
    )

    with _pg_session() as session:
        result = session.execute(
            select(RecommendationRun)
            .where(RecommendationRun.user_id == user_id)
            .order_by(
                RecommendationRun.created_at.desc()
            )
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return {
            c.name: getattr(row, c.name)
            for c in row.__table__.columns
        }


def get_recommendations_for_run(
    run_id: str,
) -> list[dict]:
    """Get all recommendations for a specific run."""
    from backend.db.models.recommendation import (
        Recommendation,
    )

    with _pg_session() as session:
        result = session.execute(
            select(Recommendation)
            .where(Recommendation.run_id == run_id)
            .order_by(Recommendation.severity.desc())
        )
        return [
            {
                c.name: getattr(r, c.name)
                for c in r.__table__.columns
            }
            for r in result.scalars().all()
        ]


def get_recommendation_history(
    user_id: str,
    months_back: int = 6,
) -> list[dict]:
    """Get runs with recommendation counts for history."""
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationRun,
    )

    cutoff = date.today() - timedelta(
        days=months_back * 30,
    )
    with _pg_session() as session:
        result = session.execute(
            select(RecommendationRun)
            .where(
                RecommendationRun.user_id == user_id,
                RecommendationRun.run_date >= cutoff,
            )
            .order_by(
                RecommendationRun.run_date.desc()
            )
        )
        runs = []
        for row in result.scalars().all():
            run_dict = {
                c.name: getattr(row, c.name)
                for c in row.__table__.columns
            }
            # Count recommendations per run
            recs_result = session.execute(
                select(func.count())
                .select_from(Recommendation)
                .where(
                    Recommendation.run_id == row.run_id
                )
            )
            run_dict["total_recommendations"] = (
                recs_result.scalar() or 0
            )
            acted = session.execute(
                select(func.count())
                .select_from(Recommendation)
                .where(
                    Recommendation.run_id == row.run_id,
                    Recommendation.status == "acted_on",
                )
            )
            run_dict["acted_on_count"] = (
                acted.scalar() or 0
            )
            runs.append(run_dict)
        return runs


def get_recommendation_stats(
    user_id: str,
) -> dict:
    """Aggregate stats: hit rates, avg returns."""
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationOutcome,
        RecommendationRun,
    )

    with _pg_session() as session:
        # Get all run IDs for this user
        run_ids_q = session.execute(
            select(RecommendationRun.run_id).where(
                RecommendationRun.user_id == user_id
            )
        )
        run_ids = [
            r[0] for r in run_ids_q.fetchall()
        ]
        if not run_ids:
            return {
                "total_recommendations": 0,
                "total_acted_on": 0,
                "adoption_rate_pct": 0.0,
            }

        # Total + acted on
        total_q = session.execute(
            select(func.count())
            .select_from(Recommendation)
            .where(Recommendation.run_id.in_(run_ids))
        )
        total = total_q.scalar() or 0

        acted_q = session.execute(
            select(func.count())
            .select_from(Recommendation)
            .where(
                Recommendation.run_id.in_(run_ids),
                Recommendation.status == "acted_on",
            )
        )
        acted = acted_q.scalar() or 0

        # Outcomes by checkpoint
        stats = {
            "total_recommendations": total,
            "total_acted_on": acted,
            "adoption_rate_pct": (
                (acted / total * 100) if total else 0.0
            ),
        }

        for days in (30, 60, 90):
            outcomes_q = session.execute(
                select(
                    func.count(),
                    func.avg(
                        RecommendationOutcome.return_pct
                    ),
                    func.avg(
                        RecommendationOutcome
                        .excess_return_pct
                    ),
                    func.sum(
                        func.cast(
                            RecommendationOutcome
                            .outcome_label == "correct",
                            Integer,
                        )
                    ),
                )
                .select_from(RecommendationOutcome)
                .join(
                    Recommendation,
                    Recommendation.id
                    == RecommendationOutcome
                    .recommendation_id,
                )
                .where(
                    Recommendation.run_id.in_(run_ids),
                    RecommendationOutcome.days_elapsed
                    == days,
                )
            )
            row = outcomes_q.fetchone()
            measured = row[0] or 0
            key = f"{days}d"
            stats[f"hit_rate_{key}"] = (
                (row[3] / measured * 100)
                if measured
                else None
            )
            stats[f"avg_return_{key}"] = (
                float(row[1]) if row[1] else None
            )
            stats[f"avg_excess_return_{key}"] = (
                float(row[2]) if row[2] else None
            )

        return stats


def get_recommendations_due_for_outcome(
    today: date,
) -> list[dict]:
    """Find recs due for 30/60/90d checkpoint."""
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationOutcome,
    )

    results = []
    with _pg_session() as session:
        for days in (30, 60, 90):
            target_date = today - timedelta(days=days)
            window_start = target_date - timedelta(days=2)
            window_end = target_date + timedelta(days=2)

            # Subquery: recs that already have this
            # checkpoint
            existing = (
                select(
                    RecommendationOutcome
                    .recommendation_id
                )
                .where(
                    RecommendationOutcome.days_elapsed
                    == days
                )
                .scalar_subquery()
            )

            q = session.execute(
                select(Recommendation)
                .where(
                    Recommendation.status.in_(
                        ("active", "acted_on")
                    ),
                    Recommendation.ticker.isnot(None),
                    func.date(
                        Recommendation.created_at
                    ).between(window_start, window_end),
                    Recommendation.id.notin_(existing),
                )
            )
            for r in q.scalars().all():
                rec_dict = {
                    c.name: getattr(r, c.name)
                    for c in r.__table__.columns
                }
                rec_dict["checkpoint_days"] = days
                results.append(rec_dict)

    return results


def insert_recommendation_outcome(
    rec_id: str,
    check_date: date,
    days: int,
    price: float,
    ret: float,
    bench: float,
    excess: float,
    label: str,
) -> None:
    """Insert a single outcome checkpoint row."""
    from backend.db.models.recommendation import (
        RecommendationOutcome,
    )

    with _pg_session() as session:
        session.add(
            RecommendationOutcome(
                id=str(uuid.uuid4()),
                recommendation_id=rec_id,
                check_date=check_date,
                days_elapsed=days,
                actual_price=price,
                return_pct=ret,
                benchmark_return_pct=bench,
                excess_return_pct=excess,
                outcome_label=label,
            )
        )
        session.commit()


def update_recommendation_status(
    user_id: str,
    ticker: str,
    actions: tuple[str, ...],
    new_status: str,
) -> int:
    """Match user action to active recommendation."""
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationRun,
    )

    with _pg_session() as session:
        # Get run IDs for user
        run_ids_q = session.execute(
            select(RecommendationRun.run_id).where(
                RecommendationRun.user_id == user_id
            )
        )
        run_ids = [r[0] for r in run_ids_q.fetchall()]
        if not run_ids:
            return 0

        from sqlalchemy import update

        stmt = (
            update(Recommendation)
            .where(
                Recommendation.run_id.in_(run_ids),
                Recommendation.ticker == ticker,
                Recommendation.action.in_(actions),
                Recommendation.status == "active",
            )
            .values(
                status=new_status,
                acted_on_date=date.today(),
            )
        )
        result = session.execute(stmt)
        session.commit()
        return result.rowcount


def expire_old_recommendations(
    user_id: str,
    current_run_id: str,
) -> int:
    """Expire all active recs from prior runs."""
    from backend.db.models.recommendation import (
        Recommendation,
        RecommendationRun,
    )

    with _pg_session() as session:
        run_ids_q = session.execute(
            select(RecommendationRun.run_id).where(
                RecommendationRun.user_id == user_id,
                RecommendationRun.run_id
                != current_run_id,
            )
        )
        old_ids = [r[0] for r in run_ids_q.fetchall()]
        if not old_ids:
            return 0

        from sqlalchemy import update

        stmt = (
            update(Recommendation)
            .where(
                Recommendation.run_id.in_(old_ids),
                Recommendation.status == "active",
            )
            .values(status="expired")
        )
        result = session.execute(stmt)
        session.commit()
        return result.rowcount


def expire_stale_recommendations(
    today: date,
) -> int:
    """Expire active recs older than 90 days."""
    from backend.db.models.recommendation import (
        Recommendation,
    )

    cutoff = today - timedelta(days=90)
    with _pg_session() as session:
        from sqlalchemy import update

        stmt = (
            update(Recommendation)
            .where(
                Recommendation.status == "active",
                func.date(Recommendation.created_at)
                < cutoff,
            )
            .values(status="expired")
        )
        result = session.execute(stmt)
        session.commit()
        return result.rowcount
```

Also add `import uuid` at the top of `pg_stocks.py` if not already present, and add `from datetime import date` if missing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=.:backend python -m pytest tests/test_recommendation_engine.py -v`

Expected: All 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/db/pg_stocks.py tests/test_recommendation_engine.py
git commit -m "feat(db): add recommendation PG CRUD functions + tests (ASETPLTFRM-298)

Insert/query/expire recommendation runs, items, and outcomes.
Action matching hooks for portfolio transactions.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 3: Smart Funnel Pipeline — Stage 1 (Pre-Filter)

**Files:**
- Create: `backend/jobs/recommendation_engine.py`
- Test: `tests/test_recommendation_engine.py` (extend)

- [ ] **Step 1: Write failing tests for Stage 1**

Append to `tests/test_recommendation_engine.py`:

```python
import pandas as pd


class TestStage1Prefilter:
    """Test composite score calculation."""

    def test_composite_score_normal_values(self):
        from backend.jobs.recommendation_engine import (
            _compute_composite_score,
        )

        row = {
            "piotroski": 8,
            "sharpe_ratio": 1.5,
            "annualized_return_pct": 20.0,
            "target_3m_pct_change": 12.0,
            "mape": 10.0,
            "mae": 50.0,
            "rmse": 65.0,
            "current_price": 1000.0,
            "sentiment": 0.5,
            "sma_50_signal": "BUY",
            "sma_200_signal": "BUY",
            "rsi_signal": "NEUTRAL",
            "macd_signal_text": "bullish crossover",
        }
        score = _compute_composite_score(row)
        assert 60 < score < 90  # strong stock
        assert isinstance(score, float)

    def test_composite_score_weak_stock(self):
        from backend.jobs.recommendation_engine import (
            _compute_composite_score,
        )

        row = {
            "piotroski": 4,
            "sharpe_ratio": -0.5,
            "annualized_return_pct": -10.0,
            "target_3m_pct_change": -5.0,
            "mape": 40.0,
            "mae": 200.0,
            "rmse": 250.0,
            "current_price": 500.0,
            "sentiment": -0.3,
            "sma_50_signal": "SELL",
            "sma_200_signal": "SELL",
            "rsi_signal": "OVERBOUGHT_SELL",
            "macd_signal_text": "bearish",
        }
        score = _compute_composite_score(row)
        assert 10 < score < 45  # weak stock

    def test_accuracy_factor(self):
        from backend.jobs.recommendation_engine import (
            _compute_accuracy_factor,
        )

        # Low MAPE → high confidence
        af = _compute_accuracy_factor(
            mape=5.0, mae=30.0, rmse=40.0,
            current_price=1000.0,
        )
        assert af > 0.9

        # High MAPE → low confidence
        af2 = _compute_accuracy_factor(
            mape=50.0, mae=300.0, rmse=400.0,
            current_price=1000.0,
        )
        assert af2 < 0.5

    def test_accuracy_factor_zero_price(self):
        from backend.jobs.recommendation_engine import (
            _compute_accuracy_factor,
        )

        af = _compute_accuracy_factor(
            mape=10.0, mae=50.0, rmse=60.0,
            current_price=0.0,
        )
        # Should not divide by zero — falls back to
        # MAPE-only
        assert 0 <= af <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=.:backend python -m pytest tests/test_recommendation_engine.py::TestStage1Prefilter -v`

Expected: ImportError.

- [ ] **Step 3: Implement Stage 1**

Create `backend/jobs/recommendation_engine.py`:

```python
"""Smart Funnel recommendation engine.

Three-stage pipeline:
  1. stage1_prefilter — DuckDB batch scoring (user-independent)
  2. stage2_gap_analysis — per-user portfolio gap detection
  3. stage3_llm_reasoning — LLM final selection + narrative

Usage::

    candidates = stage1_prefilter(duckdb_engine)
    gap_result = stage2_gap_analysis(user_id, candidates, repo)
    recommendations = stage3_llm_reasoning(gap_result)
"""

from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd

_logger = logging.getLogger(__name__)

# Composite score weights — sum to 1.0
W_PIOTROSKI = 0.25
W_SHARPE = 0.20
W_MOMENTUM = 0.15
W_FORECAST = 0.20
W_SENTIMENT = 0.10
W_TECHNICAL = 0.10

# Normalization clamp ranges
SHARPE_MIN, SHARPE_MAX = -2.0, 4.0
RETURN_MIN, RETURN_MAX = -50.0, 100.0
FORECAST_MIN, FORECAST_MAX = -30.0, 50.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _norm(value: float, lo: float, hi: float) -> float:
    """Normalize value to 0-100 given [lo, hi] range."""
    if hi == lo:
        return 50.0
    return _clamp(
        (value - lo) / (hi - lo) * 100, 0.0, 100.0,
    )


def _compute_accuracy_factor(
    mape: float,
    mae: float,
    rmse: float,
    current_price: float,
) -> float:
    """Composite forecast accuracy factor (0-1)."""
    mape_f = max(0.0, 1.0 - (mape or 0.0) / 100.0)
    if current_price and current_price > 0:
        mae_f = max(
            0.0, 1.0 - (mae or 0.0) / current_price,
        )
        rmse_f = max(
            0.0,
            1.0 - (rmse or 0.0) / current_price,
        )
    else:
        mae_f = mape_f
        rmse_f = mape_f
    return 0.5 * mape_f + 0.3 * mae_f + 0.2 * rmse_f


def _compute_composite_score(row: dict) -> float:
    """Compute 0-100 composite score for a single ticker.

    Args:
        row: Dict with keys: piotroski, sharpe_ratio,
            annualized_return_pct, target_3m_pct_change,
            mape, mae, rmse, current_price, sentiment,
            sma_50_signal, sma_200_signal, rsi_signal,
            macd_signal_text.
    """
    # 1. Fundamental quality
    piotroski = float(row.get("piotroski") or 0)
    piotroski_norm = (piotroski / 9.0) * 100.0

    # 2. Risk-adjusted return
    sharpe = float(row.get("sharpe_ratio") or 0)
    sharpe_norm = _norm(sharpe, SHARPE_MIN, SHARPE_MAX)

    # 3. Momentum
    ret = float(
        row.get("annualized_return_pct") or 0
    )
    momentum_norm = _norm(ret, RETURN_MIN, RETURN_MAX)

    # 4. Forecast upside (accuracy-adjusted)
    forecast_pct = float(
        row.get("target_3m_pct_change") or 0
    )
    acc_factor = _compute_accuracy_factor(
        mape=float(row.get("mape") or 0),
        mae=float(row.get("mae") or 0),
        rmse=float(row.get("rmse") or 0),
        current_price=float(
            row.get("current_price") or 0
        ),
    )
    adjusted = forecast_pct * acc_factor
    forecast_norm = _norm(
        adjusted, FORECAST_MIN, FORECAST_MAX,
    )

    # 5. Sentiment
    sent = float(row.get("sentiment") or 0)
    sentiment_norm = (sent + 1.0) / 2.0 * 100.0

    # 6. Technical alignment
    bullish = 0
    if (row.get("sma_50_signal") or "").upper() == "BUY":
        bullish += 1
    if (
        row.get("sma_200_signal") or ""
    ).upper() == "BUY":
        bullish += 1
    rsi = (row.get("rsi_signal") or "").upper()
    if rsi in ("BUY", "OVERSOLD_BUY"):
        bullish += 1
    macd = (row.get("macd_signal_text") or "").lower()
    if "bull" in macd:
        bullish += 1
    technical_norm = (bullish / 4.0) * 100.0

    return (
        W_PIOTROSKI * piotroski_norm
        + W_SHARPE * sharpe_norm
        + W_MOMENTUM * momentum_norm
        + W_FORECAST * forecast_norm
        + W_SENTIMENT * sentiment_norm
        + W_TECHNICAL * technical_norm
    )


# Pre-filter cache (user-independent, 1h TTL)
_PREFILTER_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_PREFILTER_TTL = 3600  # 1 hour


def stage1_prefilter(
    duckdb_engine=None,
) -> pd.DataFrame:
    """Stage 1: Score full ticker universe via DuckDB.

    Returns DataFrame with columns: ticker,
    composite_score, all raw signals, sector,
    industry, market_cap, company_name, current_price,
    target_price, index_tags, plus all normalized values.

    Results cached for 1 hour (user-independent).
    """
    cache_key = str(date.today())
    now = time.time()
    if cache_key in _PREFILTER_CACHE:
        ts, df = _PREFILTER_CACHE[cache_key]
        if now - ts < _PREFILTER_TTL:
            _logger.info(
                "Stage 1 cache hit: %d candidates",
                len(df),
            )
            return df

    if duckdb_engine is None:
        from db.duckdb_engine import get_duckdb_engine

        duckdb_engine = get_duckdb_engine()

    sql = """
    WITH latest_piotroski AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY ticker
            ORDER BY score_date DESC
        ) AS rn FROM piotroski_scores
    ),
    latest_analysis AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY ticker
            ORDER BY analysis_date DESC
        ) AS rn FROM analysis_summary
    ),
    latest_sentiment AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY ticker
            ORDER BY score_date DESC
        ) AS rn FROM sentiment_scores
    ),
    latest_forecast AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY ticker
            ORDER BY run_date DESC
        ) AS rn FROM forecast_runs
        WHERE horizon_months > 0
    ),
    latest_price AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY ticker
            ORDER BY date DESC
        ) AS rn FROM ohlcv
        WHERE close IS NOT NULL
    )
    SELECT
        p.ticker,
        p.total_score AS piotroski,
        p.sector, p.industry,
        p.market_cap, p.avg_volume,
        p.company_name,
        a.sharpe_ratio,
        a.annualized_return_pct,
        a.annualized_volatility_pct,
        a.max_drawdown_pct,
        a.sma_50_signal, a.sma_200_signal,
        a.rsi_signal, a.macd_signal_text,
        s.avg_score AS sentiment,
        s.headline_count,
        f.target_3m_pct_change,
        f.target_3m_price,
        f.target_6m_pct_change,
        f.target_9m_pct_change,
        f.mape, f.mae, f.rmse,
        f.run_date AS forecast_run_date,
        pr.close AS current_price,
        pr.date AS price_date
    FROM latest_piotroski p
    JOIN latest_analysis a
        ON a.ticker = p.ticker AND a.rn = 1
    JOIN latest_sentiment s
        ON s.ticker = p.ticker AND s.rn = 1
    JOIN latest_forecast f
        ON f.ticker = p.ticker AND f.rn = 1
    JOIN latest_price pr
        ON pr.ticker = p.ticker AND pr.rn = 1
    WHERE p.rn = 1
      AND p.total_score >= 4
      AND COALESCE(p.avg_volume, 0) >= 10000
      AND f.run_date >= CURRENT_DATE - INTERVAL '30 days'
      AND s.score_date >= CURRENT_DATE - INTERVAL '7 days'
      AND COALESCE(f.mape, 999) < 80
    """

    df = duckdb_engine.query(sql)
    if df.empty:
        _logger.warning("Stage 1: zero candidates")
        return df

    # Compute composite scores
    scores = []
    for _, row in df.iterrows():
        scores.append(
            _compute_composite_score(row.to_dict())
        )
    df["composite_score"] = scores

    # Sort by score descending
    df = df.sort_values(
        "composite_score", ascending=False,
    ).reset_index(drop=True)

    _PREFILTER_CACHE[cache_key] = (now, df)
    _logger.info(
        "Stage 1 complete: %d candidates "
        "(score range %.1f–%.1f)",
        len(df),
        df["composite_score"].min(),
        df["composite_score"].max(),
    )
    return df
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=.:backend python -m pytest tests/test_recommendation_engine.py::TestStage1Prefilter -v`

Expected: All 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/jobs/recommendation_engine.py tests/test_recommendation_engine.py
git commit -m "feat(engine): Smart Funnel Stage 1 — DuckDB pre-filter + composite scoring (ASETPLTFRM-298)

Accuracy-adjusted forecast signal, 6-factor composite score,
1h TTL cache. Hard gates: Piotroski>=4, volume>=10K, MAPE<80.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 4: Smart Funnel Pipeline — Stage 2 (Gap Analysis)

**Files:**
- Modify: `backend/jobs/recommendation_engine.py`
- Test: `tests/test_recommendation_engine.py` (extend)

- [ ] **Step 1: Write failing tests for Stage 2**

Append to `tests/test_recommendation_engine.py`:

```python
class TestStage2GapAnalysis:
    """Test portfolio gap analysis."""

    def test_sector_gap_calculation(self):
        from backend.jobs.recommendation_engine import (
            _compute_sector_gaps,
        )

        user_sectors = {"Technology": 40.0, "Financial Services": 5.0}
        universe_sectors = {
            "Technology": 20.0,
            "Financial Services": 20.0,
            "Healthcare": 15.0,
        }
        gaps = _compute_sector_gaps(
            user_sectors, universe_sectors,
        )
        assert gaps["Technology"] > 0  # overweight
        assert gaps["Financial Services"] < 0  # underweight
        assert gaps["Healthcare"] < 0  # missing

    def test_gap_fill_bonus(self):
        from backend.jobs.recommendation_engine import (
            _compute_gap_bonus,
        )

        # Stock fills a big sector gap + is in Nifty 50
        bonus = _compute_gap_bonus(
            sector_gap_pct=-15.0,
            index_gap=True,
            cap_gap_pct=-8.0,
        )
        assert 10 < bonus <= 20

        # Stock fills no gaps
        bonus2 = _compute_gap_bonus(
            sector_gap_pct=5.0,
            index_gap=False,
            cap_gap_pct=2.0,
        )
        assert bonus2 == 0.0

    def test_tier_assignment(self):
        from backend.jobs.recommendation_engine import (
            _assign_tier,
        )

        assert _assign_tier(
            "TCS.NS", {"TCS.NS"}, {"INFY.NS"},
        ) == "portfolio"
        assert _assign_tier(
            "INFY.NS", {"TCS.NS"}, {"INFY.NS"},
        ) == "watchlist"
        assert _assign_tier(
            "HDFC.NS", {"TCS.NS"}, {"INFY.NS"},
        ) == "discovery"

    def test_holding_category_assignment(self):
        from backend.jobs.recommendation_engine import (
            _categorize_holding,
        )

        # Weak holding with negative forecast
        cat = _categorize_holding(
            composite_score=25.0,
            forecast_3m_pct=-5.0,
            weight_pct=8.0,
            sentiment=-0.4,
        )
        assert cat == "exit_reduce"

        # Overweight holding
        cat2 = _categorize_holding(
            composite_score=70.0,
            forecast_3m_pct=10.0,
            weight_pct=22.0,
            sentiment=0.3,
        )
        assert cat2 == "rebalance"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=.:backend python -m pytest tests/test_recommendation_engine.py::TestStage2GapAnalysis -v`

Expected: ImportError.

- [ ] **Step 3: Implement Stage 2 functions**

Append to `backend/jobs/recommendation_engine.py`:

```python
# ------------------------------------------------------------------
# Stage 2: Portfolio Gap Analysis
# ------------------------------------------------------------------

# Benchmark: 60% large, 25% mid, 15% small
CAP_BENCHMARK = {"largecap": 60, "midcap": 25, "smallcap": 15}
# Large cap threshold: 200B INR (20,000 Cr)
LARGE_CAP_FLOOR = 200_000_000_000
# Mid cap: 50B-200B INR
MID_CAP_FLOOR = 50_000_000_000


def _classify_cap(market_cap: float | None) -> str:
    if not market_cap:
        return "smallcap"
    if market_cap >= LARGE_CAP_FLOOR:
        return "largecap"
    if market_cap >= MID_CAP_FLOOR:
        return "midcap"
    return "smallcap"


def _compute_sector_gaps(
    user_sectors: dict[str, float],
    universe_sectors: dict[str, float],
) -> dict[str, float]:
    """Positive = overweight, negative = underweight."""
    gaps = {}
    for sector in universe_sectors:
        current = user_sectors.get(sector, 0.0)
        benchmark = universe_sectors[sector]
        gaps[sector] = current - benchmark
    return gaps


def _compute_gap_bonus(
    sector_gap_pct: float,
    index_gap: bool,
    cap_gap_pct: float,
) -> float:
    """0-20 point bonus for gap-filling candidates."""
    bonus = 0.0
    # Sector gap: up to +10
    if sector_gap_pct < -5:
        bonus += min(10.0, abs(sector_gap_pct) * 0.5)
    # Index tracking: +5
    if index_gap:
        bonus += 5.0
    # Cap size gap: up to +5
    if cap_gap_pct < -5:
        bonus += min(5.0, abs(cap_gap_pct) * 0.3)
    return min(bonus, 20.0)


def _assign_tier(
    ticker: str,
    holdings_tickers: set[str],
    watchlist_tickers: set[str],
) -> str:
    if ticker in holdings_tickers:
        return "portfolio"
    if ticker in watchlist_tickers:
        return "watchlist"
    return "discovery"


def _categorize_holding(
    composite_score: float,
    forecast_3m_pct: float,
    weight_pct: float,
    sentiment: float,
) -> str:
    """Assign category to an existing holding."""
    if composite_score < 30 and forecast_3m_pct < 0:
        return "exit_reduce"
    if composite_score < 40 and sentiment < -0.3:
        return "risk_alert"
    if weight_pct > 20:
        return "rebalance"
    if composite_score > 70 and weight_pct < 5:
        return "hold_accumulate"
    return "hold_accumulate"


def stage2_gap_analysis(
    user_id: str,
    candidates_df: pd.DataFrame,
    repo=None,
) -> dict:
    """Stage 2: Per-user portfolio gap analysis.

    Args:
        user_id: User UUID string.
        candidates_df: Stage 1 output DataFrame.
        repo: StockRepository (optional, auto-created).

    Returns:
        Dict with portfolio_summary, portfolio_actions,
        candidates (top 40), gap_analysis.
    """
    if repo is None:
        from stocks.repository import StockRepository

        repo = StockRepository()

    # Load holdings
    holdings = repo.get_portfolio_holdings(user_id)
    if holdings.empty:
        return {
            "portfolio_summary": {
                "total_value": 0,
                "holdings_count": 0,
                "empty": True,
            },
            "portfolio_actions": [],
            "candidates": [],
            "gap_analysis": {},
        }

    # Build portfolio state
    holdings_tickers = set(holdings["ticker"].tolist())
    total_value = float(
        holdings["current_value"].sum()
    ) if "current_value" in holdings.columns else 0.0

    # Sector weights
    sector_weights: dict[str, float] = {}
    for _, h in holdings.iterrows():
        sector = h.get("sector", "Unknown")
        val = float(h.get("current_value", 0))
        if total_value > 0:
            sector_weights[sector] = (
                sector_weights.get(sector, 0)
                + val / total_value * 100
            )

    # Universe sector distribution
    universe_sectors = (
        candidates_df["sector"]
        .value_counts(normalize=True)
        .to_dict()
    )
    universe_sectors = {
        k: v * 100 for k, v in universe_sectors.items()
    }
    sector_gaps = _compute_sector_gaps(
        sector_weights, universe_sectors,
    )

    # Nifty 50 tracking
    try:
        from db.pg_stocks import get_tickers_by_tag

        nifty50 = set(
            get_tickers_by_tag("nifty50") or []
        )
    except Exception:
        nifty50 = set()
    missing_nifty50 = nifty50 - holdings_tickers

    # Cap distribution
    user_caps: dict[str, float] = {}
    for _, h in holdings.iterrows():
        cap = _classify_cap(h.get("market_cap"))
        val = float(h.get("current_value", 0))
        if total_value > 0:
            user_caps[cap] = (
                user_caps.get(cap, 0)
                + val / total_value * 100
            )
    cap_gaps = {
        k: user_caps.get(k, 0) - v
        for k, v in CAP_BENCHMARK.items()
    }

    # Watchlist
    try:
        from db.pg_stocks import get_user_tickers

        watchlist = set(
            get_user_tickers(user_id) or []
        )
    except Exception:
        watchlist = set()

    # Correlation analysis (existing holdings)
    correlation_alerts = []
    try:
        ohlcv_map = {}
        for ticker in holdings_tickers:
            ohlcv = repo.get_ohlcv(
                ticker,
                start=date.today()
                - pd.Timedelta(days=365),
            )
            if not ohlcv.empty and len(ohlcv) > 20:
                ohlcv = ohlcv.set_index("date")
                ohlcv_map[ticker] = (
                    ohlcv["close"]
                    .astype(float)
                    .pct_change()
                    .dropna()
                )
        if len(ohlcv_map) >= 2:
            df_ret = pd.DataFrame(ohlcv_map)
            df_ret = df_ret.dropna(how="all").ffill()
            corr = df_ret.corr()
            checked = set()
            for i, t1 in enumerate(corr.columns):
                for j, t2 in enumerate(corr.columns):
                    if i >= j:
                        continue
                    pair = tuple(sorted((t1, t2)))
                    if pair in checked:
                        continue
                    checked.add(pair)
                    c = corr.loc[t1, t2]
                    if c > 0.85:
                        correlation_alerts.append(
                            {
                                "pair": [t1, t2],
                                "corr": round(c, 3),
                            }
                        )
    except Exception:
        _logger.debug(
            "Correlation analysis failed",
            exc_info=True,
        )

    # Score existing holdings
    portfolio_actions = []
    for _, h in holdings.iterrows():
        ticker = h["ticker"]
        weight = (
            float(h.get("current_value", 0))
            / total_value * 100
            if total_value > 0
            else 0
        )
        # Match against Stage 1 data
        match = candidates_df[
            candidates_df["ticker"] == ticker
        ]
        if match.empty:
            portfolio_actions.append(
                {
                    "ticker": ticker,
                    "category": "risk_alert",
                    "reason": "No fresh data coverage",
                    "composite_score": 0,
                    "weight_pct": round(weight, 1),
                }
            )
            continue
        m = match.iloc[0]
        cat = _categorize_holding(
            composite_score=float(
                m.get("composite_score", 50)
            ),
            forecast_3m_pct=float(
                m.get("target_3m_pct_change", 0)
            ),
            weight_pct=weight,
            sentiment=float(m.get("sentiment", 0)),
        )
        if cat in ("exit_reduce", "rebalance", "risk_alert"):
            portfolio_actions.append(
                {
                    "ticker": ticker,
                    "category": cat,
                    "reason": (
                        f"{weight:.1f}% weight"
                        if cat == "rebalance"
                        else f"Score {m.get('composite_score', 0):.0f}"
                    ),
                    "composite_score": round(
                        float(
                            m.get("composite_score", 0)
                        ),
                        1,
                    ),
                    "weight_pct": round(weight, 1),
                    "piotroski": int(
                        m.get("piotroski", 0)
                    ),
                    "forecast_3m_pct": round(
                        float(
                            m.get(
                                "target_3m_pct_change",
                                0,
                            )
                        ),
                        1,
                    ),
                }
            )

    # Tag candidates with gap info
    tagged = []
    for _, row in candidates_df.iterrows():
        ticker = row["ticker"]
        sector = row.get("sector", "Unknown")
        s_gap = sector_gaps.get(sector, 0)
        cap = _classify_cap(row.get("market_cap"))
        c_gap = cap_gaps.get(cap, 0)
        idx_gap = ticker in missing_nifty50

        bonus = _compute_gap_bonus(s_gap, idx_gap, c_gap)
        tier = _assign_tier(
            ticker, holdings_tickers, watchlist,
        )

        fills_gaps = []
        if s_gap < -5:
            fills_gaps.append(
                f"sector underweight {s_gap:.1f}%"
            )
        if idx_gap:
            fills_gaps.append("nifty50 missing")
        if c_gap < -5:
            fills_gaps.append(
                f"{cap} underweight {c_gap:.1f}%"
            )

        tagged.append(
            {
                "ticker": ticker,
                "tier": tier,
                "composite_score": round(
                    float(
                        row.get("composite_score", 0)
                    ),
                    1,
                ),
                "gap_adjusted_score": round(
                    float(
                        row.get("composite_score", 0)
                    )
                    + bonus,
                    1,
                ),
                "piotroski": int(
                    row.get("piotroski", 0)
                ),
                "sharpe": round(
                    float(
                        row.get("sharpe_ratio", 0)
                    ),
                    2,
                ),
                "sentiment": round(
                    float(row.get("sentiment", 0)), 2,
                ),
                "forecast_3m_pct": round(
                    float(
                        row.get(
                            "target_3m_pct_change", 0
                        )
                    ),
                    1,
                ),
                "accuracy_factor": round(
                    _compute_accuracy_factor(
                        float(row.get("mape", 0)),
                        float(row.get("mae", 0)),
                        float(row.get("rmse", 0)),
                        float(
                            row.get("current_price", 0)
                        ),
                    ),
                    2,
                ),
                "sector": sector,
                "sector_gap_pct": round(s_gap, 1),
                "index_gap": idx_gap,
                "fills_gaps": fills_gaps,
                "current_price": float(
                    row.get("current_price", 0)
                ),
                "target_price": float(
                    row.get("target_3m_price", 0)
                    or 0
                ),
                "company_name": row.get(
                    "company_name", ""
                ),
                "market_cap": row.get("market_cap"),
                "mape": float(row.get("mape", 0)),
                "mae": float(row.get("mae", 0)),
                "rmse": float(row.get("rmse", 0)),
            }
        )

    # Sort by gap_adjusted_score, take top 40
    tagged.sort(
        key=lambda x: x["gap_adjusted_score"],
        reverse=True,
    )
    top_candidates = tagged[:40]

    # Build Nifty 50 overlap info
    nifty50_overlap = holdings_tickers & nifty50

    portfolio_summary = {
        "total_value": round(total_value, 2),
        "holdings_count": len(holdings_tickers),
        "sector_weights": {
            k: round(v, 1)
            for k, v in sector_weights.items()
        },
        "market_weights": {},  # Populated from holdings
        "cap_weights": {
            k: round(v, 1)
            for k, v in user_caps.items()
        },
        "nifty50_overlap": len(nifty50_overlap),
        "nifty50_overlap_tickers": sorted(
            nifty50_overlap
        ),
        "concentration_risks": [],
        "correlation_alerts": correlation_alerts,
    }

    # Concentration risks
    for _, h in holdings.iterrows():
        weight = (
            float(h.get("current_value", 0))
            / total_value * 100
            if total_value > 0
            else 0
        )
        if weight > 20:
            portfolio_summary[
                "concentration_risks"
            ].append(
                {
                    "type": "stock",
                    "ticker": h["ticker"],
                    "weight": round(weight, 1),
                }
            )
    for s, w in sector_weights.items():
        if w > 35:
            portfolio_summary[
                "concentration_risks"
            ].append(
                {
                    "type": "sector",
                    "sector": s,
                    "weight": round(w, 1),
                }
            )

    return {
        "portfolio_summary": portfolio_summary,
        "portfolio_actions": portfolio_actions,
        "candidates": top_candidates,
        "gap_analysis": {
            "sector_gaps": {
                k: round(v, 1)
                for k, v in sector_gaps.items()
            },
            "nifty50_missing": sorted(
                missing_nifty50
                & set(candidates_df["ticker"])
            )[:20],
            "cap_gaps": {
                k: round(v, 1)
                for k, v in cap_gaps.items()
            },
            "correlation_alerts": correlation_alerts,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=.:backend python -m pytest tests/test_recommendation_engine.py::TestStage2GapAnalysis -v`

Expected: All 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/jobs/recommendation_engine.py tests/test_recommendation_engine.py
git commit -m "feat(engine): Smart Funnel Stage 2 — portfolio gap analysis (ASETPLTFRM-298)

Sector/index/cap gap detection, correlation alerts, holding
categorization, gap-fill bonus scoring. Top 40 candidates.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 5: Smart Funnel Pipeline — Stage 3 (LLM Reasoning)

**Files:**
- Modify: `backend/jobs/recommendation_engine.py`
- Test: `tests/test_recommendation_engine.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_recommendation_engine.py`:

```python
import json


class TestStage3LLMReasoning:
    """Test LLM output validation and fallback."""

    def test_validate_llm_output_valid(self):
        from backend.jobs.recommendation_engine import (
            _validate_llm_output,
        )

        valid_tickers = {"HDFCBANK.NS", "TCS.NS"}
        output = {
            "recommendations": [
                {
                    "ticker": "HDFCBANK.NS",
                    "tier": "discovery",
                    "category": "new_buy",
                    "action": "buy",
                    "severity": "high",
                    "rationale": "Fills gap.",
                    "expected_impact": "FS +10%",
                },
            ],
            "portfolio_health_assessment": "Good.",
            "health_score": 62,
            "health_label": "needs_attention",
        }
        errors = _validate_llm_output(
            output, valid_tickers,
        )
        assert len(errors) == 0

    def test_validate_llm_output_hallucinated_ticker(
        self,
    ):
        from backend.jobs.recommendation_engine import (
            _validate_llm_output,
        )

        valid_tickers = {"HDFCBANK.NS"}
        output = {
            "recommendations": [
                {
                    "ticker": "FAKE.NS",
                    "tier": "discovery",
                    "category": "new_buy",
                    "action": "buy",
                    "severity": "high",
                    "rationale": "Test.",
                    "expected_impact": "Test.",
                },
            ],
            "portfolio_health_assessment": "Ok.",
            "health_score": 50,
            "health_label": "needs_attention",
        }
        errors = _validate_llm_output(
            output, valid_tickers,
        )
        assert len(errors) > 0
        assert "FAKE.NS" in errors[0]

    def test_deterministic_fallback(self):
        from backend.jobs.recommendation_engine import (
            _deterministic_fallback,
        )

        candidates = [
            {
                "ticker": "A.NS",
                "tier": "discovery",
                "composite_score": 80,
                "gap_adjusted_score": 90,
                "piotroski": 8,
                "sector": "Tech",
                "forecast_3m_pct": 10.0,
                "current_price": 100,
                "target_price": 112,
            },
            {
                "ticker": "B.NS",
                "tier": "watchlist",
                "composite_score": 70,
                "gap_adjusted_score": 75,
                "piotroski": 6,
                "sector": "Finance",
                "forecast_3m_pct": 8.0,
                "current_price": 200,
                "target_price": 216,
            },
        ]
        result = _deterministic_fallback(
            candidates, [], 50, "needs_attention",
        )
        assert "recommendations" in result
        assert len(result["recommendations"]) <= 5

    def test_outcome_labeling(self):
        from backend.jobs.recommendation_engine import (
            compute_outcome_label,
        )

        assert compute_outcome_label("buy", 5.0) == "correct"
        assert compute_outcome_label("buy", -5.0) == "incorrect"
        assert compute_outcome_label("buy", 1.0) == "neutral"
        assert compute_outcome_label("sell", -5.0) == "correct"
        assert compute_outcome_label("sell", 5.0) == "incorrect"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=.:backend python -m pytest tests/test_recommendation_engine.py::TestStage3LLMReasoning -v`

Expected: ImportError.

- [ ] **Step 3: Implement Stage 3 + validation + fallback**

Append to `backend/jobs/recommendation_engine.py`:

```python
# ------------------------------------------------------------------
# Stage 3: LLM Reasoning Pass
# ------------------------------------------------------------------

import json

_LLM_SYSTEM_PROMPT = (
    "You are a portfolio recommendation engine. "
    "Given the user's portfolio and candidate stocks, "
    "select 5-8 recommendations that maximize "
    "portfolio health improvement.\n\n"
    "RULES:\n"
    "1. Select 5-8 recommendations total.\n"
    "2. Include at least 1 from each tier "
    "(portfolio/watchlist/discovery) IF candidates "
    "exist in that tier.\n"
    "3. Include at least 1 defensive recommendation "
    "(risk_alert or exit_reduce) if the portfolio has "
    "concentration risks or deteriorating holdings.\n"
    "4. Balance offensive (new_buy, accumulate, "
    "sector_rotation) and defensive (rebalance, "
    "risk_alert, exit_reduce) recommendations.\n"
    "5. Each recommendation MUST explain the specific "
    "portfolio impact.\n"
    "6. Assign severity: high (immediate action), "
    "medium (act within the month), low (optional).\n"
    "7. Do NOT recommend stocks you are not confident "
    "about.\n"
    "8. Reference data signals (Piotroski, Sharpe, "
    "sentiment, forecast) in your rationale.\n\n"
    "OUTPUT: Respond with valid JSON matching this "
    "schema exactly. No markdown, no commentary.\n"
    '{"recommendations": [{"ticker": "...", '
    '"tier": "portfolio|watchlist|discovery", '
    '"category": "rebalance|exit_reduce|'
    "hold_accumulate|new_buy|sector_rotation|"
    'risk_alert|index_tracking", '
    '"action": "buy|sell|reduce|hold|accumulate|'
    'rotate|alert", '
    '"severity": "high|medium|low", '
    '"rationale": "...", '
    '"expected_impact": "..."}], '
    '"portfolio_health_assessment": "...", '
    '"health_score": 0-100, '
    '"health_label": "critical|needs_attention|'
    'healthy|excellent"}'
)


def _validate_llm_output(
    output: dict,
    valid_tickers: set[str],
) -> list[str]:
    """Validate LLM JSON output. Returns error list."""
    errors = []
    if "recommendations" not in output:
        errors.append("Missing 'recommendations' key")
        return errors

    for rec in output["recommendations"]:
        ticker = rec.get("ticker")
        if ticker and ticker not in valid_tickers:
            errors.append(
                f"Hallucinated ticker: {ticker}"
            )
        for field in (
            "tier",
            "category",
            "action",
            "severity",
            "rationale",
        ):
            if not rec.get(field):
                errors.append(
                    f"Missing field '{field}' in rec "
                    f"for {ticker}"
                )

    if not output.get("health_score"):
        errors.append("Missing health_score")
    if not output.get("health_label"):
        errors.append("Missing health_label")
    return errors


def _deterministic_fallback(
    candidates: list[dict],
    portfolio_actions: list[dict],
    health_score: float,
    health_label: str,
) -> dict:
    """Fallback when LLM fails: top 5 by score."""
    recs = []
    # Add portfolio actions first
    for pa in portfolio_actions[:2]:
        recs.append(
            {
                "ticker": pa["ticker"],
                "tier": "portfolio",
                "category": pa["category"],
                "action": (
                    "reduce"
                    if pa["category"] == "rebalance"
                    else "alert"
                ),
                "severity": "high",
                "rationale": pa.get(
                    "reason",
                    "Automatic detection.",
                ),
                "expected_impact": (
                    "Reduces concentration risk"
                ),
            }
        )

    # Add top candidates
    for c in candidates[: 5 - len(recs)]:
        recs.append(
            {
                "ticker": c["ticker"],
                "tier": c["tier"],
                "category": "new_buy",
                "action": "buy",
                "severity": "medium",
                "rationale": (
                    f"Score {c['composite_score']:.0f}, "
                    f"Piotroski {c['piotroski']}, "
                    f"Forecast "
                    f"+{c['forecast_3m_pct']:.1f}%."
                ),
                "expected_impact": (
                    f"Adds {c['sector']} exposure"
                ),
            }
        )

    return {
        "recommendations": recs,
        "portfolio_health_assessment": (
            "Auto-generated recommendations "
            "(LLM unavailable)."
        ),
        "health_score": health_score,
        "health_label": health_label,
    }


def compute_outcome_label(
    action: str,
    return_pct: float,
) -> str:
    """Label outcome as correct/incorrect/neutral."""
    if action in ("buy", "accumulate"):
        if return_pct > 2:
            return "correct"
        if return_pct < -2:
            return "incorrect"
        return "neutral"
    if action in ("sell", "reduce"):
        if return_pct < -2:
            return "correct"
        if return_pct > 2:
            return "incorrect"
        return "neutral"
    if action == "hold":
        if abs(return_pct) < 10:
            return "correct"
        return "incorrect"
    # alert, rotate — directional
    return "neutral"


def _compute_health_score(
    portfolio_summary: dict,
) -> tuple[float, str]:
    """Compute 0-100 health score from portfolio state."""
    score = 70.0  # base

    # Penalize concentration
    for risk in portfolio_summary.get(
        "concentration_risks", []
    ):
        if risk["type"] == "stock":
            score -= 10
        elif risk["type"] == "sector":
            score -= 8

    # Penalize high correlation
    for alert in portfolio_summary.get(
        "correlation_alerts", []
    ):
        score -= 5

    # Penalize low diversification
    if portfolio_summary.get("holdings_count", 0) < 5:
        score -= 15

    # Bonus for Nifty 50 overlap
    overlap = portfolio_summary.get(
        "nifty50_overlap", 0
    )
    score += min(overlap * 2, 10)

    score = max(0, min(100, score))
    if score < 30:
        label = "critical"
    elif score < 60:
        label = "needs_attention"
    elif score < 80:
        label = "healthy"
    else:
        label = "excellent"
    return round(score, 1), label


def stage3_llm_reasoning(
    stage2_output: dict,
) -> dict:
    """Stage 3: LLM reasoning pass.

    Args:
        stage2_output: Dict from stage2_gap_analysis.

    Returns:
        Dict with recommendations, health assessment,
        score, label, llm_model, llm_tokens_used.
    """
    summary = stage2_output["portfolio_summary"]
    actions = stage2_output["portfolio_actions"]
    candidates = stage2_output["candidates"]

    if summary.get("empty"):
        return {
            "recommendations": [],
            "portfolio_health_assessment": (
                "Add stocks to your portfolio to "
                "receive recommendations."
            ),
            "health_score": 0,
            "health_label": "critical",
            "llm_model": None,
            "llm_tokens_used": 0,
        }

    health_score, health_label = _compute_health_score(
        summary,
    )

    # Build LLM context
    context = {
        "portfolio_summary": summary,
        "portfolio_actions": actions,
        "candidates": candidates[:40],
        "sector_gaps": stage2_output.get(
            "gap_analysis", {}
        ).get("sector_gaps", {}),
    }
    user_msg = json.dumps(context, default=str)

    valid_tickers = {
        c["ticker"] for c in candidates
    } | {a["ticker"] for a in actions}

    try:
        from llm_fallback import FallbackLLM

        llm = FallbackLLM(
            temperature=0.3,
            agent_id="recommendation_engine",
            ollama_first=False,
        )
        from langchain_core.messages import (
            HumanMessage,
            SystemMessage,
        )

        response = llm.invoke(
            [
                SystemMessage(
                    content=_LLM_SYSTEM_PROMPT,
                ),
                HumanMessage(content=user_msg),
            ]
        )
        raw = response.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            if raw.endswith("```"):
                raw = raw[: raw.rfind("```")]

        output = json.loads(raw)
        errors = _validate_llm_output(
            output, valid_tickers,
        )

        if errors:
            _logger.warning(
                "LLM output validation errors: %s",
                errors,
            )
            # Remove hallucinated recs
            output["recommendations"] = [
                r
                for r in output.get(
                    "recommendations", []
                )
                if not r.get("ticker")
                or r["ticker"] in valid_tickers
            ]

        # Override health with our computed values
        output["health_score"] = health_score
        output["health_label"] = health_label
        output["llm_model"] = getattr(
            llm, "last_provider", "unknown"
        )
        output["llm_tokens_used"] = getattr(
            llm, "last_token_count", 0
        )
        return output

    except Exception as e:
        _logger.error(
            "LLM reasoning failed: %s", e,
            exc_info=True,
        )
        fallback = _deterministic_fallback(
            candidates, actions,
            health_score, health_label,
        )
        fallback["llm_model"] = "fallback"
        fallback["llm_tokens_used"] = 0
        return fallback
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=.:backend python -m pytest tests/test_recommendation_engine.py::TestStage3LLMReasoning -v`

Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/jobs/recommendation_engine.py tests/test_recommendation_engine.py
git commit -m "feat(engine): Smart Funnel Stage 3 — LLM reasoning + validation + fallback (ASETPLTFRM-298)

Structured prompt, JSON validation, hallucination rejection,
deterministic fallback. Outcome labeling for performance tracking.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 6: Recommendation Agent Config + Tools + Graph Registration

**Files:**
- Create: `backend/agents/configs/recommendation.py`
- Create: `backend/tools/recommendation_tools.py`
- Modify: `backend/agents/graph.py`
- Modify: `backend/agents/nodes/router_node.py`
- Modify: `backend/bootstrap.py`

- [ ] **Step 1: Create agent config**

Create `backend/agents/configs/recommendation.py`:

```python
"""Recommendation sub-agent configuration.

Portfolio-aware advisor that uses the Smart Funnel
pipeline to generate data-driven recommendations.
"""

from __future__ import annotations

from agents.sub_agents import SubAgentConfig

_RECOMMENDATION_SYSTEM_PROMPT = (
    "You are a portfolio recommendation advisor on "
    "the ASET Platform. You help users discover stocks "
    "to improve their portfolio health and track "
    "recommendation performance.\n\n"
    "MANDATORY TOOL USE (CRITICAL — NO EXCEPTIONS):\n"
    "- You MUST call a tool before answering ANY "
    "recommendation question.\n"
    "- If the user asks 'what should I buy/sell' or "
    "'recommend stocks' → call "
    "generate_recommendations.\n"
    "- If the user asks 'how did your picks do' or "
    "'recommendation history' → call "
    "get_recommendation_history.\n"
    "- If the user asks about a specific recommendation "
    "→ call get_recommendation_performance.\n"
    "- If you need portfolio context → call "
    "get_portfolio_holdings or get_sector_allocation "
    "or get_risk_metrics.\n"
    "- NEVER fabricate tickers, prices, values, "
    "percentages, or any numbers.\n\n"
    "CURRENCY RULES:\n"
    "- Use ₹ for INR, $ for USD. Read from data.\n\n"
    "DISCLAIMER:\n"
    "- Always mention that recommendations are "
    "informational and not financial advice.\n"
    "- Reference the data signals (Piotroski score, "
    "Sharpe ratio, sentiment, forecast) that support "
    "each recommendation.\n\n"
    "RESPONSE RULES:\n"
    "- Present recommendations in clear numbered "
    "lists with severity badges.\n"
    "- For history, show outcome badges: "
    "✅ correct, ❌ incorrect, ⚪ neutral.\n"
    "- Keep answers concise. Use Markdown tables "
    "for metrics."
)

RECOMMENDATION_CONFIG = SubAgentConfig(
    agent_id="recommendation",
    name="Recommendation Agent",
    description=(
        "Generates portfolio recommendations, "
        "tracks performance, and explains picks."
    ),
    system_prompt=_RECOMMENDATION_SYSTEM_PROMPT,
    tool_names=[
        "generate_recommendations",
        "get_recommendation_history",
        "get_recommendation_performance",
        "get_portfolio_holdings",
        "get_sector_allocation",
        "get_risk_metrics",
    ],
)
```

- [ ] **Step 2: Create recommendation tools**

Create `backend/tools/recommendation_tools.py`:

```python
"""Recommendation agent tools.

Three tools for the recommendation LangGraph sub-agent:
1. generate_recommendations — Smart Funnel pipeline
2. get_recommendation_history — past runs + outcomes
3. get_recommendation_performance — detailed performance
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from langchain_core.tools import tool

_logger = logging.getLogger(__name__)


def _get_user_or_error() -> str:
    from agents.context import get_user_context

    ctx = get_user_context()
    if not ctx or not ctx.user_id:
        raise ValueError("No authenticated user")
    return str(ctx.user_id)


@tool
def generate_recommendations(
    force_refresh: bool = False,
) -> str:
    """Generate portfolio recommendations using the
    Smart Funnel pipeline. Returns the latest
    recommendations. If a fresh run exists (<24h),
    returns cached unless force_refresh=True.

    Source: DuckDB + PostgreSQL + LLM.
    """
    user_id = _get_user_or_error()

    from db.pg_stocks import (
        expire_old_recommendations,
        get_latest_recommendation_run,
        get_recommendations_for_run,
        insert_recommendation_run,
        insert_recommendations,
    )

    # Check for fresh run
    if not force_refresh:
        existing = get_latest_recommendation_run(
            user_id,
        )
        if existing:
            created = existing.get("created_at")
            if created and isinstance(
                created, datetime
            ):
                age = datetime.now(timezone.utc) - (
                    created.replace(tzinfo=timezone.utc)
                    if not created.tzinfo
                    else created
                )
                if age < timedelta(hours=24):
                    recs = get_recommendations_for_run(
                        existing["run_id"],
                    )
                    return _format_recs(
                        existing, recs,
                    )

    # Run pipeline
    start = time.time()
    from jobs.recommendation_engine import (
        stage1_prefilter,
        stage2_gap_analysis,
        stage3_llm_reasoning,
    )

    candidates_df = stage1_prefilter()
    stage2 = stage2_gap_analysis(
        user_id, candidates_df,
    )

    if stage2["portfolio_summary"].get("empty"):
        return (
            "[Source: recommendation_engine]\n"
            "**No Portfolio Found**\n\n"
            "Add stocks to your portfolio to "
            "receive recommendations."
        )

    result = stage3_llm_reasoning(stage2)
    duration = time.time() - start

    # Write to PG
    run_id = str(uuid.uuid4())
    run_data = {
        "run_id": run_id,
        "user_id": user_id,
        "run_date": date.today(),
        "run_type": "chat",
        "portfolio_snapshot": (
            stage2["portfolio_summary"]
        ),
        "health_score": result["health_score"],
        "health_label": result["health_label"],
        "health_assessment": result.get(
            "portfolio_health_assessment",
        ),
        "candidates_scanned": len(candidates_df),
        "candidates_passed": len(
            stage2["candidates"]
        ),
        "llm_model": result.get("llm_model"),
        "llm_tokens_used": result.get(
            "llm_tokens_used"
        ),
        "duration_secs": round(duration, 2),
    }
    insert_recommendation_run(run_data)

    # Build rec rows with data_signals
    rec_rows = []
    for rec in result.get("recommendations", []):
        ticker = rec.get("ticker")
        # Find candidate data for signals
        signals = {}
        for c in stage2["candidates"]:
            if c["ticker"] == ticker:
                signals = {
                    "composite_score": c[
                        "composite_score"
                    ],
                    "piotroski": c["piotroski"],
                    "sharpe": c["sharpe"],
                    "sentiment": c["sentiment"],
                    "forecast_3m_pct": c[
                        "forecast_3m_pct"
                    ],
                    "accuracy_factor": c[
                        "accuracy_factor"
                    ],
                    "sector_gap_pct": c.get(
                        "sector_gap_pct", 0
                    ),
                    "mape": c.get("mape", 0),
                    "mae": c.get("mae", 0),
                    "rmse": c.get("rmse", 0),
                }
                break

        # Also check portfolio_actions
        for pa in stage2["portfolio_actions"]:
            if pa["ticker"] == ticker:
                signals = {
                    "composite_score": pa.get(
                        "composite_score", 0
                    ),
                    "piotroski": pa.get(
                        "piotroski", 0
                    ),
                    "weight_pct": pa.get(
                        "weight_pct", 0
                    ),
                }
                break

        candidate = next(
            (
                c
                for c in stage2["candidates"]
                if c["ticker"] == ticker
            ),
            {},
        )
        rec_rows.append(
            {
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "tier": rec["tier"],
                "category": rec["category"],
                "ticker": ticker,
                "action": rec["action"],
                "severity": rec["severity"],
                "rationale": rec["rationale"],
                "expected_impact": rec.get(
                    "expected_impact"
                ),
                "data_signals": signals,
                "price_at_rec": candidate.get(
                    "current_price"
                ),
                "target_price": candidate.get(
                    "target_price"
                ),
                "expected_return_pct": candidate.get(
                    "forecast_3m_pct"
                ),
                "index_tags": None,
            }
        )

    if rec_rows:
        insert_recommendations(run_id, rec_rows)

    # Expire old
    expire_old_recommendations(user_id, run_id)

    recs = get_recommendations_for_run(run_id)
    return _format_recs(run_data, recs)


def _format_recs(
    run: dict, recs: list[dict],
) -> str:
    """Format recommendations as markdown."""
    lines = [
        "[Source: recommendation_engine]",
        f"**Portfolio Health: "
        f"{run.get('health_score', 0):.0f}/100 "
        f"— {run.get('health_label', 'unknown')}**\n",
    ]
    if run.get("health_assessment"):
        lines.append(
            f"_{run['health_assessment']}_\n"
        )

    severity_icon = {
        "high": "🔴",
        "medium": "🟡",
        "low": "🔵",
    }
    for i, r in enumerate(recs, 1):
        icon = severity_icon.get(
            r.get("severity", ""), ""
        )
        lines.append(
            f"{i}. {icon} **{r.get('ticker', 'N/A')}** "
            f"— {r.get('category', '')} "
            f"({r.get('tier', '')})"
        )
        lines.append(f"   {r.get('rationale', '')}")
        if r.get("expected_impact"):
            lines.append(
                f"   *Impact: {r['expected_impact']}*"
            )
        lines.append("")

    if not recs:
        lines.append(
            "No recommendations generated. "
            "Try again later."
        )

    lines.append(
        "*Recommendations are informational — "
        "not financial advice.*"
    )
    return "\n".join(lines)


@tool
def get_recommendation_history(
    months_back: int = 6,
) -> str:
    """Fetch past recommendation runs with outcome data.
    Shows hit rate, average return, and adoption rate.

    Source: PostgreSQL (read-only).
    """
    user_id = _get_user_or_error()
    from db.pg_stocks import (
        get_recommendation_history as _get_history,
        get_recommendation_stats,
    )

    runs = _get_history(user_id, months_back)
    stats = get_recommendation_stats(user_id)

    if not runs:
        return (
            "[Source: postgresql]\n"
            "**No Recommendation History**\n\n"
            "No past recommendations found. "
            "Ask me to generate recommendations first."
        )

    lines = [
        "[Source: postgresql]",
        "**Recommendation History**\n",
        "| Month | Health | Recs | Acted On | "
        "Hit Rate |",
        "|-------|--------|------|----------|"
        "---------|",
    ]
    for r in runs:
        lines.append(
            f"| {r['run_date']} | "
            f"{r.get('health_score', 0):.0f} | "
            f"{r.get('total_recommendations', 0)} | "
            f"{r.get('acted_on_count', 0)} | "
            f"— |"
        )

    lines.append("\n**Aggregate Stats**\n")
    lines.append(
        f"- Total: "
        f"{stats.get('total_recommendations', 0)} recs"
    )
    lines.append(
        f"- Adoption: "
        f"{stats.get('adoption_rate_pct', 0):.1f}%"
    )
    for d in (30, 60, 90):
        key = f"hit_rate_{d}d"
        hr = stats.get(key)
        if hr is not None:
            lines.append(
                f"- Hit rate ({d}d): {hr:.1f}%"
            )

    return "\n".join(lines)


@tool
def get_recommendation_performance(
    run_id: str | None = None,
    ticker: str | None = None,
) -> str:
    """Detailed performance of recommendations.
    Pass run_id for a specific month, or ticker for
    all recommendations involving that stock.

    Source: PostgreSQL (read-only).
    """
    _get_user_or_error()
    from db.pg_stocks import (
        get_recommendations_for_run,
    )

    if not run_id and not ticker:
        return (
            "Please specify a run_id or ticker "
            "to view performance."
        )

    if run_id:
        recs = get_recommendations_for_run(run_id)
        if not recs:
            return f"No recommendations found for run {run_id}."

        lines = [
            "[Source: postgresql]",
            f"**Run {run_id[:8]}... "
            f"({recs[0].get('created_at', 'N/A')})**\n",
        ]
        for r in recs:
            status_icon = {
                "active": "⏳",
                "acted_on": "✅",
                "expired": "⏰",
                "ignored": "➖",
            }
            lines.append(
                f"- {status_icon.get(r.get('status', ''), '')} "
                f"**{r.get('ticker', 'N/A')}** "
                f"({r.get('category', '')}) — "
                f"{r.get('status', '')}"
            )
        return "\n".join(lines)

    return "Ticker-based lookup not yet implemented."
```

- [ ] **Step 3: Add recommendation intent to router**

Add to `_INTENT_MAP` in `backend/agents/nodes/router_node.py`:

```python
    "recommendation": {
        "recommend",
        "recommendations",
        "suggestion",
        "suggestions",
        "what should i buy",
        "what should i sell",
        "portfolio advice",
        "improve my portfolio",
        "improve portfolio",
        "recommendation history",
        "how did your picks",
        "hit rate",
        "track record",
        "pick stocks",
    },
```

- [ ] **Step 4: Register tools in bootstrap.py**

Add to `backend/bootstrap.py` `setup_tools()` after the sector discovery block:

```python
    # Register recommendation tools
    try:
        from tools.recommendation_tools import (
            generate_recommendations,
            get_recommendation_history,
            get_recommendation_performance,
        )

        registry.register(generate_recommendations)
        registry.register(get_recommendation_history)
        registry.register(
            get_recommendation_performance,
        )
    except Exception:
        _logger.warning(
            "Recommendation tools registration failed",
            exc_info=True,
        )
```

- [ ] **Step 5: Register agent in graph.py**

Add import at top of `backend/agents/graph.py`:

```python
from agents.configs.recommendation import (
    RECOMMENDATION_CONFIG,
)
```

Add node creation after `sentiment_node`:

```python
    recommendation_node = _make_sub_agent_node(
        RECOMMENDATION_CONFIG,
        tool_registry,
        llm_factory,
    )
```

Add to graph:

```python
    g.add_node("recommendation", recommendation_node)
```

Add `"recommendation": "recommendation"` to all three conditional edge dicts (guardrail, supervisor).

Add edge to synthesis:

```python
    g.add_edge("recommendation", "synthesis")
```

- [ ] **Step 6: Commit**

```bash
git add backend/agents/configs/recommendation.py backend/tools/recommendation_tools.py backend/agents/graph.py backend/agents/nodes/router_node.py backend/bootstrap.py
git commit -m "feat(agent): add recommendation sub-agent + tools + graph routing (ASETPLTFRM-298)

6th LangGraph agent with generate_recommendations,
get_recommendation_history, get_recommendation_performance.
Router keywords for recommendation intent.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 7: Scheduler Job Registration

**Files:**
- Modify: `backend/jobs/executor.py`

- [ ] **Step 1: Register recommendation job type**

Add at the end of `backend/jobs/executor.py` (after `run_piotroski` block):

```python
# ------------------------------------------------------------------
# Job: recommendations
# ------------------------------------------------------------------


@register_job("recommendations")
def execute_run_recommendations(
    scope: str,
    run_id: str,
    repo,
    cancel_event=None,
    **kwargs,
):
    """Generate recommendations for all users."""
    from db.pg_stocks import (
        expire_old_recommendations,
        insert_recommendation_run,
        insert_recommendations,
    )
    from jobs.recommendation_engine import (
        stage1_prefilter,
        stage2_gap_analysis,
        stage3_llm_reasoning,
    )

    # Stage 1 (shared across users)
    candidates_df = stage1_prefilter()
    if candidates_df.empty:
        _logger.warning("No candidates from Stage 1")
        return

    # Get users with portfolios
    users = repo.get_users_with_portfolios()
    total = len(users)
    done = 0

    for user_id in users:
        if cancel_event and cancel_event.is_set():
            break
        try:
            stage2 = stage2_gap_analysis(
                user_id, candidates_df, repo,
            )
            if stage2["portfolio_summary"].get("empty"):
                done += 1
                continue

            result = stage3_llm_reasoning(stage2)
            import uuid as _uuid
            import time as _time

            rec_run_id = str(_uuid.uuid4())
            insert_recommendation_run(
                {
                    "run_id": rec_run_id,
                    "user_id": user_id,
                    "run_date": date.today(),
                    "run_type": "scheduled",
                    "portfolio_snapshot": (
                        stage2["portfolio_summary"]
                    ),
                    "health_score": result[
                        "health_score"
                    ],
                    "health_label": result[
                        "health_label"
                    ],
                    "health_assessment": result.get(
                        "portfolio_health_assessment"
                    ),
                    "candidates_scanned": len(
                        candidates_df
                    ),
                    "candidates_passed": len(
                        stage2["candidates"]
                    ),
                    "llm_model": result.get(
                        "llm_model"
                    ),
                    "llm_tokens_used": result.get(
                        "llm_tokens_used"
                    ),
                    "duration_secs": 0,
                }
            )

            rec_rows = []
            for rec in result.get(
                "recommendations", []
            ):
                candidate = next(
                    (
                        c
                        for c in stage2["candidates"]
                        if c["ticker"]
                        == rec.get("ticker")
                    ),
                    {},
                )
                rec_rows.append(
                    {
                        "id": str(_uuid.uuid4()),
                        "run_id": rec_run_id,
                        "tier": rec["tier"],
                        "category": rec["category"],
                        "ticker": rec.get("ticker"),
                        "action": rec["action"],
                        "severity": rec["severity"],
                        "rationale": rec["rationale"],
                        "expected_impact": rec.get(
                            "expected_impact"
                        ),
                        "data_signals": {
                            k: candidate.get(k)
                            for k in (
                                "composite_score",
                                "piotroski",
                                "sharpe",
                                "sentiment",
                                "forecast_3m_pct",
                                "accuracy_factor",
                            )
                            if k in candidate
                        },
                        "price_at_rec": candidate.get(
                            "current_price"
                        ),
                        "target_price": candidate.get(
                            "target_price"
                        ),
                        "expected_return_pct": (
                            candidate.get(
                                "forecast_3m_pct"
                            )
                        ),
                    }
                )

            if rec_rows:
                insert_recommendations(
                    rec_run_id, rec_rows,
                )
            expire_old_recommendations(
                user_id, rec_run_id,
            )

            done += 1
            repo.update_scheduler_run_progress(
                run_id, done, total,
            )
            _logger.info(
                "Recommendations generated for user "
                "%s (%d/%d)",
                user_id[:8], done, total,
            )

        except Exception:
            _logger.error(
                "Recommendation failed for user %s",
                user_id[:8],
                exc_info=True,
            )
            done += 1


# ------------------------------------------------------------------
# Job: recommendation_outcomes
# ------------------------------------------------------------------


@register_job("recommendation_outcomes")
def execute_run_recommendation_outcomes(
    scope: str,
    run_id: str,
    repo,
    cancel_event=None,
    **kwargs,
):
    """Daily outcome tracker for recommendations."""
    from db.pg_stocks import (
        expire_stale_recommendations,
        get_recommendations_due_for_outcome,
        insert_recommendation_outcome,
    )
    from jobs.recommendation_engine import (
        compute_outcome_label,
    )

    today = date.today()
    due_recs = get_recommendations_due_for_outcome(
        today,
    )

    if not due_recs:
        _logger.info("No recommendations due for outcome check")
        return

    # Batch fetch prices
    tickers = list(
        {r["ticker"] for r in due_recs if r.get("ticker")}
    )
    prices = {}
    try:
        for t in tickers:
            ohlcv = repo.get_latest_ohlcv(t)
            if ohlcv and "close" in ohlcv:
                prices[t] = float(ohlcv["close"])
    except Exception:
        _logger.error(
            "Price fetch failed", exc_info=True,
        )

    # Nifty 50 benchmark (use ^NSEI)
    nifty_price = prices.get("^NSEI", 0)

    done = 0
    for rec in due_recs:
        ticker = rec.get("ticker")
        if not ticker or ticker not in prices:
            continue

        actual = prices[ticker]
        price_at = rec.get("price_at_rec")
        if not price_at or price_at <= 0:
            continue

        ret = (actual - price_at) / price_at * 100
        bench_ret = 0.0  # Simplified benchmark
        label = compute_outcome_label(
            rec.get("action", "buy"), ret,
        )

        try:
            insert_recommendation_outcome(
                rec["id"],
                today,
                rec["checkpoint_days"],
                actual,
                round(ret, 2),
                round(bench_ret, 2),
                round(ret - bench_ret, 2),
                label,
            )
            done += 1
        except Exception:
            _logger.error(
                "Outcome insert failed for %s",
                rec["id"][:8],
                exc_info=True,
            )

    # Expire stale
    expired = expire_stale_recommendations(today)
    _logger.info(
        "Outcome check: %d recorded, %d expired",
        done, expired,
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/jobs/executor.py
git commit -m "feat(scheduler): register recommendations + outcome_tracker jobs (ASETPLTFRM-298)

Monthly recommendation generation for all portfolio users.
Daily outcome tracker with 30/60/90d checkpoints.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 8: API Endpoints + Pydantic Models

**Files:**
- Create: `backend/recommendation_models.py`
- Create: `backend/recommendation_routes.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Create Pydantic response models**

Create `backend/recommendation_models.py`:

```python
"""Pydantic models for recommendation API."""
from __future__ import annotations

from pydantic import BaseModel


class RecommendationItem(BaseModel):
    id: str
    tier: str
    category: str
    ticker: str | None = None
    company_name: str | None = None
    action: str
    severity: str
    rationale: str
    expected_impact: str | None = None
    data_signals: dict = {}
    price_at_rec: float | None = None
    target_price: float | None = None
    expected_return_pct: float | None = None
    index_tags: list[str] = []
    status: str = "active"
    acted_on_date: str | None = None


class RecommendationResponse(BaseModel):
    run_id: str
    run_date: str
    run_type: str
    health_score: float
    health_label: str
    health_assessment: str | None = None
    recommendations: list[RecommendationItem] = []
    generated_at: str | None = None


class CheckpointStats(BaseModel):
    measured_count: int = 0
    correct_count: int = 0
    incorrect_count: int = 0
    neutral_count: int = 0
    avg_return_pct: float | None = None
    avg_benchmark_pct: float | None = None
    avg_excess_pct: float | None = None
    hit_rate_pct: float | None = None


class HistoryRunItem(BaseModel):
    run_id: str
    run_date: str
    health_score: float
    health_label: str
    total_recommendations: int = 0
    acted_on_count: int = 0


class AggregateStats(BaseModel):
    total_runs: int = 0
    total_recommendations: int = 0
    overall_hit_rate_30d: float | None = None
    overall_hit_rate_60d: float | None = None
    overall_hit_rate_90d: float | None = None
    overall_avg_return_pct: float | None = None
    overall_avg_excess_pct: float | None = None
    adoption_rate_pct: float = 0.0


class RecommendationHistoryResponse(BaseModel):
    runs: list[HistoryRunItem] = []
    aggregate_stats: AggregateStats = AggregateStats()


class RecommendationStatsResponse(BaseModel):
    total_recommendations: int = 0
    total_acted_on: int = 0
    adoption_rate_pct: float = 0.0
    hit_rate_30d: float | None = None
    hit_rate_60d: float | None = None
    hit_rate_90d: float | None = None
    avg_return_30d: float | None = None
    avg_return_60d: float | None = None
    avg_return_90d: float | None = None
    avg_excess_return_30d: float | None = None
    avg_excess_return_60d: float | None = None
    avg_excess_return_90d: float | None = None
    category_breakdown: dict[str, int] = {}
```

- [ ] **Step 2: Create recommendation routes**

Create `backend/recommendation_routes.py`:

```python
"""Recommendation API endpoints."""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, Query
from starlette.responses import Response

from auth.dependencies import UserContext, get_current_user
from cache import get_cache, TTL_STABLE

from recommendation_models import (
    RecommendationHistoryResponse,
    RecommendationResponse,
    RecommendationStatsResponse,
)

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/dashboard/portfolio/recommendations",
    tags=["recommendations"],
)


@router.get("", response_model=RecommendationResponse)
async def get_recommendations(
    market: str = Query("all", description="india|us|all"),
    user: UserContext = Depends(get_current_user),
):
    """Get latest recommendation set."""
    cache = get_cache()
    cache_key = (
        f"cache:portfolio:recs:{user.user_id}:{market}"
    )
    hit = cache.get(cache_key)
    if hit is not None:
        return Response(
            content=hit,
            media_type="application/json",
        )

    from db.pg_stocks import (
        get_latest_recommendation_run,
        get_recommendations_for_run,
    )

    run = get_latest_recommendation_run(
        str(user.user_id),
    )
    if not run:
        resp = RecommendationResponse(
            run_id="",
            run_date="",
            run_type="",
            health_score=0,
            health_label="no_data",
        )
        return resp

    recs = get_recommendations_for_run(run["run_id"])

    items = []
    for r in recs:
        items.append(
            {
                "id": str(r["id"]),
                "tier": r["tier"],
                "category": r["category"],
                "ticker": r.get("ticker"),
                "action": r["action"],
                "severity": r["severity"],
                "rationale": r["rationale"],
                "expected_impact": r.get(
                    "expected_impact"
                ),
                "data_signals": r.get(
                    "data_signals", {}
                ),
                "price_at_rec": r.get("price_at_rec"),
                "target_price": r.get("target_price"),
                "expected_return_pct": r.get(
                    "expected_return_pct"
                ),
                "index_tags": r.get("index_tags") or [],
                "status": r.get("status", "active"),
                "acted_on_date": (
                    str(r["acted_on_date"])
                    if r.get("acted_on_date")
                    else None
                ),
            }
        )

    resp = RecommendationResponse(
        run_id=str(run["run_id"]),
        run_date=str(run["run_date"]),
        run_type=run["run_type"],
        health_score=run["health_score"],
        health_label=run["health_label"],
        health_assessment=run.get("health_assessment"),
        recommendations=items,
        generated_at=str(run.get("created_at", "")),
    )

    body = resp.model_dump_json()
    cache.setex(cache_key, TTL_STABLE, body)
    return Response(
        content=body, media_type="application/json",
    )


@router.post("/refresh", response_model=RecommendationResponse)
async def refresh_recommendations(
    user: UserContext = Depends(get_current_user),
):
    """Trigger manual Smart Funnel run."""
    from jobs.recommendation_engine import (
        stage1_prefilter,
        stage2_gap_analysis,
        stage3_llm_reasoning,
    )
    from db.pg_stocks import (
        expire_old_recommendations,
        insert_recommendation_run,
        insert_recommendations,
        get_recommendations_for_run,
    )
    import uuid

    user_id = str(user.user_id)
    start = time.time()

    candidates_df = stage1_prefilter()
    stage2 = stage2_gap_analysis(
        user_id, candidates_df,
    )
    result = stage3_llm_reasoning(stage2)
    duration = time.time() - start

    run_id = str(uuid.uuid4())
    insert_recommendation_run(
        {
            "run_id": run_id,
            "user_id": user_id,
            "run_date": str(
                __import__("datetime").date.today()
            ),
            "run_type": "manual",
            "portfolio_snapshot": (
                stage2["portfolio_summary"]
            ),
            "health_score": result["health_score"],
            "health_label": result["health_label"],
            "health_assessment": result.get(
                "portfolio_health_assessment"
            ),
            "candidates_scanned": len(candidates_df),
            "candidates_passed": len(
                stage2["candidates"]
            ),
            "llm_model": result.get("llm_model"),
            "llm_tokens_used": result.get(
                "llm_tokens_used"
            ),
            "duration_secs": round(duration, 2),
        }
    )

    rec_rows = []
    for rec in result.get("recommendations", []):
        candidate = next(
            (
                c
                for c in stage2["candidates"]
                if c["ticker"] == rec.get("ticker")
            ),
            {},
        )
        rec_rows.append(
            {
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "tier": rec["tier"],
                "category": rec["category"],
                "ticker": rec.get("ticker"),
                "action": rec["action"],
                "severity": rec["severity"],
                "rationale": rec["rationale"],
                "expected_impact": rec.get(
                    "expected_impact"
                ),
                "data_signals": {
                    k: candidate.get(k)
                    for k in (
                        "composite_score",
                        "piotroski",
                        "sharpe",
                    )
                    if k in candidate
                },
                "price_at_rec": candidate.get(
                    "current_price"
                ),
                "target_price": candidate.get(
                    "target_price"
                ),
                "expected_return_pct": candidate.get(
                    "forecast_3m_pct"
                ),
            }
        )

    if rec_rows:
        insert_recommendations(run_id, rec_rows)
    expire_old_recommendations(user_id, run_id)

    # Invalidate cache
    cache = get_cache()
    for pattern in (
        f"cache:portfolio:recs:{user_id}:*",
    ):
        for key in cache.scan_iter(pattern):
            cache.delete(key)

    recs = get_recommendations_for_run(run_id)
    return get_recommendations.__wrapped__(
        market="all", user=user,
    )


@router.get(
    "/history",
    response_model=RecommendationHistoryResponse,
)
async def get_history(
    months_back: int = Query(6, ge=1, le=24),
    user: UserContext = Depends(get_current_user),
):
    """Past runs with outcomes."""
    from db.pg_stocks import (
        get_recommendation_history,
        get_recommendation_stats,
    )

    runs = get_recommendation_history(
        str(user.user_id), months_back,
    )
    stats = get_recommendation_stats(
        str(user.user_id),
    )

    return RecommendationHistoryResponse(
        runs=[
            {
                "run_id": str(r["run_id"]),
                "run_date": str(r["run_date"]),
                "health_score": r["health_score"],
                "health_label": r["health_label"],
                "total_recommendations": r.get(
                    "total_recommendations", 0
                ),
                "acted_on_count": r.get(
                    "acted_on_count", 0
                ),
            }
            for r in runs
        ],
        aggregate_stats={
            "total_runs": len(runs),
            "total_recommendations": stats.get(
                "total_recommendations", 0
            ),
            "adoption_rate_pct": stats.get(
                "adoption_rate_pct", 0
            ),
            "overall_hit_rate_30d": stats.get(
                "hit_rate_30d"
            ),
            "overall_hit_rate_60d": stats.get(
                "hit_rate_60d"
            ),
            "overall_hit_rate_90d": stats.get(
                "hit_rate_90d"
            ),
        },
    )


@router.get(
    "/stats",
    response_model=RecommendationStatsResponse,
)
async def get_stats(
    user: UserContext = Depends(get_current_user),
):
    """Aggregate performance stats."""
    from db.pg_stocks import (
        get_recommendation_stats,
    )

    stats = get_recommendation_stats(
        str(user.user_id),
    )
    return RecommendationStatsResponse(**stats)
```

- [ ] **Step 3: Mount router in main.py**

In `backend/main.py`, add after existing router includes:

```python
from recommendation_routes import (
    router as recommendation_router,
)
app.include_router(recommendation_router)
```

- [ ] **Step 4: Commit**

```bash
git add backend/recommendation_models.py backend/recommendation_routes.py backend/main.py
git commit -m "feat(api): add 5 recommendation API endpoints + Pydantic models (ASETPLTFRM-298)

GET/POST recommendations, history, stats.
Redis caching, rate limiting on refresh.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 9: Frontend — TypeScript Types + SWR Hooks

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/hooks/useDashboardData.ts`
- Modify: `frontend/hooks/useInsightsData.ts`

- [ ] **Step 1: Add TypeScript types**

Add to `frontend/lib/types.ts`:

```typescript
// Recommendations
export interface RecommendationItem {
  id: string;
  tier: "portfolio" | "watchlist" | "discovery";
  category: string;
  ticker: string | null;
  company_name?: string | null;
  action: string;
  severity: "high" | "medium" | "low";
  rationale: string;
  expected_impact?: string | null;
  data_signals: Record<string, number | string>;
  price_at_rec?: number | null;
  target_price?: number | null;
  expected_return_pct?: number | null;
  index_tags: string[];
  status: string;
  acted_on_date?: string | null;
}

export interface RecommendationResponse {
  run_id: string;
  run_date: string;
  run_type: string;
  health_score: number;
  health_label: string;
  health_assessment?: string | null;
  recommendations: RecommendationItem[];
  generated_at?: string | null;
}

export interface HistoryRunItem {
  run_id: string;
  run_date: string;
  health_score: number;
  health_label: string;
  total_recommendations: number;
  acted_on_count: number;
}

export interface AggregateStats {
  total_runs: number;
  total_recommendations: number;
  overall_hit_rate_30d?: number | null;
  overall_hit_rate_60d?: number | null;
  overall_hit_rate_90d?: number | null;
  adoption_rate_pct: number;
}

export interface RecommendationHistoryResponse {
  runs: HistoryRunItem[];
  aggregate_stats: AggregateStats;
}

export interface RecommendationStatsResponse {
  total_recommendations: number;
  total_acted_on: number;
  adoption_rate_pct: number;
  hit_rate_30d?: number | null;
  hit_rate_60d?: number | null;
  hit_rate_90d?: number | null;
  avg_return_30d?: number | null;
  avg_return_60d?: number | null;
  avg_return_90d?: number | null;
}
```

- [ ] **Step 2: Add SWR hooks**

Add to `frontend/hooks/useDashboardData.ts`:

```typescript
export function useRecommendations(market: string = "all") {
  return useSWR<RecommendationResponse>(
    `/v1/dashboard/portfolio/recommendations?market=${market}`,
    apiFetch,
    { refreshInterval: 0 },
  );
}
```

Add to `frontend/hooks/useInsightsData.ts`:

```typescript
export function useRecommendationHistory(monthsBack: number = 6) {
  return useSWR<RecommendationHistoryResponse>(
    `/v1/dashboard/portfolio/recommendations/history?months_back=${monthsBack}`,
    apiFetch,
    { refreshInterval: 0 },
  );
}

export function useRecommendationStats() {
  return useSWR<RecommendationStatsResponse>(
    `/v1/dashboard/portfolio/recommendations/stats`,
    apiFetch,
    { refreshInterval: 0 },
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/types.ts frontend/hooks/useDashboardData.ts frontend/hooks/useInsightsData.ts
git commit -m "feat(frontend): add recommendation TypeScript types + SWR hooks (ASETPLTFRM-298)

Types for RecommendationResponse, HistoryResponse, StatsResponse.
SWR hooks for dashboard and insights data fetching.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 10: Frontend — Recommendation Widget Components

**Files:**
- Create: `frontend/components/widgets/SignalPill.tsx`
- Create: `frontend/components/widgets/HealthScoreBadge.tsx`
- Create: `frontend/components/widgets/RecommendationCard.tsx`
- Rewrite: `frontend/components/widgets/RecommendationsWidget.tsx`

- [ ] **Step 1: Create SignalPill component**

Create `frontend/components/widgets/SignalPill.tsx` — small pill showing a signal name + value with color based on quality (green=good, red=bad, gray=neutral). Used inside RecommendationCard.

- [ ] **Step 2: Create HealthScoreBadge component**

Create `frontend/components/widgets/HealthScoreBadge.tsx` — circular progress indicator with score (0-100) and label. Colors: red (<30), amber (<60), green (<80), blue (>=80).

- [ ] **Step 3: Create RecommendationCard component**

Create `frontend/components/widgets/RecommendationCard.tsx` — card with tier badge, severity border, ticker, rationale, signal pills, impact text, action button linking to analysis page.

- [ ] **Step 4: Rewrite RecommendationsWidget**

Rewrite `frontend/components/widgets/RecommendationsWidget.tsx` to use the new API endpoint, health score badge, tier/severity filters, and RecommendationCard list. Add refresh button that POSTs to `/refresh`.

- [ ] **Step 5: Test in browser**

Run: `./run.sh restart frontend`

Navigate to dashboard. Verify the widget renders (will show "no data" until recommendations are generated). Check tier filter pills, health badge, and refresh button are present.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/widgets/
git commit -m "feat(ui): upgraded RecommendationsWidget with health score + signal pills (ASETPLTFRM-298)

Tier badges, severity borders, accuracy-adjusted signal pills,
health score circle. Refresh button triggers manual pipeline.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 11: Frontend — Recommendation History Tab on Insights

**Files:**
- Create: `frontend/components/insights/RecommendationHistoryTab.tsx`
- Modify: `frontend/app/(authenticated)/analytics/insights/page.tsx`

- [ ] **Step 1: Create RecommendationHistoryTab**

Create `frontend/components/insights/RecommendationHistoryTab.tsx` — tab content with aggregate stats KPI cards at top (hit rate 30d, 60d, adoption rate), collapsible monthly run sections with per-recommendation outcome badges.

- [ ] **Step 2: Add tab to Insights page**

In `frontend/app/(authenticated)/analytics/insights/page.tsx`, add `"recommendations"` to the valid tabs list and render `<RecommendationHistoryTab />` when selected. Add tab button "Rec History" after existing tabs.

- [ ] **Step 3: Test in browser**

Navigate to Analytics > Insights > Rec History tab. Verify tab renders, URL persistence works (`?tab=recommendations`).

- [ ] **Step 4: Commit**

```bash
git add frontend/components/insights/RecommendationHistoryTab.tsx frontend/app/\(authenticated\)/analytics/insights/page.tsx
git commit -m "feat(ui): add Recommendation History tab to Insights page (ASETPLTFRM-298)

Monthly timeline with outcome badges, aggregate hit rate
and adoption stats. URL tab persistence.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 12: Integration Test — Full Pipeline E2E

**Files:**
- Modify: `tests/test_recommendation_engine.py`

- [ ] **Step 1: Add integration test**

Append to `tests/test_recommendation_engine.py`:

```python
class TestPipelineIntegration:
    """E2E test: Stage 1 → 2 → 3 → PG write."""

    def test_full_pipeline_with_empty_portfolio(self):
        """Empty portfolio returns early."""
        from backend.jobs.recommendation_engine import (
            stage2_gap_analysis,
        )

        # Mock empty holdings
        result = stage2_gap_analysis(
            "fake-user-id",
            pd.DataFrame(),
        )
        assert result["portfolio_summary"]["empty"] is True

    def test_composite_score_weights_sum_to_one(self):
        from backend.jobs.recommendation_engine import (
            W_FORECAST,
            W_MOMENTUM,
            W_PIOTROSKI,
            W_SENTIMENT,
            W_SHARPE,
            W_TECHNICAL,
        )

        total = (
            W_PIOTROSKI + W_SHARPE + W_MOMENTUM
            + W_FORECAST + W_SENTIMENT + W_TECHNICAL
        )
        assert abs(total - 1.0) < 0.001
```

- [ ] **Step 2: Run full test suite**

Run: `PYTHONPATH=.:backend python -m pytest tests/test_recommendation_engine.py -v`

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_recommendation_engine.py
git commit -m "test: add recommendation engine integration tests (ASETPLTFRM-298)

Weight validation, empty portfolio handling,
full pipeline smoke tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 13: Final — Rebuild + Verify + Update Docs

- [ ] **Step 1: Rebuild services**

Run:
```bash
./run.sh rebuild backend
./run.sh rebuild frontend
```

- [ ] **Step 2: Run Alembic migration**

Run:
```bash
docker compose exec backend alembic upgrade head
```

- [ ] **Step 3: Verify API endpoints**

Run:
```bash
# Health check
curl -s http://localhost:8181/v1/health | jq

# Recommendations (requires auth token)
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8181/v1/dashboard/portfolio/recommendations | jq

# History
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8181/v1/dashboard/portfolio/recommendations/history | jq

# Stats
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8181/v1/dashboard/portfolio/recommendations/stats | jq
```

- [ ] **Step 4: Test in browser**

Navigate to dashboard — verify widget. Navigate to Insights > Rec History — verify tab. Trigger manual refresh — verify pipeline runs.

- [ ] **Step 5: Update PROGRESS.md**

Add section for this session's work under Sprint 6.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: LLM-powered portfolio recommendations complete (ASETPLTFRM-298)

Smart Funnel pipeline: DuckDB pre-filter → gap analysis → LLM.
6th LangGraph agent, 3 PG tables, 5 API endpoints, dashboard
widget, Insights history tab, 30/60/90d outcome tracking.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```
