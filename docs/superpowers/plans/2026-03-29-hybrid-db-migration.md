# Hybrid DB Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate 5 CRUD tables from Iceberg to PostgreSQL (big
bang cut-over), keep 14 append-only/scoped-delete tables on
Iceberg, add DuckDB query layer.

**Architecture:** SQLAlchemy 2.0 async ORM with Alembic migrations
on the existing Docker PostgreSQL 16 service. Single cut-over —
no feature flags. Iceberg tables retained until cleanup story.

**Tech Stack:** SQLAlchemy 2.0.48 (already installed), asyncpg,
Alembic, PostgreSQL 16 (Docker), DuckDB, existing PyIceberg for
remaining tables.

**Spec:** `docs/superpowers/specs/2026-03-29-hybrid-db-migration-design.md`

---

## File Map

### New files

```
backend/db/__init__.py              # exports engine, async_session, Base
backend/db/engine.py                # async engine + session factory
backend/db/base.py                  # declarative Base
backend/db/models/__init__.py       # exports all models
backend/db/models/user.py           # User ORM model
backend/db/models/user_ticker.py    # UserTicker ORM model
backend/db/models/payment.py        # PaymentTransaction ORM model
backend/db/models/registry.py       # StockRegistry ORM model
backend/db/models/scheduler.py      # ScheduledJob ORM model
alembic.ini                         # Alembic config (project root)
backend/db/migrations/env.py        # async Alembic env
backend/db/migrations/versions/     # migration scripts (auto-generated)
scripts/migrate_iceberg_to_pg.py    # one-time data migration
auth/repo/ticker_repo.py            # PostgreSQL ticker link/unlink
auth/repo/payment_repo.py           # PostgreSQL payment transactions
tests/backend/test_pg_models.py     # ORM constraint tests
tests/backend/test_pg_repos.py      # repository integration tests
tests/backend/test_migration.py     # data migration script tests
```

### Modified files

```
backend/requirements.txt            # add asyncpg, alembic, duckdb
backend/config.py                   # no changes needed (database_url exists)
backend/main.py                     # wire PG engine at startup
backend/bootstrap.py                # inject async_session into repos
auth/repo/repository.py             # rewrite internals to SQLAlchemy
auth/repo/user_reads.py             # rewrite to select() queries
auth/repo/user_writes.py            # rewrite to session.add/merge
auth/repo/oauth.py                  # rewrite to indexed query
stocks/repository.py                # rewrite registry + scheduler methods
backend/jobs/scheduler_service.py   # read/write from PostgreSQL
tests/backend/conftest.py           # add pg_session fixture
```

### Deleted files (in cleanup story ASETPLTFRM-236)

```
auth/repo/catalog.py                # Iceberg catalog singleton
auth/repo/schemas.py                # PyArrow schemas (replaced by ORM)
```

---

## Task 1: Add dependencies (ASETPLTFRM-231)

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add asyncpg, alembic, duckdb to requirements.txt**

Add after the existing `SQLAlchemy==2.0.48` line (currently
line 149):

```
asyncpg==0.30.0
alembic==1.15.2
duckdb==1.2.2
```

- [ ] **Step 2: Install dependencies**

Run:
```bash
source ~/.ai-agent-ui/venv/bin/activate
pip install asyncpg==0.30.0 alembic==1.15.2 duckdb==1.2.2
```

Expected: Successfully installed asyncpg alembic duckdb

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore: add asyncpg, alembic, duckdb dependencies

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 2: Create async engine + session factory (ASETPLTFRM-231)

**Files:**
- Create: `backend/db/__init__.py`
- Create: `backend/db/base.py`
- Create: `backend/db/engine.py`

- [ ] **Step 1: Write failing test for engine creation**

Create `tests/backend/test_pg_engine.py`:

```python
"""Test PostgreSQL async engine creation."""
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_engine_creates_from_database_url():
    """Engine uses DATABASE_URL from settings."""
    with patch("backend.db.engine.get_settings") as mock:
        mock.return_value.database_url = (
            "postgresql+asyncpg://app:test@localhost:5432/testdb"
        )
        from backend.db.engine import get_engine

        engine = get_engine()
        assert str(engine.url) == (
            "postgresql+asyncpg://app:***@localhost:5432/testdb"
        )
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_factory_returns_async_session():
    """Session factory produces AsyncSession instances."""
    with patch("backend.db.engine.get_settings") as mock:
        mock.return_value.database_url = (
            "postgresql+asyncpg://app:test@localhost:5432/testdb"
        )
        from backend.db.engine import get_session_factory

        factory = get_session_factory()
        assert factory is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_pg_engine.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Create backend/db/base.py**

```python
"""Declarative base for all PostgreSQL ORM models."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [ ] **Step 4: Create backend/db/engine.py**

```python
"""Async SQLAlchemy engine and session factory."""
import logging
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import get_settings

log = logging.getLogger(__name__)


@lru_cache
def get_engine():
    """Create async engine from DATABASE_URL (cached)."""
    url = get_settings().database_url
    log.info("Creating async engine: %s", url.split("@")[-1])
    return create_async_engine(
        url,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return cached async session factory."""
    return async_sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
```

- [ ] **Step 5: Create backend/db/__init__.py**

```python
"""PostgreSQL async ORM package."""
from backend.db.base import Base
from backend.db.engine import get_engine, get_session_factory

__all__ = ["Base", "get_engine", "get_session_factory"]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/backend/test_pg_engine.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/db/ tests/backend/test_pg_engine.py
git commit -m "feat: async SQLAlchemy engine + session factory

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 3: ORM models — User (ASETPLTFRM-232)

**Files:**
- Create: `backend/db/models/__init__.py`
- Create: `backend/db/models/user.py`
- Test: `tests/backend/test_pg_models.py`

- [ ] **Step 1: Write failing test for User model**

Create `tests/backend/test_pg_models.py`:

```python
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
from backend.db.models.user import User


@pytest_asyncio.fixture
async def pg_session():
    """In-memory SQLite async session for model tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_user_create_and_read(pg_session):
    """Create a user and read it back."""
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
    """Duplicate email raises IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    uid = str(uuid.uuid4())
    u1 = User(
        user_id=uid,
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_pg_models.py -v`
Expected: FAIL with ImportError (User not defined)

- [ ] **Step 3: Install aiosqlite for test fixture**

Run:
```bash
pip install aiosqlite pytest-asyncio
```

- [ ] **Step 4: Create backend/db/models/user.py**

