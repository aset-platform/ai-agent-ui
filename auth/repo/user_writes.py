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

# Tier → role mapping used by auto-sync.  Superuser is
# sticky and never auto-demoted by subscription changes.
_PAID_TIERS = frozenset({"pro", "premium"})


def _role_for_tier(tier: str | None) -> str:
    """Return the role a non-superuser user should have
    for the given *tier*.  ``None`` / ``"free"`` → general.
    """
    if tier and tier in _PAID_TIERS:
        return "pro"
    return "general"


async def create(
    session: AsyncSession,
    user_data: dict[str, Any],
) -> dict[str, Any]:
    """Create a new user. Raises ValueError on duplicate email."""
    email = user_data["email"]

    existing = await session.execute(
        select(User).where(User.email == email)
    )
    if existing.scalar_one_or_none():
        raise ValueError(
            f"User with email {email} already exists"
        )

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
    """Update user fields. Raises ValueError if not found.

    Side-effect: when ``subscription_tier`` is in *updates* and the
    user's current role is NOT ``"superuser"``, the role is
    auto-synced from the new tier.  Superusers are sticky — tier
    changes never demote them.  An audit event
    (``ROLE_PROMOTED`` / ``ROLE_DEMOTED``) is written after commit
    when a role flip actually happens.
    """
    result = await session.execute(
        select(User).where(User.user_id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise ValueError(f"User {user_id} not found")

    # ── Auto-sync role from subscription_tier ────────────────
    # Runs before the setattr loop so the computed role lands
    # via the same code path as any explicit role update.
    role_transition: tuple[str, str, str] | None = None
    if (
        "subscription_tier" in updates
        and user.role != "superuser"
    ):
        new_tier = updates["subscription_tier"]
        new_role = _role_for_tier(new_tier)
        if user.role != new_role and "role" not in updates:
            updates = {**updates, "role": new_role}
            role_transition = (
                user.role, new_role, new_tier or "free",
            )

    for key, value in updates.items():
        if key in _IMMUTABLE_FIELDS:
            continue
        if hasattr(user, key):
            setattr(user, key, value)

    user.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user)

    log.info("Updated user %s", user_id)

    # ── Post-commit audit for role changes ──────────────────
    if role_transition is not None:
        old_role, new_role, new_tier = role_transition
        try:
            from pyiceberg.catalog import load_catalog

            from auth.repo.audit import append_audit_event

            evt = (
                "ROLE_PROMOTED"
                if old_role == "general" and new_role == "pro"
                else "ROLE_DEMOTED"
            )
            cat = load_catalog("local")
            # Actor == target: this path represents an
            # automated system-driven transition triggered by
            # the user's own subscription change.
            append_audit_event(
                cat,
                evt,
                str(user.user_id),
                str(user.user_id),
                {
                    "old_role": old_role,
                    "new_role": new_role,
                    "reason": "subscription_tier_change",
                    "new_tier": new_tier,
                },
            )
        except Exception:
            log.warning(
                "Failed to write role-change audit",
                exc_info=True,
            )

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
