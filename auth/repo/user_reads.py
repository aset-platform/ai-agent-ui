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
