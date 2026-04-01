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
    1. Match on (oauth_provider, oauth_sub) -> return
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
        log.info(
            "Linked OAuth %s to existing user %s",
            provider, user.user_id,
        )
        return {
            c.name: getattr(user, c.name)
            for c in user.__table__.columns
        }

    # 3. Create new user
    now = datetime.now(timezone.utc)
    sentinel = "!sso_only_" + secrets.token_hex(32)
    user = User(
        user_id=str(uuid.uuid4()),
        email=email,
        hashed_password=sentinel,
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
    log.info(
        "Created OAuth user %s (%s)", user.user_id, email,
    )
    return {
        c.name: getattr(user, c.name)
        for c in user.__table__.columns
    }
