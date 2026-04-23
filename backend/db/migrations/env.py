"""Alembic async migration environment."""
import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Ensure backend is on sys.path for model imports
_backend = str(Path(__file__).resolve().parent.parent.parent)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from backend.db.base import Base
from backend.db.models import (  # noqa: F401
    IngestionCursor,
    IngestionSkipped,
    PaymentTransaction,
    Pipeline,
    PipelineStep,
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
    ScheduledJob,
    SchedulerRun,
    SentimentDormant,
    StockMaster,
    StockRegistry,
    StockTag,
    User,
    UserMemory,
    UserTicker,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _get_url() -> str:
    """DATABASE_URL env var overrides alembic.ini (for Docker)."""
    return os.environ.get(
        "DATABASE_URL",
        config.get_main_option("sqlalchemy.url"),
    )


async def run_async_migrations() -> None:
    connectable = create_async_engine(_get_url())
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
