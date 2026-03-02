"""Pydantic request and response models for the auth module.

All HTTP request/response bodies for the ``/auth`` and ``/users`` endpoints
are re-exported from this package so callers can continue using::

    from auth.models import LoginRequest, TokenResponse, ...
"""

import logging

from auth.models.request import (
    LoginRequest,
    LogoutRequest,
    OAuthCallbackRequest,
    OAuthProvider,
    PasswordResetConfirmBody,
    PasswordResetRequestBody,
    ProfileUpdateRequest,
    RefreshRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
from auth.models.response import (
    OAuthAuthorizeResponse,
    TokenResponse,
    UserContext,
    UserResponse,
)

logger = logging.getLogger(__name__)

# Module-level export list — kept at module scope as required by Python's import machinery.
_all_exports = [
    "LoginRequest",
    "LogoutRequest",
    "OAuthCallbackRequest",
    "OAuthProvider",
    "PasswordResetConfirmBody",
    "PasswordResetRequestBody",
    "ProfileUpdateRequest",
    "RefreshRequest",
    "UserCreateRequest",
    "UserUpdateRequest",
    "OAuthAuthorizeResponse",
    "TokenResponse",
    "UserContext",
    "UserResponse",
]

__all__ = _all_exports
