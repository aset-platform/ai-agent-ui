"""Ingestion cursor and skipped-ticker CRUD operations."""
import logging

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.ingestion_cursor import (
    IngestionCursor,
)
from backend.db.models.ingestion_skipped import (
    IngestionSkipped,
)
from backend.db.models.stock_master import StockMaster

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Cursor functions
# ------------------------------------------------------------------


async def get_cursor(
    session: AsyncSession,
    cursor_name: str,
) -> IngestionCursor | None:
    """Get cursor by name."""
    result = await session.execute(
        select(IngestionCursor).where(
            IngestionCursor.cursor_name == cursor_name
        )
    )
    return result.scalar_one_or_none()


async def create_cursor(
    session: AsyncSession,
    cursor_name: str,
    total_tickers: int,
    batch_size: int = 50,
) -> IngestionCursor:
    """Create a new cursor. Raises if name exists."""
    cursor = IngestionCursor(
        cursor_name=cursor_name,
        total_tickers=total_tickers,
        batch_size=batch_size,
    )
    session.add(cursor)
    await session.commit()
    _logger.info("Created cursor: %s", cursor_name)
    return cursor


async def advance_cursor(
    session: AsyncSession,
    cursor_name: str,
    last_processed_id: int,
) -> None:
    """Update last_processed_id for cursor.

    This is called per-ticker, not per-batch.
    """
    result = await session.execute(
        select(IngestionCursor).where(
            IngestionCursor.cursor_name == cursor_name
        )
    )
    cursor = result.scalar_one_or_none()
    if cursor is None:
        raise ValueError(
            f"Cursor not found: {cursor_name}"
        )
    cursor.last_processed_id = last_processed_id
    await session.commit()
    _logger.debug(
        "Advanced cursor %s to id=%d",
        cursor_name,
        last_processed_id,
    )


async def set_cursor_status(
    session: AsyncSession,
    cursor_name: str,
    status: str,
) -> None:
    """Set cursor status.

    Valid: pending, in_progress, completed, paused.
    """
    result = await session.execute(
        select(IngestionCursor).where(
            IngestionCursor.cursor_name == cursor_name
        )
    )
    cursor = result.scalar_one_or_none()
    if cursor is None:
        raise ValueError(
            f"Cursor not found: {cursor_name}"
        )
    cursor.status = status
    await session.commit()
    _logger.info(
        "Cursor %s status -> %s",
        cursor_name,
        status,
    )


async def reset_cursor(
    session: AsyncSession,
    cursor_name: str,
) -> None:
    """Reset cursor to last_processed_id=0, status=pending."""
    result = await session.execute(
        select(IngestionCursor).where(
            IngestionCursor.cursor_name == cursor_name
        )
    )
    cursor = result.scalar_one_or_none()
    if cursor is None:
        raise ValueError(
            f"Cursor not found: {cursor_name}"
        )
    cursor.last_processed_id = 0
    cursor.status = "pending"
    await session.commit()
    _logger.info("Reset cursor: %s", cursor_name)


# ------------------------------------------------------------------
# Keyset pagination
# ------------------------------------------------------------------


async def get_next_batch(
    session: AsyncSession,
    cursor_name: str,
) -> list[StockMaster]:
    """Get next batch of stocks using keyset pagination.

    Reads cursor's last_processed_id and batch_size,
    queries stock_master WHERE id > last_processed_id
    AND is_active = true ORDER BY id LIMIT batch_size.
    """
    result = await session.execute(
        select(IngestionCursor).where(
            IngestionCursor.cursor_name == cursor_name
        )
    )
    cursor = result.scalar_one_or_none()
    if cursor is None:
        raise ValueError(
            f"Cursor not found: {cursor_name}"
        )

    batch = await session.execute(
        select(StockMaster)
        .where(
            and_(
                StockMaster.id
                > cursor.last_processed_id,
                StockMaster.is_active.is_(True),
            )
        )
        .order_by(StockMaster.id)
        .limit(cursor.batch_size)
    )
    return list(batch.scalars().all())


# ------------------------------------------------------------------
# Skipped-ticker functions
# ------------------------------------------------------------------


async def log_skipped(
    session: AsyncSession,
    cursor_name: str,
    ticker: str,
    job_type: str,
    error_message: str,
    error_category: str,
) -> None:
    """Log a failed ticker. Truncates error_message to 1000.

    If ticker+cursor_name+job_type already exists and
    resolved=false: increment attempts, update timestamp.
    Else: insert new row.
    """
    truncated = error_message[:1000]

    result = await session.execute(
        select(IngestionSkipped).where(
            and_(
                IngestionSkipped.cursor_name
                == cursor_name,
                IngestionSkipped.ticker == ticker,
                IngestionSkipped.job_type == job_type,
                IngestionSkipped.resolved.is_(False),
            )
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.attempts += 1
        existing.error_message = truncated
        existing.error_category = error_category
        existing.last_attempted_at = func.now()
    else:
        session.add(
            IngestionSkipped(
                cursor_name=cursor_name,
                ticker=ticker,
                job_type=job_type,
                error_message=truncated,
                error_category=error_category,
            )
        )

    await session.commit()
    _logger.info(
        "Logged skipped: %s/%s/%s (%s)",
        cursor_name,
        ticker,
        job_type,
        error_category,
    )


async def get_skipped(
    session: AsyncSession,
    cursor_name: str | None = None,
    error_category: str | None = None,
    resolved: bool | None = None,
    ticker: str | None = None,
) -> list[IngestionSkipped]:
    """Get skipped records with optional filters."""
    stmt = select(IngestionSkipped)
    conditions = []

    if cursor_name is not None:
        conditions.append(
            IngestionSkipped.cursor_name == cursor_name
        )
    if error_category is not None:
        conditions.append(
            IngestionSkipped.error_category
            == error_category
        )
    if resolved is not None:
        conditions.append(
            IngestionSkipped.resolved.is_(resolved)
        )
    if ticker is not None:
        conditions.append(
            IngestionSkipped.ticker == ticker
        )

    if conditions:
        stmt = stmt.where(and_(*conditions))

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def mark_resolved(
    session: AsyncSession,
    skipped_id: int,
) -> None:
    """Mark a skipped record as resolved=true."""
    result = await session.execute(
        select(IngestionSkipped).where(
            IngestionSkipped.id == skipped_id
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise ValueError(
            f"Skipped record not found: {skipped_id}"
        )
    record.resolved = True
    await session.commit()
    _logger.info("Resolved skipped id=%d", skipped_id)


_RETRYABLE_CATEGORIES = {"rate_limit", "timeout"}


async def get_retryable_skipped(
    session: AsyncSession,
    cursor_name: str,
    all_categories: bool = False,
) -> list[IngestionSkipped]:
    """Get skipped tickers eligible for retry.

    Default: only rate_limit and timeout categories.
    all_categories=True: all unresolved.
    """
    conditions = [
        IngestionSkipped.cursor_name == cursor_name,
        IngestionSkipped.resolved.is_(False),
    ]

    if not all_categories:
        conditions.append(
            IngestionSkipped.error_category.in_(
                _RETRYABLE_CATEGORIES
            )
        )

    result = await session.execute(
        select(IngestionSkipped).where(
            and_(*conditions)
        )
    )
    return list(result.scalars().all())
