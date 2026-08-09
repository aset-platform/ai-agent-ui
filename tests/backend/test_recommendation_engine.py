"""Tests for recommendation PG CRUD functions."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.db.base import Base


# ── Fixtures ───────────────────────────────────────────


@pytest_asyncio.fixture
async def pg_session():
    """In-memory SQLite session for recommendation tests.

    Excludes user_memories (pgvector, not available in SQLite);
    creates all other tables including recommendation_*.
    schema_translate_map strips "stocks." qualification for SQLite
    (no cross-schema concept) while leaving real Postgres DDL/DML
    untouched in production.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={
            "schema_translate_map": {"stocks": None},
        },
    )
    async with engine.begin() as conn:
        # pipelines/pipeline_steps/conversation_contexts use raw
        # Postgres-only JSONB/ARRAY columns and aren't under test
        # in this file — exclude rather than widen the blast
        # radius of type changes to untested models.
        _skip = {
            "user_memories", "pipelines", "pipeline_steps",
            "conversation_contexts",
        }
        _tables = [
            t for t in Base.metadata.sorted_tables
            if t.name not in _skip
        ]
        await conn.run_sync(
            Base.metadata.create_all,
            tables=_tables,
        )
    factory = async_sessionmaker(
        engine, class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield session
    await engine.dispose()


def _make_run_data(
    user_id: str | None = None,
    run_id: str | None = None,
    run_date: date | None = None,
) -> dict:
    """Build a valid recommendation run dict."""
    return {
        "run_id": run_id or str(uuid.uuid4()),
        "user_id": user_id or str(uuid.uuid4()),
        "run_date": run_date or date.today(),
        "run_type": "full",
        "portfolio_snapshot": {"holdings": []},
        "health_score": 72.5,
        "health_label": "good",
        "health_assessment": "Portfolio is healthy.",
        "candidates_scanned": 500,
        "candidates_passed": 12,
        "llm_model": "gpt-oss-20b",
        "llm_tokens_used": 4200,
        "duration_secs": 8.3,
    }


def _make_rec_data(
    rec_id: str | None = None,
    ticker: str = "RELIANCE.NS",
    action: str = "buy",
    status: str = "active",
) -> dict:
    """Build a valid recommendation dict."""
    return {
        "id": rec_id or str(uuid.uuid4()),
        "tier": "high_conviction",
        "category": "value",
        "ticker": ticker,
        "action": action,
        "severity": "medium",
        "rationale": "Strong fundamentals.",
        "expected_impact": "+15% in 6 months",
        "data_signals": {"piotroski": 8, "pe": 12.5},
        "price_at_rec": 2450.0,
        "target_price": 2800.0,
        "expected_return_pct": 14.3,
        "index_tags": None,  # ARRAY not supported in SQLite
        "status": status,
    }


# ── insert_recommendation_run + get_latest ─────────────


@pytest.mark.asyncio
async def test_insert_and_get_run(pg_session):
    from backend.db.pg_stocks import (
        get_latest_recommendation_run,
        insert_recommendation_run,
    )

    user_id = str(uuid.uuid4())
    data = _make_run_data(user_id=user_id)
    run_id = await insert_recommendation_run(
        pg_session, data,
    )
    assert run_id == data["run_id"]

    latest = await get_latest_recommendation_run(
        pg_session, user_id,
    )
    assert latest is not None
    assert latest["run_id"] == run_id
    assert latest["health_score"] == 72.5
    assert latest["candidates_scanned"] == 500


@pytest.mark.asyncio
async def test_get_latest_returns_newest(pg_session):
    from backend.db.pg_stocks import (
        get_latest_recommendation_run,
        insert_recommendation_run,
    )

    user_id = str(uuid.uuid4())
    old = _make_run_data(
        user_id=user_id,
        run_date=date.today() - timedelta(days=7),
    )
    new = _make_run_data(
        user_id=user_id,
        run_date=date.today(),
    )
    await insert_recommendation_run(pg_session, old)
    await insert_recommendation_run(pg_session, new)

    latest = await get_latest_recommendation_run(
        pg_session, user_id,
    )
    assert latest["run_id"] == new["run_id"]


@pytest.mark.asyncio
async def test_get_latest_returns_none(pg_session):
    from backend.db.pg_stocks import (
        get_latest_recommendation_run,
    )

    result = await get_latest_recommendation_run(
        pg_session, str(uuid.uuid4()),
    )
    assert result is None


# ── insert_recommendations + get_for_run ───────────────


@pytest.mark.asyncio
async def test_insert_recommendations(pg_session):
    from backend.db.pg_stocks import (
        get_recommendations_for_run,
        insert_recommendation_run,
        insert_recommendations,
    )

    user_id = str(uuid.uuid4())
    data = _make_run_data(user_id=user_id)
    run_id = await insert_recommendation_run(
        pg_session, data,
    )

    recs = [
        _make_rec_data(ticker="RELIANCE.NS", action="buy"),
        _make_rec_data(ticker="TCS.NS", action="hold"),
        _make_rec_data(ticker="INFY.NS", action="sell"),
    ]
    count = await insert_recommendations(
        pg_session, run_id, recs,
    )
    assert count == 3

    fetched = await get_recommendations_for_run(
        pg_session, run_id,
    )
    assert len(fetched) == 3
    tickers = {r["ticker"] for r in fetched}
    assert tickers == {"RELIANCE.NS", "TCS.NS", "INFY.NS"}


# ── update_recommendation_status ───────────────────────


@pytest.mark.asyncio
async def test_update_recommendation_status(pg_session):
    from backend.db.pg_stocks import (
        get_recommendations_for_run,
        insert_recommendation_run,
        insert_recommendations,
        update_recommendation_status,
    )

    user_id = str(uuid.uuid4())
    data = _make_run_data(user_id=user_id)
    run_id = await insert_recommendation_run(
        pg_session, data,
    )

    recs = [
        _make_rec_data(
            ticker="RELIANCE.NS", action="buy",
        ),
        _make_rec_data(
            ticker="TCS.NS", action="sell",
        ),
    ]
    await insert_recommendations(pg_session, run_id, recs)

    updated = await update_recommendation_status(
        pg_session,
        user_id=user_id,
        ticker="RELIANCE.NS",
        actions=["buy"],
        new_status="acted",
    )
    assert updated == 1

    # TCS should still be active
    fetched = await get_recommendations_for_run(
        pg_session, run_id,
    )
    by_ticker = {r["ticker"]: r for r in fetched}
    assert by_ticker["RELIANCE.NS"]["status"] == "acted"
    assert by_ticker["TCS.NS"]["status"] == "active"


@pytest.mark.asyncio
async def test_update_status_no_match(pg_session):
    from backend.db.pg_stocks import (
        update_recommendation_status,
    )

    updated = await update_recommendation_status(
        pg_session,
        user_id=str(uuid.uuid4()),
        ticker="AAPL",
        actions=["buy"],
        new_status="acted",
    )
    assert updated == 0


# ── expire_old_recommendations ─────────────────────────


@pytest.mark.asyncio
async def test_expire_old_recommendations(pg_session):
    from backend.db.pg_stocks import (
        expire_old_recommendations,
        get_recommendations_for_run,
        insert_recommendation_run,
        insert_recommendations,
    )

    user_id = str(uuid.uuid4())

    # Old run
    old_data = _make_run_data(
        user_id=user_id,
        run_date=date.today() - timedelta(days=7),
    )
    old_run_id = await insert_recommendation_run(
        pg_session, old_data,
    )
    await insert_recommendations(
        pg_session,
        old_run_id,
        [_make_rec_data(ticker="OLD.NS")],
    )

    # New run
    new_data = _make_run_data(
        user_id=user_id,
        run_date=date.today(),
    )
    new_run_id = await insert_recommendation_run(
        pg_session, new_data,
    )
    await insert_recommendations(
        pg_session,
        new_run_id,
        [_make_rec_data(ticker="NEW.NS")],
    )

    expired = await expire_old_recommendations(
        pg_session, user_id, new_run_id,
    )
    assert expired == 1

    old_recs = await get_recommendations_for_run(
        pg_session, old_run_id,
    )
    assert old_recs[0]["status"] == "expired"

    new_recs = await get_recommendations_for_run(
        pg_session, new_run_id,
    )
    assert new_recs[0]["status"] == "active"


# ── expire_stale_recommendations ───────────────────────


@pytest.mark.asyncio
async def test_expire_stale_recommendations(pg_session):
    from backend.db.pg_stocks import (
        expire_stale_recommendations,
        insert_recommendation_run,
        insert_recommendations,
    )
    from backend.db.models.recommendation import (
        Recommendation,
    )
    from sqlalchemy import select as sa_select

    user_id = str(uuid.uuid4())
    data = _make_run_data(user_id=user_id)
    run_id = await insert_recommendation_run(
        pg_session, data,
    )

    rec = _make_rec_data(ticker="STALE.NS")
    await insert_recommendations(
        pg_session, run_id, [rec],
    )

    # Manually backdate created_at to 100 days ago
    result = await pg_session.execute(
        sa_select(Recommendation).where(
            Recommendation.id == rec["id"],
        )
    )
    row = result.scalar_one()
    row.created_at = datetime.now(timezone.utc) - timedelta(
        days=100,
    )
    await pg_session.commit()

    expired = await expire_stale_recommendations(
        pg_session, date.today(),
    )
    assert expired == 1


# ── get_recommendation_history ─────────────────────────


@pytest.mark.asyncio
async def test_get_recommendation_history(pg_session):
    from backend.db.pg_stocks import (
        get_recommendation_history,
        insert_recommendation_run,
        insert_recommendations,
    )

    user_id = str(uuid.uuid4())
    data = _make_run_data(user_id=user_id)
    run_id = await insert_recommendation_run(
        pg_session, data,
    )
    await insert_recommendations(
        pg_session,
        run_id,
        [_make_rec_data(), _make_rec_data()],
    )

    history = await get_recommendation_history(
        pg_session, user_id,
    )
    assert len(history) == 1
    assert history[0]["rec_count"] == 2


# ── get_recommendation_stats ──────────────────────────


@pytest.mark.asyncio
async def test_get_recommendation_stats_empty(pg_session):
    from backend.db.pg_stocks import (
        get_recommendation_stats,
    )

    stats = await get_recommendation_stats(
        pg_session, str(uuid.uuid4()),
    )
    assert stats["total_runs"] == 0
    assert stats["total_recs"] == 0
    assert stats["hit_rate_pct"] == 0.0


# ── insert_recommendation_outcome ─────────────────────


@pytest.mark.asyncio
async def test_insert_outcome(pg_session):
    from backend.db.pg_stocks import (
        insert_recommendation_outcome,
        insert_recommendation_run,
        insert_recommendations,
    )
    from backend.db.models.recommendation import (
        RecommendationOutcome,
    )
    from sqlalchemy import select as sa_select

    user_id = str(uuid.uuid4())
    data = _make_run_data(user_id=user_id)
    run_id = await insert_recommendation_run(
        pg_session, data,
    )
    rec = _make_rec_data()
    await insert_recommendations(
        pg_session, run_id, [rec],
    )

    await insert_recommendation_outcome(
        pg_session,
        rec_id=rec["id"],
        check_date=date.today(),
        days=30,
        price=2600.0,
        ret=6.1,
        bench=3.2,
        excess=2.9,
        label="positive",
    )

    result = await pg_session.execute(
        sa_select(RecommendationOutcome).where(
            RecommendationOutcome.recommendation_id
            == rec["id"],
        )
    )
    outcome = result.scalar_one()
    assert outcome.days_elapsed == 30
    assert outcome.actual_price == 2600.0
    assert outcome.outcome_label == "positive"
