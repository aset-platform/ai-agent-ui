"""Request-body Pydantic models for auth and user-management endpoints.

Defines all HTTP request bodies consumed by the ``/auth`` and ``/users``
routes.

Models
------
- :class:`LoginRequest`
- :class:`UserCreateRequest`
- :class:`UserUpdateRequest`
- :class:`AdminPasswordResetBody`
- :class:`PasswordResetRequestBody`
- :class:`PasswordResetConfirmBody`
- :class:`RefreshRequest`
- :class:`LogoutRequest`
- :class:`OAuthProvider`
- :class:`OAuthCallbackRequest`
"""

from enum import Enum
from typing import Dict, Literal

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Credentials submitted to ``POST /auth/login``.

    Attributes:
        email: The user's registered email address.
        password: The plaintext password (never stored).
    """

    email: EmailStr
    password: str = Field(..., max_length=128)


class UserCreateRequest(BaseModel):
    """Request body for ``POST /users`` (superuser only).

    Attributes:
        email: Email address for the new account.  Must be unique.
        password: Plaintext initial password (min 8 chars, one digit).
        full_name: Display name shown in the UI.
        role: Account role — ``"superuser"`` or ``"general"``.
    """

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=200)
    role: Literal["general", "superuser"] = "general"


class UserUpdateRequest(BaseModel):
    """Request body for ``PATCH /users/{user_id}`` (superuser only).

    All fields are optional — only supplied fields are updated.

    Attributes:
        full_name: New display name.
        email: New email address.  Must be unique.
        role: New role — ``"superuser"`` or ``"general"``.
        is_active: Set to ``False`` to deactivate the account.
    """

    full_name: str | None = Field(None, max_length=200)
    email: EmailStr | None = None
    role: Literal["general", "superuser"] | None = None
    is_active: bool | None = None
    page_permissions: Dict[str, bool] | None = None


class ProfileUpdateRequest(BaseModel):
    """Request body for ``PATCH /auth/me`` (self-service profile update).

    Attributes:
        full_name: New display name.
        avatar_url: New avatar URL (set by upload-avatar endpoint).
    """

    full_name: str | None = Field(None, max_length=200)
    avatar_url: str | None = Field(None, max_length=500)


class AdminPasswordResetBody(BaseModel):
    """Request body for ``POST /users/{user_id}/reset-password``.

    Superuser-only endpoint to set a new password for any user.

    Attributes:
        new_password: The new plaintext password (min 8 chars).
    """

    new_password: str = Field(..., min_length=8, max_length=128)


class PasswordResetRequestBody(BaseModel):
    """Request body for ``POST /auth/password-reset/request``.

    Attributes:
        email: Email of the account whose password should be reset.
    """

    email: EmailStr


class PasswordResetConfirmBody(BaseModel):
    """Request body for ``POST /auth/password-reset/confirm``.

    Attributes:
        reset_token: The single-use token previously issued.
        new_password: The new plaintext password (min 8 chars, one digit).
    """

    reset_token: str = Field(..., max_length=500)
    new_password: str = Field(..., min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    """Request body for ``POST /auth/refresh``.

    Attributes:
        refresh_token: The long-lived refresh token issued
            at login.
    """

    refresh_token: str = Field(..., max_length=2000)


class LogoutRequest(BaseModel):
    """Request body for ``POST /auth/logout``.

    Attributes:
        refresh_token: The refresh token to invalidate
            server-side.
    """

    refresh_token: str = Field(..., max_length=2000)


class OAuthProvider(str, Enum):
    """Supported OAuth2 SSO providers.

    Attributes:
        google: Google OAuth2 (OpenID Connect).
        facebook: Facebook OAuth2 (Graph API).
    """

    google = "google"
    facebook = "facebook"


class OAuthCallbackRequest(BaseModel):
    """Request body for ``POST /auth/oauth/callback``.

    Attributes:
        provider: The OAuth provider that issued the code.
        code: Authorization code from the provider's redirect.
        state: The CSRF state token originally returned by authorize.
        code_verifier: PKCE code verifier (required for Google).
    """

    provider: OAuthProvider
    code: str = Field(..., max_length=2000)
    state: str = Field(..., max_length=500)
    code_verifier: str | None = Field(None, max_length=500)
