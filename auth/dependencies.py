"""FastAPI dependency functions for JWT auth and RBAC.

This module exposes two FastAPI ``Depends``-compatible functions:

- :func:`get_current_user` — decodes the Bearer token and returns a
  :class:`~auth.models.UserContext`.  Use this on any endpoint that requires
  authentication.

- :func:`superuser_only` — extends :func:`get_current_user` and additionally
  verifies that the caller has the ``"superuser"`` role.  Use this on
  admin-only endpoints.

The :class:`~auth.service.AuthService` singleton is created lazily on first
call from environment variables.  The singleton persists for the process
lifetime so the in-memory refresh-token deny-list works correctly across
requests.

Usage in a FastAPI route::

    from fastapi import APIRouter, Depends
    from auth.dependencies import get_current_user, superuser_only
    from auth.models import UserContext

    router = APIRouter()

    @router.get("/me")
    def me(user: UserContext = Depends(get_current_user)):
        return {"user_id": user.user_id}

    @router.get("/admin")
    def admin(user: UserContext = Depends(superuser_only)):
        return {"role": user.role}
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

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
        raise HTTPException(status_code=401, detail="Malformed token payload")

    logger.debug("Authenticated user_id=%s role=%s", user_id, role)
    return UserContext(user_id=user_id, email=email or "", role=role)


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
            "Forbidden: user_id=%s role=%s attempted superuser-only action.",
            user.user_id,
            user.role,
        )
        raise HTTPException(status_code=403, detail="Superuser role required")
    return user
