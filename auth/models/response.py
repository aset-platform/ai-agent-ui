"""Response-body Pydantic models for auth and user-management endpoints.

Defines all HTTP response bodies returned by the ``/auth`` and ``/users``
routes.

Models
------
- :class:`TokenResponse`
- :class:`UserContext`
- :class:`UserResponse`
- :class:`OAuthAuthorizeResponse`
"""

from typing import Dict, Optional

from pydantic import BaseModel

from auth.models.request import OAuthProvider  # noqa: F401 — re-exported


class TokenResponse(BaseModel):
    """JWT pair returned on successful authentication or token refresh.

    Attributes:
        access_token: Short-lived JWT (default 60 minutes).
        refresh_token: Long-lived JWT (default 7 days).
        token_type: Always ``"bearer"``.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserContext(BaseModel):
    """Decoded JWT payload injected by the :func:`~auth.dependencies.get_current_user` dependency.

    Attributes:
        user_id: UUID string of the authenticated user.
        email: Email address extracted from the JWT payload.
        role: Either ``"superuser"`` or ``"general"``.
    """

    user_id: str
    email: str
    role: str


class UserResponse(BaseModel):
    """Public user representation (no password hash exposed).

    Attributes:
        user_id: UUID string.
        email: Email address.
        full_name: Display name.
        role: ``"superuser"`` or ``"general"``.
        is_active: Whether the account is active.
        created_at: ISO-8601 UTC creation timestamp, or ``None``.
        updated_at: ISO-8601 UTC last-modified timestamp, or ``None``.
        last_login_at: ISO-8601 UTC most-recent login, or ``None``.
        avatar_url: URL of the user's profile picture, or ``None``.
        page_permissions: Per-page access grants for non-superuser accounts, or ``None``.
    """

    user_id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_login_at: Optional[str] = None
    avatar_url: Optional[str] = None
    page_permissions: Optional[Dict[str, bool]] = None


class OAuthAuthorizeResponse(BaseModel):
    """Response body for ``GET /auth/oauth/{provider}/authorize``.

    Attributes:
        state: CSRF state token generated server-side.
        authorize_url: Full provider consent URL to redirect the browser to.
    """

    state: str
    authorize_url: str
