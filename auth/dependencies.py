"""FastAPI dependency functions for JWT auth, RBAC, and subscription guards.

Dependencies
------------
- :func:`get_current_user` — decode Bearer token → UserContext.
- :func:`superuser_only` — additionally require ``"superuser"`` role.
- :func:`require_tier` — factory returning a dependency that enforces
  a minimum subscription tier.
- :func:`check_usage_quota` — reject if monthly quota is exhausted.

Usage in a FastAPI route::

    from fastapi import APIRouter, Depends
    from auth.dependencies import (
        get_current_user,
        require_tier,
        check_usage_quota,
    )
    from auth.models import UserContext

    router = APIRouter()

    @router.get("/me")
    def me(user: UserContext = Depends(get_current_user)):
        return {"user_id": user.user_id}

    @router.get("/pro-feature")
    def pro(user: UserContext = Depends(require_tier("pro"))):
        return {"tier": user.subscription_tier}
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Callable

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

from auth.models import UserContext
from auth.service import AuthService
from auth.token_store import create_token_store

logger = logging.getLogger(__name__)

# OAuth2 scheme — extracts the Bearer token from the Authorization header.
# tokenUrl is the login endpoint (used by OpenAPI docs "Authorize" button).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login/form")


@lru_cache(maxsize=1)
def _get_service() -> AuthService:
    """Create and cache the :class:`~auth.service.AuthService` singleton.

    Reads configuration from environment variables.  The result is cached for
    the process lifetime so the in-memory refresh-token deny-list persists.

    Returns:
        The application-wide :class:`~auth.service.AuthService` instance.

    Raises:
        ValueError: If ``JWT_SECRET_KEY`` is missing or shorter than 32 chars.

    Example:
        >>> svc = _get_service()  # doctest: +SKIP
        >>> isinstance(svc, AuthService)
        True
    """
    secret = os.environ.get("JWT_SECRET_KEY", "")
    access_mins = int(
        os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60"),
    )
    refresh_days = int(
        os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7"),
    )
    redis_url = os.environ.get("REDIS_URL", "")
    store = create_token_store(redis_url)
    return AuthService(
        secret_key=secret,
        access_expire_minutes=access_mins,
        refresh_expire_days=refresh_days,
        token_store=store,
    )


def get_auth_service() -> AuthService:
    """Return the :class:`~auth.service.AuthService` singleton.

    Wrap endpoints that need direct service access
    (e.g. login, logout, refresh) with
    ``Depends(get_auth_service)``.

    Returns:
        The cached :class:`~auth.service.AuthService` instance.

    Example:
        >>> from auth.dependencies import get_auth_service
        >>> svc = get_auth_service()  # doctest: +SKIP
    """
    return _get_service()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    service: AuthService = Depends(get_auth_service),
) -> UserContext:
    """Validate the Bearer token and return the caller's context.

    Decodes the JWT, checks its signature and expiry,
    verifies it is an access token (not a refresh token),
    and returns a :class:`~auth.models.UserContext`.

    Args:
        token: Raw JWT string extracted from the ``Authorization: Bearer``
            header by :data:`oauth2_scheme`.
        service: The :class:`~auth.service.AuthService` singleton injected by
            :func:`get_auth_service`.

    Returns:
        A :class:`~auth.models.UserContext` containing ``user_id``, ``email``,
        and ``role`` from the token payload.

    Raises:
        HTTPException: 401 if the token is missing, invalid, expired, or
            of the wrong type.
        HTTPException: 401 if the user's ``user_id`` or
            ``role`` is absent from the token payload.

    Example:
        >>> # In a route:
        >>> # def my_route(user: UserContext = Depends(get_current_user)): ...
    """
    payload = service.decode_token(token, expected_type="access")

    user_id: str | None = payload.get("sub")
    email: str | None = payload.get("email")
    role: str | None = payload.get("role")

    if not user_id or not role:
        raise HTTPException(
            status_code=401,
            detail="Malformed token payload",
        )

    logger.debug("Authenticated user_id=%s role=%s", user_id, role)
    return UserContext(
        user_id=user_id,
        email=email or "",
        role=role,
        subscription_tier=payload.get(
            "subscription_tier", "free"
        ),
        subscription_status=payload.get(
            "subscription_status", "active"
        ),
        usage_remaining=payload.get("usage_remaining"),
    )


def superuser_only(
    user: UserContext = Depends(get_current_user),
) -> UserContext:
    """FastAPI dependency that restricts access to superusers.

    Extends :func:`get_current_user` and additionally checks that the
    authenticated caller has the ``"superuser"`` role.

    Args:
        user: The authenticated :class:`~auth.models.UserContext` from
            :func:`get_current_user`.

    Returns:
        The same :class:`~auth.models.UserContext` if the role check passes.

    Raises:
        HTTPException: 403 if the caller is authenticated but not a superuser.

    Example:
        >>> # In a route:
        >>> # def admin_route(user: UserContext = Depends(superuser_only)): ...
    """
    if user.role != "superuser":
        logger.warning(
            "Forbidden: user_id=%s role=%s"
            " attempted superuser-only action.",
            user.user_id,
            user.role,
        )
        raise HTTPException(
            status_code=403,
            detail="Superuser role required",
        )
    return user


def require_tier(
    min_tier: str,
) -> Callable[..., UserContext]:
    """Factory that returns a dependency enforcing *min_tier*.

    Args:
        min_tier: Minimum subscription tier required
            (``"free"``, ``"pro"``, or ``"premium"``).

    Returns:
        A FastAPI-compatible dependency function.

    Example:
        >>> dep = require_tier("pro")
        >>> callable(dep)
        True
    """
    from subscription_config import TIER_ORDER

    required_level = TIER_ORDER.get(min_tier, 0)

    def _guard(
        user: UserContext = Depends(get_current_user),
    ) -> UserContext:
        user_level = TIER_ORDER.get(
            user.subscription_tier, 0,
        )
        if user_level < required_level:
            logger.warning(
                "Tier guard: user_id=%s tier=%s"
                " < required=%s",
                user.user_id,
                user.subscription_tier,
                min_tier,
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This feature requires the"
                    f" {min_tier} tier or above"
                ),
            )
        return user

    return _guard


def check_usage_quota(
    user: UserContext = Depends(get_current_user),
) -> UserContext:
    """Reject if the user's monthly analysis quota is used up.

    Reads ``usage_remaining`` from the JWT. Returns 429
    when the quota is exhausted (``usage_remaining == 0``).
    Premium users (``usage_remaining is None``) are always
    allowed through.

    Args:
        user: Authenticated user context with subscription
            claims from the JWT.

    Returns:
        The :class:`~auth.models.UserContext` if quota allows.

    Raises:
        HTTPException: 429 if usage quota is exhausted.
    """
    remaining = user.usage_remaining
    if remaining is not None and remaining <= 0:
        logger.warning(
            "Quota exceeded: user_id=%s tier=%s",
            user.user_id,
            user.subscription_tier,
        )
        raise HTTPException(
            status_code=429,
            detail=(
                "Monthly analysis quota exceeded."
                " Upgrade your plan for more."
            ),
        )
    return user
