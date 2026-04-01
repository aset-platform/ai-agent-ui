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

# Ensure imports work
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "backend"))
sys.path.insert(0, str(_root))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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
logging.basicConfig(
    level=logging.INFO, format="%(message)s",
)

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


def _clean_value(v):
    """Sanitize Iceberg values for PostgreSQL."""
    import math
    import pandas as pd
    if v is None:
        return None
    # Catch all NaT/NaN variants
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        if v.tzinfo is None:
            v = v.tz_localize("UTC")
        return v.to_pydatetime()
    if hasattr(v, 'isoformat') and hasattr(v, 'tzinfo'):
        if v.tzinfo is None:
            from datetime import timezone
            v = v.replace(tzinfo=timezone.utc)
    return v


def _map_row(model_cls, row: dict) -> dict:
    """Filter row keys and sanitize values for PG."""
    columns = {c.name for c in model_cls.__table__.columns}
    return {
        k: _clean_value(v)
        for k, v in row.items()
        if k in columns
    }


async def migrate(database_url: str) -> dict[str, int]:
    """Migrate all tables. Returns {table: row_count}."""
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        engine, class_=AsyncSession,
        expire_on_commit=False,
    )

    # Import catalog loader
    from auth.repo.catalog import get_catalog
    cat = get_catalog(str(_root))
    results = {}

    async with factory() as session:
        for ice_name, model_cls in _TABLES:
            rows = _iceberg_rows(cat, ice_name)
            if not rows:
                log.info(
                    "%-40s  0 rows (empty/missing)",
                    ice_name,
                )
                results[ice_name] = 0
                continue

            # Clear existing PG data (idempotent)
            await session.execute(
                text(
                    f"DELETE FROM "
                    f"{model_cls.__tablename__}"
                )
            )

            for row in rows:
                mapped = _map_row(model_cls, row)
                session.add(model_cls(**mapped))

            await session.commit()
            log.info(
                "%-40s  %d rows migrated",
                ice_name, len(rows),
            )
            results[ice_name] = len(rows)

    await engine.dispose()
    return results


async def verify(
    database_url: str, expected: dict[str, int],
):
    """Verify row counts match."""
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        engine, class_=AsyncSession,
        expire_on_commit=False,
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
            status = (
                "OK" if pg_count == ice_count
                else "MISMATCH"
            )
            log.info(
                "%-40s  Iceberg=%d  PG=%d  [%s]",
                ice_name, ice_count, pg_count, status,
            )

    await engine.dispose()


async def main():
    url = get_settings().database_url
    log.info("=== Iceberg -> PostgreSQL Migration ===")
    log.info("")

    results = await migrate(url)

    log.info("")
    log.info("=== Verification ===")
    log.info("")
    await verify(url, results)

    total = sum(results.values())
    log.info("")
    log.info("Done. %d total rows migrated.", total)


if __name__ == "__main__":
    asyncio.run(main())
