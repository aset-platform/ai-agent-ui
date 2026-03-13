"""Login, refresh, logout, and password-reset endpoint registrations.

Functions
---------
- :func:`register` — attach auth routes to the router
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_auth_service, get_current_user
from auth.models import (
    LoginRequest,
    LogoutRequest,
    PasswordResetConfirmBody,
    PasswordResetRequestBody,
    RefreshRequest,
    TokenResponse,
    UserContext,
)
from auth.rate_limit import limiter, login_limit, register_limit
from auth.service import AuthService

# Module-level logger; mutable but intentionally
# module-scoped for consistent log attribution.
_logger = logging.getLogger(__name__)

# Cookie config for the HttpOnly refresh token.
_COOKIE_KEY = "refresh_token"
_COOKIE_PATH = "/auth"
_COOKIE_MAX_AGE = 7 * 24 * 3600  # 7 days


def _set_refresh_cookie(
    response: JSONResponse,
    refresh_token: str,
) -> None:
    """Attach the refresh token as an HttpOnly cookie.

    Args:
        response: The outgoing response.
        refresh_token: JWT refresh token string.
    """
    response.set_cookie(
        key=_COOKIE_KEY,
        value=refresh_token,
        httponly=True,
        secure=False,  # True in production (HTTPS)
        samesite="lax",
        path=_COOKIE_PATH,
        max_age=_COOKIE_MAX_AGE,
    )


def _clear_refresh_cookie(response: JSONResponse) -> None:
    """Remove the refresh-token cookie.

    Args:
        response: The outgoing response.
    """
    response.delete_cookie(
        key=_COOKIE_KEY,
        path=_COOKIE_PATH,
    )


def register(router: APIRouter) -> None:
    """Register login, refresh, logout, and password-reset routes.

    Args:
        router: The :class:`~fastapi.APIRouter` to attach routes to.
    """

    @router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
    @limiter.limit(login_limit)
    def login(
        request: Request,
        body: LoginRequest,
        service: AuthService = Depends(get_auth_service),
    ) -> TokenResponse:
        """Authenticate a user and return a JWT access + refresh token pair.

        Args:
            body: Login credentials.
            service: Injected :class:`~auth.service.AuthService`.

        Returns:
            A :class:`~auth.models.TokenResponse`.

        Raises:
            HTTPException: 401 if credentials are invalid
                or account is deactivated.
        """
        repo = _helpers._get_repo()
        user = repo.get_by_email(str(body.email))
        user = _helpers._require_active_user(user, str(body.email))
        if not service.verify_password(body.password, user["hashed_password"]):
            _logger.warning(
                "Login failed for email=%s (wrong password).", body.email
            )
            raise HTTPException(status_code=401, detail="Invalid credentials")
        repo.update(
            user["user_id"], {"last_login_at": datetime.now(timezone.utc)}
        )
        repo.append_audit_event(
            "LOGIN",
            actor_user_id=user["user_id"],
            target_user_id=user["user_id"],
        )
        access = service.create_access_token(
            user_id=user["user_id"],
            email=user["email"],
            role=user["role"],
        )
        refresh = service.create_refresh_token(
            user_id=user["user_id"],
        )
        service.register_session(
            user_id=user["user_id"],
            refresh_token=refresh,
            ip_address=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", ""),
        )
        _logger.info(
            "User logged in: user_id=%s",
            user["user_id"],
        )
        resp = JSONResponse(
            content=TokenResponse(
                access_token=access,
                refresh_token=refresh,
            ).model_dump(),
        )
        _set_refresh_cookie(resp, refresh)
        return resp

    @router.post(
        "/auth/login/form",
        response_model=TokenResponse,
        tags=["auth"],
    )
    def login_form(
        form: OAuth2PasswordRequestForm = Depends(),
        service: AuthService = Depends(get_auth_service),
    ) -> TokenResponse:
        """OAuth2 form-based login for the OpenAPI documentation UI.

        Args:
            form: Form with ``username`` (email) and ``password`` fields.
            service: Injected :class:`~auth.service.AuthService`.

        Returns:
            A :class:`~auth.models.TokenResponse`.

        Raises:
            HTTPException: 401 if credentials are invalid.
        """
        repo = _helpers._get_repo()
        user = repo.get_by_email(form.username)
        user = _helpers._require_active_user(user, form.username)
        if not service.verify_password(form.password, user["hashed_password"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        repo.update(
            user["user_id"], {"last_login_at": datetime.now(timezone.utc)}
        )
        repo.append_audit_event("LOGIN", user["user_id"], user["user_id"])
        access = service.create_access_token(
            user_id=user["user_id"], email=user["email"], role=user["role"]
        )
        refresh = service.create_refresh_token(user_id=user["user_id"])
        return TokenResponse(access_token=access, refresh_token=refresh)

    @router.post(
        "/auth/refresh",
        response_model=TokenResponse,
        tags=["auth"],
    )
    def refresh_token(
        request: Request,
        body: RefreshRequest | None = None,
        service: AuthService = Depends(get_auth_service),
    ):
        """Exchange a valid refresh token for new tokens.

        Reads the refresh token from the HttpOnly cookie
        first, falling back to the request body for backward
        compatibility.

        Args:
            request: The incoming HTTP request.
            body: Optional request body with ``refresh_token``.
            service: Injected :class:`~auth.service.AuthService`.

        Returns:
            A :class:`~auth.models.TokenResponse` with fresh
            tokens.

        Raises:
            HTTPException: 401 if the refresh token is invalid
                or revoked.
        """
        # Prefer cookie, fall back to body.
        token = request.cookies.get(_COOKIE_KEY)
        if not token and body:
            token = body.refresh_token
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Missing refresh token",
            )

        payload = service.decode_token(
            token,
            expected_type="refresh",
        )
        user_id: str = payload["sub"]
        repo = _helpers._get_repo()
        user = repo.get_by_id(user_id)
        if user is None or not user.get("is_active", False):
            raise HTTPException(
                status_code=401,
                detail="User not found or deactivated",
            )
        service.revoke_refresh_token(token)
        access = service.create_access_token(
            user_id=user["user_id"],
            email=user["email"],
            role=user["role"],
        )
        new_refresh = service.create_refresh_token(
            user_id=user["user_id"],
        )
        _logger.info(
            "Token refreshed for user_id=%s",
            user_id,
        )
        resp = JSONResponse(
            content=TokenResponse(
                access_token=access,
                refresh_token=new_refresh,
            ).model_dump(),
        )
        _set_refresh_cookie(resp, new_refresh)
        return resp

    @router.post("/auth/logout", tags=["auth"])
    def logout(
        request: Request,
        body: LogoutRequest | None = None,
        service: AuthService = Depends(get_auth_service),
        current_user: UserContext = Depends(
            get_current_user,
        ),
    ):
        """Invalidate the refresh token and clear the cookie.

        Reads the refresh token from the HttpOnly cookie
        first, falling back to the request body.

        Args:
            request: The incoming HTTP request.
            body: Optional request body with ``refresh_token``.
            service: Injected :class:`~auth.service.AuthService`.
            current_user: Authenticated user context.

        Returns:
            A JSON response with a ``"detail"`` message.
        """
        token = request.cookies.get(_COOKIE_KEY)
        if not token and body:
            token = body.refresh_token
        if token:
            service.revoke_refresh_token(token)
        _logger.info(
            "User logged out: user_id=%s",
            current_user.user_id,
        )
        resp = JSONResponse(
            content={"detail": "Logged out successfully"},
        )
        _clear_refresh_cookie(resp)
        return resp

    @router.post("/auth/password-reset/request", tags=["auth"])
    @limiter.limit(register_limit)
    def password_reset_request(
        request: Request,
        body: PasswordResetRequestBody,
        current_user: UserContext = Depends(get_current_user),
    ) -> Dict[str, str]:
        """Generate a single-use password reset token for the requesting user.

        Args:
            body: Request body with ``email``.
            current_user: Authenticated :class:`~auth.models.UserContext`.

        Returns:
            A dict containing ``"reset_token"`` (development) and ``"detail"``.

        Raises:
            HTTPException: 403 if email does not match caller's email.
            HTTPException: 404 if the user record is not found.
        """
        if str(body.email).lower() != current_user.email.lower():
            raise HTTPException(
                status_code=403, detail="You may only reset your own password."
            )
        repo = _helpers._get_repo()
        user = repo.get_by_id(current_user.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        reset_token = secrets.token_urlsafe(32)
        expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
        repo.update(
            current_user.user_id,
            {
                "password_reset_token": reset_token,
                "password_reset_expiry": expiry,
            },
        )
        repo.append_audit_event(
            "PASSWORD_RESET",
            actor_user_id=current_user.user_id,
            target_user_id=current_user.user_id,
            metadata={"stage": "request"},
        )
        _logger.info(
            "Password reset requested by user_id=%s", current_user.user_id
        )
        return {
            "detail": (
                "Password reset token generated"
                " (development: token included"
                " in response)."
            ),
            "reset_token": reset_token,
        }

    @router.post("/auth/password-reset/confirm", tags=["auth"])
    def password_reset_confirm(
        body: PasswordResetConfirmBody,
        current_user: UserContext = Depends(get_current_user),
        service: AuthService = Depends(get_auth_service),
    ) -> Dict[str, str]:
        """Apply a new password using a previously issued reset token.

        Args:
            body: Request body with ``reset_token`` and ``new_password``.
            current_user: Authenticated :class:`~auth.models.UserContext`.
            service: Injected :class:`~auth.service.AuthService`.

        Returns:
            A dict ``{"detail": "Password updated successfully"}``.

        Raises:
            HTTPException: 400 if the token is invalid or expired.
        """
        AuthService.validate_password_strength(body.new_password)
        repo = _helpers._get_repo()
        user = repo.get_by_id(current_user.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        stored_token = user.get("password_reset_token")
        expiry = user.get("password_reset_expiry")
        if not stored_token or stored_token != body.reset_token:
            raise HTTPException(status_code=400, detail="Invalid reset token")
        if expiry is not None:
            expiry_utc = (
                expiry.replace(tzinfo=timezone.utc)
                if expiry.tzinfo is None
                else expiry
            )
            if datetime.now(timezone.utc) > expiry_utc:
                raise HTTPException(
                    status_code=400, detail="Reset token has expired"
                )
        new_hash = service.hash_password(body.new_password)
        repo.update(
            current_user.user_id,
            {
                "hashed_password": new_hash,
                "password_reset_token": None,
                "password_reset_expiry": None,
            },
        )
        repo.append_audit_event(
            "PASSWORD_RESET",
            actor_user_id=current_user.user_id,
            target_user_id=current_user.user_id,
            metadata={"stage": "confirm"},
        )
        _logger.info(
            "Password reset completed for user_id=%s", current_user.user_id
        )
        return {"detail": "Password updated successfully"}

    @router.get("/auth/health", tags=["auth"])
    def auth_health(
        service: AuthService = Depends(get_auth_service),
    ) -> Dict[str, object]:
        """Return token-store health status.

        Returns:
            A dict with ``status``, ``backend``, and ``ok`` keys.
        """
        health = service.store_health()
        ok = health["ok"]
        return {
            "status": "healthy" if ok else "degraded",
            "backend": health["backend"],
            "ok": ok,
        }
