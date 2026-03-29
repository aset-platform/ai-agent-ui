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