```python
"""User ORM model — maps to auth.users Iceberg table."""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False,
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    full_name: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="user",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Password reset
    password_reset_token: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    password_reset_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # OAuth
    oauth_provider: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    oauth_sub: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    profile_picture_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    # Permissions
    page_permissions: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )

    # Subscription
    subscription_tier: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    subscription_status: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    razorpay_customer_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    razorpay_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    monthly_usage_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    usage_month: Mapped[str | None] = mapped_column(
        String(7), nullable=True,
    )
    subscription_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    subscription_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Relationships (added in later tasks)
    tickers = relationship(
        "UserTicker", back_populates="user",
        cascade="all, delete-orphan",
    )
    payment_transactions = relationship(
        "PaymentTransaction", back_populates="user",
    )

    __table_args__ = (
        Index(
            "ix_user_oauth",
            "oauth_provider", "oauth_sub",
        ),
        Index("ix_user_tier", "subscription_tier"),
    )
```

- [ ] **Step 5: Create backend/db/models/__init__.py**

```python
"""ORM models package."""
from backend.db.models.user import User

__all__ = ["User"]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/backend/test_pg_models.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/db/models/ tests/backend/test_pg_models.py
git commit -m "feat: User ORM model with constraints + indexes

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 4: ORM models — UserTicker, PaymentTransaction (ASETPLTFRM-233)

**Files:**
- Create: `backend/db/models/user_ticker.py`
- Create: `backend/db/models/payment.py`
- Modify: `backend/db/models/__init__.py`
- Test: `tests/backend/test_pg_models.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/backend/test_pg_models.py`:

```python
from backend.db.models.user_ticker import UserTicker
from backend.db.models.payment import PaymentTransaction


@pytest.mark.asyncio
async def test_user_ticker_composite_pk(pg_session):
    """UserTicker uses (user_id, ticker) composite PK."""
    user = User(
        user_id="u1", email="a@b.com", hashed_password="h",
        full_name="A", role="user", is_active=True,
    )
    pg_session.add(user)
    await pg_session.commit()

    t1 = UserTicker(user_id="u1", ticker="AAPL", source="manual")
    pg_session.add(t1)
    await pg_session.commit()

    # Duplicate should fail
    from sqlalchemy.exc import IntegrityError

    t2 = UserTicker(user_id="u1", ticker="AAPL", source="manual")
    pg_session.add(t2)
    with pytest.raises(IntegrityError):
        await pg_session.commit()


@pytest.mark.asyncio
async def test_user_ticker_cascade_delete(pg_session):
    """Deleting user cascades to tickers."""
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
async def test_payment_transaction_jsonb(pg_session):
    """PaymentTransaction stores raw_payload as JSON."""
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
```

- [ ] **Step 2: Run test to verify new tests fail**

Run: `python -m pytest tests/backend/test_pg_models.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Create backend/db/models/user_ticker.py**

```python
"""UserTicker ORM model — user watchlist link/unlink."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class UserTicker(Base):
    __tablename__ = "user_tickers"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ticker: Mapped[str] = mapped_column(
        String(20), primary_key=True,
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="manual",
    )

    user = relationship("User", back_populates="tickers")
```

- [ ] **Step 4: Create backend/db/models/payment.py**

```python
"""PaymentTransaction ORM model — webhook events + reconciliation."""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from backend.db.base import Base


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    transaction_id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.user_id"),
        nullable=False,
    )
    gateway: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False,
    )
    gateway_event_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    customer_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    amount: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    currency: Mapped[str | None] = mapped_column(
        String(10), nullable=True,
    )
    tier_before: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    tier_after: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False,
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user = relationship("User", back_populates="payment_transactions")

    __table_args__ = (
        Index(
            "ix_payment_gateway_event",
            "gateway", "gateway_event_id",
        ),
    )
```

- [ ] **Step 5: Update backend/db/models/__init__.py**

```python
"""ORM models package."""
from backend.db.models.payment import PaymentTransaction
from backend.db.models.user import User
from backend.db.models.user_ticker import UserTicker

__all__ = ["PaymentTransaction", "User", "UserTicker"]
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/backend/test_pg_models.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/db/models/ tests/backend/test_pg_models.py
git commit -m "feat: UserTicker + PaymentTransaction ORM models

Composite PK on user_tickers, JSONB raw_payload, FK cascade.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 5: ORM models — StockRegistry, ScheduledJob (ASETPLTFRM-234)

**Files:**
- Create: `backend/db/models/registry.py`
- Create: `backend/db/models/scheduler.py`
- Modify: `backend/db/models/__init__.py`
- Test: `tests/backend/test_pg_models.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/backend/test_pg_models.py`:

```python
from backend.db.models.registry import StockRegistry
from backend.db.models.scheduler import ScheduledJob


