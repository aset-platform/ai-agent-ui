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
