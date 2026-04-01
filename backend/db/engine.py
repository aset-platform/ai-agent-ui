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
        pool_pre_ping=True,
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