@pytest.mark.asyncio
async def test_registry_upsert(pg_session):
    """StockRegistry uses ticker as natural PK."""
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
    """ScheduledJob name must be unique."""
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
```

- [ ] **Step 2: Run test to verify new tests fail**

Run: `python -m pytest tests/backend/test_pg_models.py -v -k "registry or scheduled"`
Expected: FAIL with ImportError

- [ ] **Step 3: Create backend/db/models/registry.py**

```python
"""StockRegistry ORM model — ticker fetch metadata."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class StockRegistry(Base):
    __tablename__ = "stock_registry"

    ticker: Mapped[str] = mapped_column(
        String(20), primary_key=True,
    )
    last_fetch_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True,
    )
    total_rows: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    date_range_start: Mapped[date | None] = mapped_column(
        Date, nullable=True,
    )
    date_range_end: Mapped[date | None] = mapped_column(
        Date, nullable=True,
    )
    market: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
```

- [ ] **Step 4: Create backend/db/models/scheduler.py**

```python
"""ScheduledJob ORM model — cron job definitions."""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from backend.db.base import Base


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    job_id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
    )
    name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False,
    )
    job_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
    )
    cron_days: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    cron_time: Mapped[str | None] = mapped_column(
        String(10), nullable=True,
    )
    cron_dates: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    scope: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
```

- [ ] **Step 5: Update backend/db/models/__init__.py**

```python
"""ORM models package."""
from backend.db.models.payment import PaymentTransaction
from backend.db.models.registry import StockRegistry
from backend.db.models.scheduler import ScheduledJob
from backend.db.models.user import User
from backend.db.models.user_ticker import UserTicker

__all__ = [
    "PaymentTransaction",
    "ScheduledJob",
    "StockRegistry",
    "User",
    "UserTicker",
]
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/backend/test_pg_models.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/db/models/ tests/backend/test_pg_models.py
git commit -m "feat: StockRegistry + ScheduledJob ORM models

Natural PK on ticker, unique job name, server-default timestamps.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 6: Alembic setup + initial migration (ASETPLTFRM-231)

**Files:**
- Create: `alembic.ini`
- Create: `backend/db/migrations/env.py`
- Create: `backend/db/migrations/script.py.mako`
- Auto-generate: `backend/db/migrations/versions/001_*.py`

- [ ] **Step 1: Initialize Alembic**

Run:
```bash
cd /Users/abhay/Documents/projects/ai-agent-ui
alembic init backend/db/migrations
```

Expected: Creates `alembic.ini` and `backend/db/migrations/`

- [ ] **Step 2: Configure alembic.ini**

Edit `alembic.ini` — set `script_location` and `sqlalchemy.url`:

```ini
[alembic]
script_location = backend/db/migrations
sqlalchemy.url = postgresql+asyncpg://app:devpass123@localhost:5432/aiagent
```

- [ ] **Step 3: Rewrite backend/db/migrations/env.py for async**

```python
"""Alembic async migration environment."""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from backend.db.base import Base
from backend.db.models import (  # noqa: F401 — register models
    PaymentTransaction,
    ScheduledJob,
    StockRegistry,
    User,
    UserTicker,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in offline mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Run migrations with connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode."""
    connectable = create_async_engine(
        config.get_main_option("sqlalchemy.url"),
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Auto-generate initial migration**

Run:
```bash
docker compose up -d postgres
sleep 3
alembic revision --autogenerate -m "initial schema — 5 tables"
```

Expected: Creates `backend/db/migrations/versions/001_initial_schema_5_tables.py`

- [ ] **Step 5: Run migration**

Run:
```bash
alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade -> 001, initial schema — 5 tables`

- [ ] **Step 6: Verify tables exist**

Run:
```bash
docker compose exec postgres psql -U app -d aiagent -c "\dt"
```

Expected: Lists `users`, `user_tickers`, `payment_transactions`,
`stock_registry`, `scheduled_jobs`, `alembic_version`

- [ ] **Step 7: Commit**

```bash
git add alembic.ini backend/db/migrations/
git commit -m "feat: Alembic async setup + initial 5-table migration

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 7: Auth repo rewrite — user reads (ASETPLTFRM-232)

**Files:**
- Modify: `auth/repo/user_reads.py`
- Test: `tests/backend/test_pg_repos.py`

- [ ] **Step 1: Write failing test for PG user reads**

Create `tests/backend/test_pg_repos.py`:

```python
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


@pytest.mark.asyncio
async def test_get_by_email(pg_session):
    """get_by_email returns user dict or None."""
    from auth.repo.user_reads import get_by_email

    # No user yet
    result = await get_by_email(pg_session, "x@y.com")
    assert result is None

    # Create user
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
    assert result["full_name"] == "X"


@pytest.mark.asyncio
async def test_get_by_id(pg_session):
    """get_by_id returns user dict or None."""
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
    """list_all returns all users as list of dicts."""
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_pg_repos.py -v`
Expected: FAIL (signature mismatch — old functions take `cat`)

- [ ] **Step 3: Rewrite auth/repo/user_reads.py**

Replace the entire file contents:

```python
"""User read operations — PostgreSQL via SQLAlchemy."""
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.user import User

log = logging.getLogger(__name__)


def _user_to_dict(user: User) -> dict[str, Any]:
    """Convert User ORM instance to dict."""
    return {
        c.name: getattr(user, c.name)
        for c in user.__table__.columns
    }


async def get_by_email(
    session: AsyncSession, email: str,
) -> dict[str, Any] | None:
    """Return user dict by email, or None."""
    result = await session.execute(
        select(User).where(User.email == email)
    )
    user = result.scalar_one_or_none()
    return _user_to_dict(user) if user else None


async def get_by_id(
    session: AsyncSession, user_id: str,
) -> dict[str, Any] | None:
    """Return user dict by user_id, or None."""
    result = await session.execute(
        select(User).where(User.user_id == user_id)
    )
    user = result.scalar_one_or_none()
    return _user_to_dict(user) if user else None


async def list_all(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Return all users as list of dicts."""
    result = await session.execute(select(User))
    return [_user_to_dict(u) for u in result.scalars().all()]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/backend/test_pg_repos.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add auth/repo/user_reads.py tests/backend/test_pg_repos.py
git commit -m "feat: rewrite user_reads to async SQLAlchemy

Replaces Iceberg full-table scan with indexed select() queries.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 8: Auth repo rewrite — user writes (ASETPLTFRM-232)

**Files:**
- Modify: `auth/repo/user_writes.py`
- Test: `tests/backend/test_pg_repos.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/backend/test_pg_repos.py`:

```python
@pytest.mark.asyncio
async def test_create_user(pg_session):
    """create() inserts a new user and returns dict."""
    from auth.repo.user_writes import create

    user_data = {
        "email": "new@test.com",
        "hashed_password": "hashed",
        "full_name": "New User",
        "role": "user",
    }
    result = await create(pg_session, user_data)
    assert result["email"] == "new@test.com"
    assert result["user_id"]  # auto-generated
    assert result["is_active"] is True


@pytest.mark.asyncio
async def test_create_duplicate_email(pg_session):
    """create() with duplicate email raises ValueError."""
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
    """update() modifies fields and returns updated dict."""
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
    """delete() sets is_active=False (soft delete)."""
    from auth.repo.user_writes import create, delete
    from auth.repo.user_reads import get_by_id

    created = await create(pg_session, {
        "email": "del@test.com", "hashed_password": "h",
        "full_name": "Gone", "role": "user",
    })
    await delete(pg_session, created["user_id"])
    result = await get_by_id(pg_session, created["user_id"])
    assert result["is_active"] is False
```

- [ ] **Step 2: Run test to verify new tests fail**

Run: `python -m pytest tests/backend/test_pg_repos.py -v -k "create or update or delete"`
Expected: FAIL

- [ ] **Step 3: Rewrite auth/repo/user_writes.py**

Replace the entire file contents:

```python
"""User write operations — PostgreSQL via SQLAlchemy."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.user import User

log = logging.getLogger(__name__)

_IMMUTABLE_FIELDS = {"user_id", "created_at"}


async def create(
    session: AsyncSession,
    user_data: dict[str, Any],
) -> dict[str, Any]:
    """Create a new user. Raises ValueError on duplicate email."""
    email = user_data["email"]

    # Check duplicate
    existing = await session.execute(
        select(User).where(User.email == email)
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"User with email {email} already exists")

    now = datetime.now(timezone.utc)
    user = User(
        user_id=user_data.get("user_id", str(uuid.uuid4())),
        email=email,
        hashed_password=user_data["hashed_password"],
        full_name=user_data["full_name"],
        role=user_data.get("role", "user"),
        is_active=user_data.get("is_active", True),
        created_at=now,
        updated_at=now,
    )

    # Set optional fields
    for field in (
        "last_login_at", "password_reset_token",
        "password_reset_expiry", "oauth_provider", "oauth_sub",
        "profile_picture_url", "page_permissions",
        "subscription_tier", "subscription_status",
        "razorpay_customer_id", "razorpay_subscription_id",
        "stripe_customer_id", "stripe_subscription_id",
        "monthly_usage_count", "usage_month",
        "subscription_start_at", "subscription_end_at",
    ):
        if field in user_data:
            setattr(user, field, user_data[field])

    session.add(user)
    await session.commit()
    await session.refresh(user)

    log.info("Created user %s (%s)", user.user_id, email)
    return {
        c.name: getattr(user, c.name)
        for c in user.__table__.columns
    }


async def update(
    session: AsyncSession,
    user_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Update user fields. Raises ValueError if not found."""
    result = await session.execute(
        select(User).where(User.user_id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise ValueError(f"User {user_id} not found")

    for key, value in updates.items():
        if key in _IMMUTABLE_FIELDS:
            continue
        if hasattr(user, key):
            setattr(user, key, value)

    user.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user)

    log.info("Updated user %s", user_id)
    return {
        c.name: getattr(user, c.name)
        for c in user.__table__.columns
    }


async def delete(
    session: AsyncSession,
    user_id: str,
) -> None:
    """Soft-delete user (set is_active=False)."""
    result = await session.execute(
        select(User).where(User.user_id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise ValueError(f"User {user_id} not found")

    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)
    await session.commit()
    log.info("Soft-deleted user %s", user_id)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/backend/test_pg_repos.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add auth/repo/user_writes.py tests/backend/test_pg_repos.py
git commit -m "feat: rewrite user_writes to async SQLAlchemy

Replaces Iceberg copy-on-write with row-level SQL operations.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 9: Auth repo rewrite — OAuth (ASETPLTFRM-232)

**Files:**
- Modify: `auth/repo/oauth.py`
- Test: `tests/backend/test_pg_repos.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/backend/test_pg_repos.py`:

```python
@pytest.mark.asyncio
async def test_get_by_oauth_sub(pg_session):
    """get_by_oauth_sub returns user by provider + sub."""
    from auth.repo.oauth import get_by_oauth_sub
    from auth.repo.user_writes import create

    await create(pg_session, {
        "email": "oauth@test.com", "hashed_password": "h",
        "full_name": "OAuth User", "role": "user",
        "oauth_provider": "google", "oauth_sub": "g123",
    })

    result = await get_by_oauth_sub(pg_session, "google", "g123")
    assert result is not None
    assert result["email"] == "oauth@test.com"

    result = await get_by_oauth_sub(pg_session, "google", "wrong")
    assert result is None


@pytest.mark.asyncio
async def test_get_or_create_by_oauth_new(pg_session):
    """get_or_create_by_oauth creates user if not found."""
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
    """get_or_create_by_oauth links OAuth to existing email."""
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
```

- [ ] **Step 2: Run test to verify new tests fail**

Run: `python -m pytest tests/backend/test_pg_repos.py -v -k "oauth"`
Expected: FAIL

- [ ] **Step 3: Rewrite auth/repo/oauth.py**

Replace the entire file contents:

```python
"""OAuth user operations — PostgreSQL via SQLAlchemy."""
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.user import User

log = logging.getLogger(__name__)


async def get_by_oauth_sub(
    session: AsyncSession,
    provider: str,
    oauth_sub: str,
) -> dict[str, Any] | None:
    """Find user by (oauth_provider, oauth_sub)."""
    result = await session.execute(
        select(User).where(
            User.oauth_provider == provider,
            User.oauth_sub == oauth_sub,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        return None
    return {
        c.name: getattr(user, c.name)
        for c in user.__table__.columns
    }


async def get_or_create_by_oauth(
    session: AsyncSession,
    provider: str,
    oauth_sub: str,
    email: str,
    full_name: str,
    picture_url: str | None = None,
) -> dict[str, Any]:
    """Find or create user by OAuth identity.

    Lookup order:
    1. Match on (oauth_provider, oauth_sub) -> return SSO user
    2. Match on email -> link OAuth to existing account
    3. No match -> create new user with sentinel password
    """
    # 1. Check by OAuth sub
    result = await session.execute(
        select(User).where(
            User.oauth_provider == provider,
            User.oauth_sub == oauth_sub,
        )
    )
    user = result.scalar_one_or_none()
    if user:
        return {
            c.name: getattr(user, c.name)
            for c in user.__table__.columns
        }

    # 2. Check by email
    result = await session.execute(
        select(User).where(User.email == email)
    )
    user = result.scalar_one_or_none()
    if user:
        user.oauth_provider = provider
        user.oauth_sub = oauth_sub
        if picture_url:
            user.profile_picture_url = picture_url
        user.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(user)
        log.info("Linked OAuth %s to existing user %s", provider,
                 user.user_id)
        return {
            c.name: getattr(user, c.name)
            for c in user.__table__.columns
        }

    # 3. Create new user
    now = datetime.now(timezone.utc)
    user = User(
        user_id=str(uuid.uuid4()),
        email=email,
        hashed_password="!sso_only_" + secrets.token_hex(32),
        full_name=full_name,
        role="user",
        is_active=True,
        oauth_provider=provider,
        oauth_sub=oauth_sub,
        profile_picture_url=picture_url,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    log.info("Created OAuth user %s (%s)", user.user_id, email)
    return {
        c.name: getattr(user, c.name)
        for c in user.__table__.columns
    }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/backend/test_pg_repos.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add auth/repo/oauth.py tests/backend/test_pg_repos.py
git commit -m "feat: rewrite OAuth repo to async SQLAlchemy

Indexed (provider, sub) lookup replaces full-table scan.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 10: Ticker + payment repos (ASETPLTFRM-233)

**Files:**
- Create: `auth/repo/ticker_repo.py`
- Create: `auth/repo/payment_repo.py`
- Test: `tests/backend/test_pg_repos.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/backend/test_pg_repos.py`:

```python
from backend.db.models.user_ticker import UserTicker
from backend.db.models.payment import PaymentTransaction


@pytest.mark.asyncio
async def test_link_ticker(pg_session):
    """link_ticker inserts a user-ticker record."""
    from auth.repo.ticker_repo import link_ticker, get_user_tickers
    from auth.repo.user_writes import create

    user = await create(pg_session, {
        "email": "t@test.com", "hashed_password": "h",
        "full_name": "T", "role": "user",
    })

    linked = await link_ticker(pg_session, user["user_id"], "AAPL")
    assert linked is True

    tickers = await get_user_tickers(pg_session, user["user_id"])
    assert "AAPL" in tickers


@pytest.mark.asyncio
async def test_link_ticker_duplicate(pg_session):
    """link_ticker returns False for duplicate."""
    from auth.repo.ticker_repo import link_ticker
    from auth.repo.user_writes import create

    user = await create(pg_session, {
        "email": "t2@test.com", "hashed_password": "h",
        "full_name": "T2", "role": "user",
    })
    await link_ticker(pg_session, user["user_id"], "MSFT")
    result = await link_ticker(pg_session, user["user_id"], "MSFT")
    assert result is False


@pytest.mark.asyncio
async def test_unlink_ticker(pg_session):
    """unlink_ticker removes the record."""
    from auth.repo.ticker_repo import (
        link_ticker, unlink_ticker, get_user_tickers,
    )
    from auth.repo.user_writes import create

    user = await create(pg_session, {
        "email": "t3@test.com", "hashed_password": "h",
        "full_name": "T3", "role": "user",
    })
    await link_ticker(pg_session, user["user_id"], "GOOG")
    await unlink_ticker(pg_session, user["user_id"], "GOOG")

    tickers = await get_user_tickers(pg_session, user["user_id"])
    assert "GOOG" not in tickers


@pytest.mark.asyncio
async def test_record_payment(pg_session):
    """record_transaction inserts a payment record."""
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
    """update_status modifies transaction status."""
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
```

- [ ] **Step 2: Run test to verify new tests fail**

Run: `python -m pytest tests/backend/test_pg_repos.py -v -k "ticker or payment"`
Expected: FAIL with ImportError

- [ ] **Step 3: Create auth/repo/ticker_repo.py**

```python
"""Ticker link/unlink — PostgreSQL via SQLAlchemy."""
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.user_ticker import UserTicker

log = logging.getLogger(__name__)


async def link_ticker(
    session: AsyncSession,
    user_id: str,
    ticker: str,
    source: str = "manual",
) -> bool:
    """Link ticker to user. Returns False if already linked."""
    result = await session.execute(
        select(UserTicker).where(
            UserTicker.user_id == user_id,
            UserTicker.ticker == ticker,
        )
    )
    if result.scalar_one_or_none():
        return False

    session.add(UserTicker(
        user_id=user_id,
        ticker=ticker,
        linked_at=datetime.now(timezone.utc),
        source=source,
    ))
    await session.commit()
    log.info("Linked %s to user %s", ticker, user_id)
    return True


async def unlink_ticker(
    session: AsyncSession,
    user_id: str,
    ticker: str,
) -> bool:
    """Unlink ticker from user. Returns False if not found."""
    result = await session.execute(
        delete(UserTicker).where(
            UserTicker.user_id == user_id,
            UserTicker.ticker == ticker,
        )
    )
    await session.commit()
    removed = result.rowcount > 0
    if removed:
        log.info("Unlinked %s from user %s", ticker, user_id)
    return removed


async def get_user_tickers(
    session: AsyncSession,
    user_id: str,
) -> list[str]:
    """Return list of tickers linked to user."""
    result = await session.execute(
        select(UserTicker.ticker).where(
            UserTicker.user_id == user_id,
        )
    )
    return [row[0] for row in result.all()]


async def get_all_user_tickers(
    session: AsyncSession,
) -> dict[str, list[str]]:
    """Return {user_id: [tickers]} for all users."""
    result = await session.execute(select(UserTicker))
    mapping: dict[str, list[str]] = {}
    for ut in result.scalars().all():
        mapping.setdefault(ut.user_id, []).append(ut.ticker)
    return mapping
```

- [ ] **Step 4: Create auth/repo/payment_repo.py**

```python
"""Payment transaction operations — PostgreSQL via SQLAlchemy."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.payment import PaymentTransaction

log = logging.getLogger(__name__)


async def record_transaction(
    session: AsyncSession,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Insert a payment transaction record."""
    txn = PaymentTransaction(
        transaction_id=data.get(
            "transaction_id", str(uuid.uuid4()),
        ),
        user_id=data["user_id"],
        gateway=data["gateway"],
        event_type=data["event_type"],
        gateway_event_id=data.get("gateway_event_id"),
        subscription_id=data.get("subscription_id"),
        customer_id=data.get("customer_id"),
        amount=data.get("amount"),
        currency=data.get("currency"),
        tier_before=data.get("tier_before"),
        tier_after=data.get("tier_after"),
        status=data["status"],
        raw_payload=data.get("raw_payload"),
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    log.info("Recorded %s txn %s", data["gateway"],
             txn.transaction_id)
    return {
        c.name: getattr(txn, c.name)
        for c in txn.__table__.columns
    }


async def update_status(
    session: AsyncSession,
    transaction_id: str,
    status: str,
) -> dict[str, Any]:
    """Update transaction status (reconciliation)."""
    result = await session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.transaction_id == transaction_id
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise ValueError(f"Transaction {transaction_id} not found")

    txn.status = status
    await session.commit()
    await session.refresh(txn)
    log.info("Updated txn %s status to %s",
             transaction_id, status)
    return {
        c.name: getattr(txn, c.name)
        for c in txn.__table__.columns
    }


async def get_by_user(
    session: AsyncSession,
    user_id: str,
) -> list[dict[str, Any]]:
    """Return all transactions for a user."""
    result = await session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.user_id == user_id
        )
    )
    return [
        {c.name: getattr(t, c.name) for c in t.__table__.columns}
        for t in result.scalars().all()
    ]
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/backend/test_pg_repos.py -v`
Expected: PASS (15 tests)

- [ ] **Step 6: Commit**

```bash
git add auth/repo/ticker_repo.py auth/repo/payment_repo.py \
    tests/backend/test_pg_repos.py
git commit -m "feat: ticker + payment PostgreSQL repos

link/unlink with composite PK, JSONB payment records.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 11: Rewrite IcebergUserRepository facade (ASETPLTFRM-232)

**Files:**
- Modify: `auth/repo/repository.py`

- [ ] **Step 1: Write failing test for async facade**

Append to `tests/backend/test_pg_repos.py`:

```python
@pytest.mark.asyncio
async def test_repository_facade(pg_session):
    """IcebergUserRepository facade delegates to PG modules."""
    from auth.repo.repository import IcebergUserRepository

    repo = IcebergUserRepository(session=pg_session)

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_pg_repos.py::test_repository_facade -v`
Expected: FAIL

- [ ] **Step 3: Rewrite auth/repo/repository.py**

Replace the entire file contents:

```python
"""User repository facade — PostgreSQL via SQLAlchemy.

Maintains the same interface as the old Iceberg-backed
IcebergUserRepository so callers do not need changes.
Will be renamed to UserRepository in cleanup story.
"""
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from auth.repo import oauth, ticker_repo, user_reads, user_writes

log = logging.getLogger(__name__)


class IcebergUserRepository:
    """Facade over PostgreSQL-backed user operations.

    Name kept for backward compatibility with callers.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        **kwargs,
    ) -> None:
        self._session = session

    # --- Reads ---

    async def get_by_email(
        self, email: str,
    ) -> dict[str, Any] | None:
        return await user_reads.get_by_email(
            self._session, email,
        )

    async def get_by_id(
        self, user_id: str,
    ) -> dict[str, Any] | None:
        return await user_reads.get_by_id(
            self._session, user_id,
        )

    async def list_all(self) -> list[dict[str, Any]]:
        return await user_reads.list_all(self._session)

    # --- Writes ---

    async def create(
        self, user_data: dict[str, Any],
    ) -> dict[str, Any]:
        return await user_writes.create(
            self._session, user_data,
        )

    async def update(
        self, user_id: str, updates: dict[str, Any],
    ) -> dict[str, Any]:
        return await user_writes.update(
            self._session, user_id, updates,
        )

    async def delete(self, user_id: str) -> None:
        return await user_writes.delete(
            self._session, user_id,
        )

    # --- OAuth ---

    async def get_by_oauth_sub(
        self, provider: str, oauth_sub: str,
    ) -> dict[str, Any] | None:
        return await oauth.get_by_oauth_sub(
            self._session, provider, oauth_sub,
        )

    async def get_or_create_by_oauth(
        self,
        provider: str,
        oauth_sub: str,
        email: str,
        full_name: str,
        picture_url: str | None = None,
    ) -> dict[str, Any]:
        return await oauth.get_or_create_by_oauth(
            self._session, provider, oauth_sub,
            email, full_name, picture_url,
        )

    # --- Tickers ---

    async def get_user_tickers(
        self, user_id: str,
    ) -> list[str]:
        return await ticker_repo.get_user_tickers(
            self._session, user_id,
        )

    async def link_ticker(
        self, user_id: str, ticker: str,
        source: str = "manual",
    ) -> bool:
        return await ticker_repo.link_ticker(
            self._session, user_id, ticker, source,
        )

    async def unlink_ticker(
        self, user_id: str, ticker: str,
    ) -> bool:
        return await ticker_repo.unlink_ticker(
            self._session, user_id, ticker,
        )

    async def get_all_user_tickers(
        self,
    ) -> dict[str, list[str]]:
        return await ticker_repo.get_all_user_tickers(
            self._session,
        )

    # --- Audit (stays on Iceberg — not migrated) ---

    async def append_audit_event(
        self, event_type: str, actor_user_id: str,
        target_user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # Audit log stays on Iceberg — import lazily
        from auth.repo.catalog import get_catalog
        from auth.repo.schemas import _AUDIT_PA_SCHEMA
        log.debug("Audit event %s (Iceberg)", event_type)
        # TODO: delegate to existing Iceberg audit writer
        pass

    async def list_audit_events(
        self,
    ) -> list[dict[str, Any]]:
        from auth.repo.catalog import get_catalog
        log.debug("List audit events (Iceberg)")
        return []
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/backend/test_pg_repos.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add auth/repo/repository.py tests/backend/test_pg_repos.py
git commit -m "feat: rewrite IcebergUserRepository facade to PostgreSQL

Same interface, async SQLAlchemy internals. Audit stays on Iceberg.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 12: Rewrite stocks registry + scheduler methods (ASETPLTFRM-234)

**Files:**
- Modify: `stocks/repository.py` (registry + scheduler methods only)
- Modify: `backend/jobs/scheduler_service.py`
- Test: `tests/backend/test_pg_repos.py` (append)

- [ ] **Step 1: Write failing tests for PG registry**

Append to `tests/backend/test_pg_repos.py`:

```python
from backend.db.models.registry import StockRegistry
from backend.db.models.scheduler import ScheduledJob


@pytest.mark.asyncio
async def test_registry_upsert_via_repo(pg_session):
    """Registry upsert inserts then updates on conflict."""
    from stocks.repository import upsert_registry, get_registry

    await upsert_registry(pg_session, {
        "ticker": "AAPL",
        "market": "NASDAQ",
        "total_rows": 100,
    })

    rows = await get_registry(pg_session, "AAPL")
    assert rows["ticker"] == "AAPL"
    assert rows["total_rows"] == 100

    # Upsert again with new data
    await upsert_registry(pg_session, {
        "ticker": "AAPL",
        "total_rows": 200,
    })
    rows = await get_registry(pg_session, "AAPL")
    assert rows["total_rows"] == 200


@pytest.mark.asyncio
async def test_scheduler_job_crud(pg_session):
    """Scheduler CRUD: create, read, toggle, delete."""
    from stocks.repository import (
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_pg_repos.py -v -k "registry or scheduler"`
Expected: FAIL

- [ ] **Step 3: Add PostgreSQL registry/scheduler functions**

These functions will be added to `stocks/repository.py` as
module-level async functions. The existing `StockRepository` class
methods for registry and scheduler will be updated to delegate to
these functions.

Add at the end of `stocks/repository.py`:

```python
# ── PostgreSQL-backed registry + scheduler functions ──────


async def get_registry(
    session: "AsyncSession",
    ticker: str | None = None,
) -> dict | pd.DataFrame:
    """Get registry entry by ticker or all entries."""
    from backend.db.models.registry import StockRegistry

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
    return pd.DataFrame([
        {c.name: getattr(r, c.name)
         for c in r.__table__.columns}
        for r in result.scalars().all()
    ])


async def upsert_registry(
    session: "AsyncSession",
    data: dict,
) -> None:
    """Insert or update registry entry by ticker."""
    from backend.db.models.registry import StockRegistry

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
        session.add(StockRegistry(**data))

    await session.commit()


async def get_scheduled_jobs(
    session: "AsyncSession",
) -> list[dict]:
    """Return all scheduled job definitions."""
    from backend.db.models.scheduler import ScheduledJob

    result = await session.execute(select(ScheduledJob))
    return [
        {c.name: getattr(j, c.name)
         for c in j.__table__.columns}
        for j in result.scalars().all()
    ]


async def upsert_scheduled_job(
    session: "AsyncSession",
    job: dict,
) -> None:
    """Insert or update scheduled job by job_id."""
    from backend.db.models.scheduler import ScheduledJob

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


async def delete_scheduled_job(
    session: "AsyncSession",
    job_id: str,
) -> None:
    """Delete scheduled job by job_id."""
    from backend.db.models.scheduler import ScheduledJob

    result = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.job_id == job_id
        )
    )
    job = result.scalar_one_or_none()
    if job:
        await session.delete(job)
        await session.commit()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/backend/test_pg_repos.py -v -k "registry or scheduler"`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add stocks/repository.py tests/backend/test_pg_repos.py
git commit -m "feat: PostgreSQL registry + scheduler job functions

Upsert pattern replaces Iceberg copy-on-write for both tables.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 13: Wire PG engine at startup (ASETPLTFRM-231)

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/bootstrap.py` (if exists, or create)

- [ ] **Step 1: Update backend/main.py startup**

In `ChatServer.__init__()`, after the existing Iceberg table
creation (line ~81), add PostgreSQL engine initialization:

```python
# After: _ensure_iceberg_tables()
# Add:
from backend.db.engine import get_engine, get_session_factory

self._pg_session_factory = get_session_factory()
log.info("PostgreSQL async engine ready")
```

Update the `IcebergUserRepository` instantiation to pass the
session factory. Find where `IcebergUserRepository` is created
and update:

```python
# Old:
# self._user_repo = IcebergUserRepository(catalog)
# New:
async with self._pg_session_factory() as session:
    self._user_repo = IcebergUserRepository(session=session)
```

Note: The exact wiring depends on how `bootstrap.py` creates
the repo. Read `backend/bootstrap.py` to find the current
instantiation pattern and adapt.

- [ ] **Step 2: Add PG health check to /v1/health**

Find the health endpoint in `backend/routes.py` or
`backend/main.py` and add a PostgreSQL connectivity check:

```python
async def _pg_health() -> dict:
    """Check PostgreSQL connectivity."""
    from backend.db.engine import get_engine

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"postgresql": "ok"}
    except Exception as exc:
        return {"postgresql": f"error: {exc}"}
```

- [ ] **Step 3: Verify with docker compose**

Run:
```bash
docker compose up -d
curl http://localhost:8181/v1/health | python -m json.tool
```

Expected: Health response includes `"postgresql": "ok"`

- [ ] **Step 4: Commit**

```bash
git add backend/main.py backend/routes.py
git commit -m "feat: wire PostgreSQL engine at startup + health check

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 14: Data migration script (ASETPLTFRM-232/233/234)

**Files:**
- Create: `scripts/migrate_iceberg_to_pg.py`
- Test: `tests/backend/test_migration.py`

- [ ] **Step 1: Write the migration script**

Create `scripts/migrate_iceberg_to_pg.py`:

```python
"""One-time data migration: Iceberg -> PostgreSQL.

Usage:
    # Ensure PostgreSQL is running and schema is up:
    alembic upgrade head

    # Run migration:
    PYTHONPATH=backend python scripts/migrate_iceberg_to_pg.py

Tables migrated (in FK order):
    1. auth.users -> users
    2. auth.user_tickers -> user_tickers
    3. auth.payment_transactions -> payment_transactions
    4. stocks.registry -> stock_registry
    5. stocks.scheduled_jobs -> scheduled_jobs
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from auth.repo.catalog import get_catalog
from backend.db.base import Base
from backend.db.models import (
    PaymentTransaction,
    ScheduledJob,
    StockRegistry,
    User,
    UserTicker,
)
from config import get_settings

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

# Iceberg table names
_TABLES = [
    ("auth.users", User),
    ("auth.user_tickers", UserTicker),
    ("auth.payment_transactions", PaymentTransaction),
    ("stocks.registry", StockRegistry),
    ("stocks.scheduled_jobs", ScheduledJob),
]


def _iceberg_rows(cat, table_name: str) -> list[dict]:
    """Read all rows from Iceberg table as dicts."""
    try:
        tbl = cat.load_table(table_name)
        df = tbl.scan().to_pandas()
        return df.to_dict("records")
    except Exception as exc:
        log.warning("Skip %s: %s", table_name, exc)
        return []


def _map_row(model_cls, row: dict) -> dict:
    """Filter row keys to match ORM model columns."""
    columns = {c.name for c in model_cls.__table__.columns}
    return {k: v for k, v in row.items() if k in columns}


async def migrate(database_url: str) -> dict[str, int]:
    """Migrate all tables. Returns {table: row_count}."""
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )

    cat = get_catalog(str(Path(__file__).parent.parent))
    results = {}

    async with factory() as session:
        for ice_name, model_cls in _TABLES:
            rows = _iceberg_rows(cat, ice_name)
            if not rows:
                log.info("%-35s  0 rows (empty/missing)", ice_name)
                results[ice_name] = 0
                continue

            # Clear existing PG data for idempotency
            await session.execute(
                text(f"DELETE FROM {model_cls.__tablename__}")
            )

            for row in rows:
                mapped = _map_row(model_cls, row)
                session.add(model_cls(**mapped))

            await session.commit()
            log.info(
                "%-35s  %d rows migrated", ice_name, len(rows),
            )
            results[ice_name] = len(rows)

    await engine.dispose()
    return results


async def verify(database_url: str, expected: dict[str, int]):
    """Verify row counts match."""
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )

    async with factory() as session:
        for ice_name, model_cls in _TABLES:
            result = await session.execute(
                text(
                    f"SELECT COUNT(*) FROM "
                    f"{model_cls.__tablename__}"
                )
            )
            pg_count = result.scalar()
            ice_count = expected.get(ice_name, 0)
            status = "OK" if pg_count == ice_count else "MISMATCH"
            log.info(
                "%-35s  Iceberg=%d  PG=%d  [%s]",
                ice_name, ice_count, pg_count, status,
            )

    await engine.dispose()


async def main():
    url = get_settings().database_url
    log.info("=== Iceberg -> PostgreSQL Migration ===\n")

    results = await migrate(url)

    log.info("\n=== Verification ===\n")
    await verify(url, results)

    total = sum(results.values())
    log.info("\nDone. %d total rows migrated.", total)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Test migration script against live data**

Run:
```bash
docker compose up -d postgres
alembic upgrade head
PYTHONPATH=backend python scripts/migrate_iceberg_to_pg.py
```

Expected: Row counts for each table, all [OK]

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate_iceberg_to_pg.py
git commit -m "feat: one-time Iceberg -> PostgreSQL data migration

Migrates 5 tables in FK order with idempotent re-run support.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 15: Update test fixtures (all stories)

**Files:**
- Modify: `tests/backend/conftest.py`

- [ ] **Step 1: Add pg_session fixture to conftest.py**

Add to `tests/backend/conftest.py`:

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.db.base import Base


@pytest_asyncio.fixture
async def pg_session():
    """Async SQLite session for testing PG-backed repos."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with factory() as session:
        yield session
    await engine.dispose()
```

- [ ] **Step 2: Run full test suite**

Run:
```bash
python -m pytest tests/ -v --timeout=60
```

Expected: All tests pass (existing + new)

- [ ] **Step 3: Commit**

```bash
git add tests/backend/conftest.py
git commit -m "feat: add pg_session fixture to test conftest

Shared async SQLite session for PostgreSQL repo tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 16: DuckDB query layer (ASETPLTFRM-235)

**Files:**
- Create: `backend/db/duckdb_engine.py`
- Test: `tests/backend/test_duckdb.py`

- [ ] **Step 1: Write failing test**

Create `tests/backend/test_duckdb.py`:

```python
"""Test DuckDB Iceberg query layer."""
import pytest
from unittest.mock import patch, MagicMock


def test_duckdb_engine_creates_connection():
    """DuckDB engine returns a connection."""
    from backend.db.duckdb_engine import get_connection

    conn = get_connection()
    assert conn is not None
    result = conn.execute("SELECT 1 AS n").fetchone()
    assert result[0] == 1
    conn.close()


def test_duckdb_query_with_params():
    """DuckDB parameterized query works."""
    from backend.db.duckdb_engine import get_connection

    conn = get_connection()
    conn.execute(
        "CREATE TABLE test (ticker VARCHAR, price DOUBLE)"
    )
    conn.execute(
        "INSERT INTO test VALUES ('AAPL', 150.0), "
        "('MSFT', 300.0)"
    )
    result = conn.execute(
        "SELECT price FROM test WHERE ticker = ?", ["AAPL"]
    ).fetchone()
    assert result[0] == 150.0
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_duckdb.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Create backend/db/duckdb_engine.py**

```python
"""DuckDB in-process query engine for Iceberg tables."""
import logging
from pathlib import Path

import duckdb

from backend.paths import ICEBERG_CATALOG, ICEBERG_WAREHOUSE

log = logging.getLogger(__name__)


def get_connection() -> duckdb.DuckDBPyConnection:
    """Create a new DuckDB connection with Iceberg support.

    Each connection is short-lived — create per query batch,
    close after use. DuckDB handles its own caching.
    """
    conn = duckdb.connect(":memory:")

    # Install and load Iceberg extension
    conn.execute("INSTALL iceberg; LOAD iceberg;")

    log.debug("DuckDB connection created with Iceberg support")
    return conn


def query_iceberg_table(
    table_name: str,
    sql: str,
    params: list | None = None,
) -> list[dict]:
    """Run a SQL query against an Iceberg table.

    Args:
        table_name: Full table name (e.g., 'stocks.ohlcv')
        sql: SQL query with ? placeholders
        params: Query parameters

    Returns:
        List of dicts (column_name: value)
    """
    conn = get_connection()
    try:
        # Register Iceberg table
        metadata_path = (
            ICEBERG_WAREHOUSE
            / table_name.replace(".", "/")
            / "metadata"
        )
        # Find latest metadata file
        metadata_files = sorted(
            metadata_path.glob("*.metadata.json"), reverse=True,
        )
        if not metadata_files:
            log.warning("No metadata for %s", table_name)
            return []

        conn.execute(
            f"CREATE VIEW {table_name.split('.')[-1]} AS "
            f"SELECT * FROM iceberg_scan('{metadata_files[0]}')"
        )

        result = conn.execute(sql, params or [])
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/backend/test_duckdb.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/db/duckdb_engine.py tests/backend/test_duckdb.py
git commit -m "feat: DuckDB in-process query layer for Iceberg

Connection factory + parameterized query support.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Task 17: End-to-end smoke test

**Files:** None (manual verification)

- [ ] **Step 1: Start all services**

Run:
```bash
docker compose up -d
docker compose ps  # verify all healthy
```

- [ ] **Step 2: Run Alembic migration**

Run:
```bash
alembic upgrade head
```

- [ ] **Step 3: Run data migration**

Run:
```bash
PYTHONPATH=backend python scripts/migrate_iceberg_to_pg.py
```

Expected: All tables show [OK]

- [ ] **Step 4: Run unit tests**

Run:
```bash
python -m pytest tests/ -v --timeout=60
```

Expected: All tests pass

- [ ] **Step 5: Run E2E tests**

Run:
```bash
cd e2e && npm test
```

Expected: All 219 tests pass

- [ ] **Step 6: Smoke test key flows manually**

Test these in the UI:
- Login / signup
- Add/remove watchlist ticker
- Payment checkout flow
- Scheduler create/edit/delete job
- Dashboard charts load

- [ ] **Step 7: Final commit with all remaining changes**

```bash
git add -A
git commit -m "feat: hybrid DB migration — PostgreSQL for CRUD, Iceberg for analytics

5 tables migrated to PostgreSQL (users, user_tickers,
payment_transactions, stock_registry, scheduled_jobs).
14 tables remain on Iceberg (append-only + scoped-delete).
DuckDB query layer for Iceberg reads.

ASETPLTFRM-225 / Sprint 4

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

## Dependency Graph

```
Task 1  (deps)
Task 2  (engine)     ─┐
Task 3  (User model)  ├─ Task 6 (Alembic) ─┐
Task 4  (Ticker+Pay)  │                     │
Task 5  (Reg+Sched)  ─┘                     │
                                             ├─ Task 13 (wire startup)
Task 7  (user reads)  ─┐                    │
Task 8  (user writes)  ├─ Task 11 (facade) ─┤
Task 9  (OAuth)       ─┘                    │
Task 10 (ticker+pay repos) ────────────────┤
Task 12 (registry+sched) ─────────────────┤
Task 15 (test fixtures) ──────────────────┤
                                           │
Task 14 (data migration) ─────────────────┤
Task 16 (DuckDB) ─────────────────────────┤
                                           │
Task 17 (smoke test) ─────────────────────┘
```
