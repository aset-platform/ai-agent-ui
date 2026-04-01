"""Test ORM model definitions and constraints."""
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
from backend.db.models.payment import PaymentTransaction
from backend.db.models.registry import StockRegistry
from backend.db.models.scheduler import ScheduledJob
from backend.db.models.user import User
from backend.db.models.user_ticker import UserTicker


@pytest_asyncio.fixture
async def pg_session():
    """In-memory SQLite async session for model tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
    )
    async with engine.begin() as conn:
        _tables = [
            t for t in Base.metadata.sorted_tables
            if t.name != "user_memories"
        ]
        await conn.run_sync(
            Base.metadata.create_all,
            tables=_tables,
        )

    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_user_create_and_read(pg_session):
    user = User(
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        hashed_password="hashed",
        full_name="Test User",
        role="user",
        is_active=True,
    )
    pg_session.add(user)
    await pg_session.commit()

    result = await pg_session.execute(
        select(User).where(User.email == "test@example.com")
    )
    row = result.scalar_one()
    assert row.full_name == "Test User"
    assert row.is_active is True


@pytest.mark.asyncio
async def test_user_email_unique(pg_session):
    from sqlalchemy.exc import IntegrityError

    u1 = User(
        user_id=str(uuid.uuid4()),
        email="dup@example.com",
        hashed_password="h",
        full_name="A",
        role="user",
        is_active=True,
    )
    pg_session.add(u1)
    await pg_session.commit()

    u2 = User(
        user_id=str(uuid.uuid4()),
        email="dup@example.com",
        hashed_password="h",
        full_name="B",
        role="user",
        is_active=True,
    )
    pg_session.add(u2)
    with pytest.raises(IntegrityError):
        await pg_session.commit()


@pytest.mark.asyncio
async def test_user_ticker_composite_pk(pg_session):
    user = User(
        user_id="u1", email="a@b.com", hashed_password="h",
        full_name="A", role="user", is_active=True,
    )
    pg_session.add(user)
    await pg_session.commit()

    t1 = UserTicker(user_id="u1", ticker="AAPL", source="manual")
    pg_session.add(t1)
    await pg_session.commit()

    from sqlalchemy.exc import IntegrityError

    t2 = UserTicker(user_id="u1", ticker="AAPL", source="manual")
    pg_session.add(t2)
    with pytest.raises(IntegrityError):
        await pg_session.commit()


@pytest.mark.asyncio
async def test_user_ticker_cascade_delete(pg_session):
    user = User(
        user_id="u2", email="c@d.com", hashed_password="h",
        full_name="B", role="user", is_active=True,
    )
    t = UserTicker(user_id="u2", ticker="MSFT", source="auto")
    pg_session.add_all([user, t])
    await pg_session.commit()

    await pg_session.delete(user)
    await pg_session.commit()

    result = await pg_session.execute(
        select(UserTicker).where(UserTicker.user_id == "u2")
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_payment_transaction_json(pg_session):
    user = User(
        user_id="u3", email="e@f.com", hashed_password="h",
        full_name="C", role="user", is_active=True,
    )
    pg_session.add(user)
    await pg_session.commit()

    txn = PaymentTransaction(
        transaction_id="txn1",
        user_id="u3",
        gateway="razorpay",
        event_type="subscription.charged",
        status="success",
        amount=499.0,
        currency="INR",
        raw_payload={"key": "value"},
    )
    pg_session.add(txn)
    await pg_session.commit()

    result = await pg_session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.transaction_id == "txn1"
        )
    )
    row = result.scalar_one()
    assert row.gateway == "razorpay"
    assert row.amount == 499.0


@pytest.mark.asyncio
async def test_registry_natural_pk(pg_session):
    reg = StockRegistry(
        ticker="AAPL",
        market="NASDAQ",
        total_rows=100,
    )
    pg_session.add(reg)
    await pg_session.commit()

    result = await pg_session.execute(
        select(StockRegistry).where(
            StockRegistry.ticker == "AAPL"
        )
    )
    row = result.scalar_one()
    assert row.market == "NASDAQ"
    assert row.total_rows == 100


@pytest.mark.asyncio
async def test_scheduled_job_unique_name(pg_session):
    from sqlalchemy.exc import IntegrityError

    j1 = ScheduledJob(
        job_id="j1", name="daily-fetch",
        job_type="fetch", enabled=True,
    )
    pg_session.add(j1)
    await pg_session.commit()

    j2 = ScheduledJob(
        job_id="j2", name="daily-fetch",
        job_type="fetch", enabled=True,
    )
    pg_session.add(j2)
    with pytest.raises(IntegrityError):
        await pg_session.commit()
