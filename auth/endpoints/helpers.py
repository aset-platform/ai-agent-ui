"""Shared helpers, singleton accessors, and guard functions for auth endpoints.

Functions
---------
- :func:`_get_repo` — lru_cache singleton for IcebergUserRepository
- :func:`_get_oauth_svc` — lru_cache singleton for OAuthService
- :func:`_user_to_response` — convert raw user dict to UserResponse
- :func:`_require_active_user` — raise HTTP 401 for missing/inactive users
"""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from fastapi import HTTPException

from auth.models import UserResponse
from auth.oauth_service import OAuthService
from auth.repository import IcebergUserRepository

# Module-level logger; cannot be moved into a class
# as these are module-level functions.
_logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_repo() -> IcebergUserRepository:
    """Return the app-wide IcebergUserRepository.

    Constructed once and cached for the process lifetime.

    Returns:
        The cached IcebergUserRepository instance.
    """
    return IcebergUserRepository()


@lru_cache(maxsize=1)
def _get_oauth_svc() -> OAuthService:
    """Return the application-wide :class:`~auth.oauth_service.OAuthService`.

    The state store backend is determined by ``REDIS_URL``: when set,
    OAuth state tokens are stored in Redis (shared across processes);
    otherwise an in-memory store is used.

    Returns:
        The cached :class:`~auth.oauth_service.OAuthService` instance.
    """
    import os

    from config import get_settings

    from auth.token_store import create_token_store

    redis_url = os.environ.get("REDIS_URL", "")
    state_store = create_token_store(
        redis_url,
        prefix="auth:oauth_state:",
    )
    return OAuthService(get_settings(), state_store=state_store)


def _user_to_response(user: dict[str, Any]) -> UserResponse:
    """Convert a raw user dict to a UserResponse.

    Sensitive fields (``hashed_password``, ``password_reset_token``,
    ``password_reset_expiry``) are intentionally excluded.

    Args:
        user: A user dict as returned by
            :class:`~auth.repository.IcebergUserRepository`.

    Returns:
        A :class:`~auth.models.UserResponse` safe to include in API responses.
    """

    def _iso(dt: datetime | None) -> str | None:
        """Convert datetime to ISO-8601 string, attaching UTC tz if naive."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    def _str_or_none(val: object) -> str | None:
        """Return *val* as a string, or ``None`` for NaN / non-str."""
        if val is None:
            return None
        if isinstance(val, float):
            return None  # Parquet NaN
        return str(val)

    raw_perms = user.get("page_permissions")
    perms = _json.loads(raw_perms) if isinstance(raw_perms, str) else None

    return UserResponse(
        user_id=user["user_id"],
        email=user["email"],
        full_name=user["full_name"],
        role=user["role"],
        is_active=user["is_active"],
        created_at=_iso(user.get("created_at")),
        updated_at=_iso(user.get("updated_at")),
        last_login_at=_iso(user.get("last_login_at")),
        avatar_url=_str_or_none(user.get("profile_picture_url")),
        page_permissions=perms,
    )


def _subscription_claims(
    user: dict[str, Any],
) -> dict[str, Any]:
    """Extract subscription kwargs for access token creation.

    Reads the user's subscription fields from Iceberg and
    computes ``usage_remaining`` from quota minus monthly count.

    Args:
        user: A user dict from
            :class:`~auth.repository.IcebergUserRepository`.

    Returns:
        A dict with ``subscription_tier``,
        ``subscription_status``, and ``usage_remaining``.
    """
    from subscription_config import (
        DEFAULT_STATUS,
        DEFAULT_TIER,
        USAGE_QUOTAS,
    )

    tier = user.get("subscription_tier") or DEFAULT_TIER
    status = user.get("subscription_status") or DEFAULT_STATUS
    quota = USAGE_QUOTAS.get(tier, 0)
    count = user.get("monthly_usage_count") or 0

    if quota == 0:
        remaining = None  # unlimited
    else:
        remaining = max(0, quota - count)

    return {
        "subscription_tier": tier,
        "subscription_status": status,
        "usage_remaining": remaining,
    }


def _require_active_user(
    user: dict[str, Any] | None, email: str
) -> dict[str, Any]:
    """Raise HTTP 401 if the user is not found or is deactivated.

    Args:
        user: User dict from the repository, or ``None`` if not found.
        email: The email that was looked up (for debug logging only).

    Returns:
        The user dict if valid and active.

    Raises:
        HTTPException: 401 with ``"Invalid credentials"`` detail.
    """
    if user is None or not user.get("is_active", False):
        _logger.warning(
            "Login failed for email=%s (not found or inactive).", email
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return user
