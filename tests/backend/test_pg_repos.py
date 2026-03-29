"""Integration tests for PostgreSQL-backed repositories."""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.db.base import Base
from backend.db.models.user import User


@pytest_asyncio.fixture
async def pg_session():
    """In-memory SQLite async session for repo tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with factory() as session:
        yield session
    await engine.dispose()


# ── user_reads ──


@pytest.mark.asyncio
async def test_get_by_email(pg_session):
    from auth.repo.user_reads import get_by_email

    result = await get_by_email(pg_session, "x@y.com")
    assert result is None

    user = User(
        user_id=str(uuid.uuid4()),
        email="x@y.com",
        hashed_password="h",
        full_name="X",
        role="user",
        is_active=True,
    )
    pg_session.add(user)
    await pg_session.commit()

    result = await get_by_email(pg_session, "x@y.com")
    assert result is not None
    assert result["email"] == "x@y.com"


@pytest.mark.asyncio
async def test_get_by_id(pg_session):
    from auth.repo.user_reads import get_by_id

    uid = str(uuid.uuid4())
    user = User(
        user_id=uid, email="a@b.com", hashed_password="h",
        full_name="A", role="user", is_active=True,
    )
    pg_session.add(user)
    await pg_session.commit()

    result = await get_by_id(pg_session, uid)
    assert result is not None
    assert result["user_id"] == uid

    result = await get_by_id(pg_session, "nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_list_all(pg_session):
    from auth.repo.user_reads import list_all

    for i in range(3):
        pg_session.add(User(
            user_id=str(uuid.uuid4()),
            email=f"u{i}@test.com",
            hashed_password="h",
            full_name=f"User {i}",
            role="user",
            is_active=True,
        ))
    await pg_session.commit()

    result = await list_all(pg_session)
    assert len(result) == 3


# ── user_writes ──


@pytest.mark.asyncio
async def test_create_user(pg_session):
    from auth.repo.user_writes import create

    result = await create(pg_session, {
        "email": "new@test.com",
        "hashed_password": "hashed",
        "full_name": "New User",
        "role": "user",
    })
    assert result["email"] == "new@test.com"
    assert result["user_id"]
    assert result["is_active"] is True


@pytest.mark.asyncio
async def test_create_duplicate_email(pg_session):
    from auth.repo.user_writes import create

    await create(pg_session, {
        "email": "dup@test.com", "hashed_password": "h",
        "full_name": "A", "role": "user",
    })
    with pytest.raises(ValueError, match="already exists"):
        await create(pg_session, {
            "email": "dup@test.com", "hashed_password": "h",
            "full_name": "B", "role": "user",
        })


@pytest.mark.asyncio
async def test_update_user(pg_session):
    from auth.repo.user_writes import create, update

    created = await create(pg_session, {
        "email": "upd@test.com", "hashed_password": "h",
        "full_name": "Old Name", "role": "user",
    })
    updated = await update(
        pg_session, created["user_id"],
        {"full_name": "New Name"},
    )
    assert updated["full_name"] == "New Name"
    assert updated["email"] == "upd@test.com"


@pytest.mark.asyncio
async def test_delete_user_soft(pg_session):
    from auth.repo.user_writes import create, delete
    from auth.repo.user_reads import get_by_id

    created = await create(pg_session, {
        "email": "del@test.com", "hashed_password": "h",
        "full_name": "Gone", "role": "user",
    })
    await delete(pg_session, created["user_id"])
    result = await get_by_id(pg_session, created["user_id"])
    assert result["is_active"] is False


# ── oauth ──


@pytest.mark.asyncio
async def test_get_by_oauth_sub(pg_session):
    from auth.repo.oauth import get_by_oauth_sub
    from auth.repo.user_writes import create

    await create(pg_session, {
        "email": "oauth@test.com", "hashed_password": "h",
        "full_name": "OAuth User", "role": "user",
        "oauth_provider": "google", "oauth_sub": "g123",
    })

    result = await get_by_oauth_sub(
        pg_session, "google", "g123",
    )
    assert result is not None
    assert result["email"] == "oauth@test.com"

    result = await get_by_oauth_sub(
        pg_session, "google", "wrong",
    )
    assert result is None


@pytest.mark.asyncio
async def test_get_or_create_by_oauth_new(pg_session):
    from auth.repo.oauth import get_or_create_by_oauth

    result = await get_or_create_by_oauth(
        pg_session, "google", "new123",
        "new@test.com", "New OAuth",
    )
    assert result["email"] == "new@test.com"
    assert result["oauth_provider"] == "google"
    assert result["oauth_sub"] == "new123"


@pytest.mark.asyncio
async def test_get_or_create_by_oauth_existing_email(pg_session):
    from auth.repo.oauth import get_or_create_by_oauth
    from auth.repo.user_writes import create

    await create(pg_session, {
        "email": "existing@test.com", "hashed_password": "h",
        "full_name": "Existing", "role": "user",
    })

    result = await get_or_create_by_oauth(
        pg_session, "facebook", "fb456",
        "existing@test.com", "Existing",
    )
    assert result["oauth_provider"] == "facebook"
    assert result["oauth_sub"] == "fb456"


from backend.db.models.user_ticker import UserTicker
from backend.db.models.payment import PaymentTransaction


# ── ticker_repo ──


@pytest.mark.asyncio
async def test_link_ticker(pg_session):
    from auth.repo.ticker_repo import (
        link_ticker, get_user_tickers,
    )
    from auth.repo.user_writes import create

    user = await create(pg_session, {
        "email": "t@test.com", "hashed_password": "h",
        "full_name": "T", "role": "user",
    })

    linked = await link_ticker(
        pg_session, user["user_id"], "AAPL",
    )
    assert linked is True

    tickers = await get_user_tickers(
        pg_session, user["user_id"],
    )
    assert "AAPL" in tickers


@pytest.mark.asyncio
async def test_link_ticker_duplicate(pg_session):
    from auth.repo.ticker_repo import link_ticker
    from auth.repo.user_writes import create

    user = await create(pg_session, {
        "email": "t2@test.com", "hashed_password": "h",
        "full_name": "T2", "role": "user",
    })
    await link_ticker(
        pg_session, user["user_id"], "MSFT",
    )
    result = await link_ticker(
        pg_session, user["user_id"], "MSFT",
    )
    assert result is False


@pytest.mark.asyncio
async def test_unlink_ticker(pg_session):
    from auth.repo.ticker_repo import (
        link_ticker, unlink_ticker, get_user_tickers,
    )
    from auth.repo.user_writes import create

    user = await create(pg_session, {
        "email": "t3@test.com", "hashed_password": "h",
        "full_name": "T3", "role": "user",
    })
    await link_ticker(
        pg_session, user["user_id"], "GOOG",
    )
    await unlink_ticker(
        pg_session, user["user_id"], "GOOG",
    )

    tickers = await get_user_tickers(
        pg_session, user["user_id"],
    )
    assert "GOOG" not in tickers


# ── payment_repo ──


@pytest.mark.asyncio
async def test_record_payment(pg_session):
    from auth.repo.payment_repo import (
        record_transaction, get_by_user,
    )
    from auth.repo.user_writes import create

    user = await create(pg_session, {
        "email": "p@test.com", "hashed_password": "h",
        "full_name": "P", "role": "user",
    })

    txn = await record_transaction(pg_session, {
        "user_id": user["user_id"],
        "gateway": "razorpay",
        "event_type": "subscription.charged",
        "status": "success",
        "amount": 499.0,
        "currency": "INR",
        "raw_payload": {"id": "evt_123"},
    })
    assert txn["transaction_id"]
    assert txn["gateway"] == "razorpay"

    txns = await get_by_user(pg_session, user["user_id"])
    assert len(txns) == 1


@pytest.mark.asyncio
async def test_update_payment_status(pg_session):
    from auth.repo.payment_repo import (
        record_transaction, update_status,
    )
    from auth.repo.user_writes import create

    user = await create(pg_session, {
        "email": "p2@test.com", "hashed_password": "h",
        "full_name": "P2", "role": "user",
    })

    txn = await record_transaction(pg_session, {
        "user_id": user["user_id"],
        "gateway": "stripe",
        "event_type": "charge.succeeded",
        "status": "pending",
    })

    updated = await update_status(
        pg_session, txn["transaction_id"], "success",
    )
    assert updated["status"] == "success"


# ── repository facade ──


@pytest.mark.asyncio
async def test_repository_facade(pg_session):
    from auth.repo.repository import UserRepository

    repo = UserRepository(session=pg_session)

    # Create
    user = await repo.create({
        "email": "facade@test.com", "hashed_password": "h",
        "full_name": "Facade", "role": "user",
    })
    assert user["email"] == "facade@test.com"

    # Read
    found = await repo.get_by_email("facade@test.com")
    assert found is not None

    # Update
    updated = await repo.update(
        user["user_id"], {"full_name": "Updated"},
    )
    assert updated["full_name"] == "Updated"

    # Tickers
    await repo.link_ticker(user["user_id"], "AAPL")
    tickers = await repo.get_user_tickers(user["user_id"])
    assert "AAPL" in tickers

    await repo.unlink_ticker(user["user_id"], "AAPL")
    tickers = await repo.get_user_tickers(user["user_id"])
    assert "AAPL" not in tickers


from backend.db.models.registry import StockRegistry
from backend.db.models.scheduler import ScheduledJob


# ── pg_stocks: registry ──


@pytest.mark.asyncio
async def test_registry_upsert(pg_session):
    from backend.db.pg_stocks import (
        upsert_registry, get_registry,
    )

    await upsert_registry(pg_session, {
        "ticker": "AAPL",
        "market": "NASDAQ",
        "total_rows": 100,
    })

    row = await get_registry(pg_session, "AAPL")
    assert row["ticker"] == "AAPL"
    assert row["total_rows"] == 100

    # Upsert again
    await upsert_registry(pg_session, {
        "ticker": "AAPL",
        "total_rows": 200,
    })
    row = await get_registry(pg_session, "AAPL")
    assert row["total_rows"] == 200


@pytest.mark.asyncio
async def test_registry_get_all(pg_session):
    from backend.db.pg_stocks import (
        upsert_registry, get_registry,
    )

    await upsert_registry(pg_session, {
        "ticker": "AAPL", "market": "NASDAQ",
    })
    await upsert_registry(pg_session, {
        "ticker": "MSFT", "market": "NASDAQ",
    })

    df = await get_registry(pg_session)
    assert len(df) == 2


# ── pg_stocks: scheduler ──


@pytest.mark.asyncio
async def test_scheduler_job_crud(pg_session):
    from backend.db.pg_stocks import (
        upsert_scheduled_job,
        get_scheduled_jobs,
        delete_scheduled_job,
    )

    await upsert_scheduled_job(pg_session, {
        "job_id": "j1",
        "name": "daily-fetch",
        "job_type": "fetch",
        "cron_days": "mon,wed,fri",
        "cron_time": "09:00",
        "enabled": True,
    })

    jobs = await get_scheduled_jobs(pg_session)
    assert len(jobs) == 1
    assert jobs[0]["name"] == "daily-fetch"

    # Toggle off
    await upsert_scheduled_job(pg_session, {
        "job_id": "j1",
        "name": "daily-fetch",
        "job_type": "fetch",
        "enabled": False,
    })
    jobs = await get_scheduled_jobs(pg_session)
    assert jobs[0]["enabled"] is False

    # Delete
    await delete_scheduled_job(pg_session, "j1")
    jobs = await get_scheduled_jobs(pg_session)
    assert len(jobs) == 0
