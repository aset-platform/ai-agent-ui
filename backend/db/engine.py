"""Async SQLAlchemy engine and session factory."""
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from config import get_settings

log = logging.getLogger(__name__)


@lru_cache
def get_engine():
    """Create async engine from DATABASE_URL (cached).

    Use only from the uvicorn event loop (FastAPI request
    handlers, background tasks spawned via ``BackgroundTasks``).
    Scheduler jobs run under their own ``asyncio.run`` event
    loop and MUST NOT touch this cached engine — the pool's
    asyncpg connections are bound to the loop that first
    created them, and reusing them from a different loop
    raises::

        Task ... got Future ... attached to a different loop

    Use ``disposable_pg_session()`` from those callers instead.
    """
    url = get_settings().database_url
    log.info("Creating async engine: %s", url.split("@")[-1])
    return create_async_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return cached async session factory.

    Same loop-binding caveat as :func:`get_engine` — for
    scheduler-job callers use :func:`disposable_pg_session`.
    """
    return async_sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


@asynccontextmanager
async def disposable_pg_session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession backed by a per-call NullPool engine.

    The engine is created on entry and disposed on exit, so every
    asyncpg connection lives entirely inside the caller's event
    loop. Use this from scheduler jobs (each fires under its own
    ``asyncio.run`` event loop), maintenance scripts, or anywhere
    else outside the uvicorn loop where the cached engine in
    :func:`get_engine` would leak loop-bound futures::

        async with disposable_pg_session() as session:
            await session.execute(...)
            await session.commit()

    Inside FastAPI request handlers / background tasks spawned
    on the uvicorn loop, prefer :func:`get_session_factory` — the
    cached pool is faster when reused inside its native loop.
    """
    url = get_settings().database_url
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        pool_pre_ping=True,
        echo=False,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()
