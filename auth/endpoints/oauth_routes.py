"""OAuth/SSO endpoint registrations.

Functions
---------
- :func:`register` — attach OAuth routes to the router
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_auth_service
from auth.endpoints.auth_routes import _set_refresh_cookie
from auth.models import (
    OAuthAuthorizeResponse,
    OAuthCallbackRequest,
    OAuthProvider,
    TokenResponse,
)
from auth.rate_limit import limiter, oauth_limit
from auth.service import AuthService

# Module-level logger; cannot be moved into a class
# as this module uses plain functions.
_logger = logging.getLogger(__name__)


def register(router: APIRouter) -> None:
    """Register OAuth/SSO provider, authorize, and callback routes.

    Args:
        router: The :class:`~fastapi.APIRouter` to attach routes to.
    """

    @router.get("/auth/oauth/providers", tags=["oauth"])
    def list_oauth_providers() -> Dict[str, List[str]]:
        """List OAuth providers that are currently enabled.

        Returns:
            A dict ``{"providers": ["google", "facebook"]}`` listing only
            providers with non-empty credentials configured.
        """
        from config import get_settings

        settings = get_settings()
        providers: List[str] = []
        if settings.google_client_id:
            providers.append(OAuthProvider.google.value)
        if settings.facebook_app_id:
            providers.append(OAuthProvider.facebook.value)
        return {"providers": providers}

    @router.get(
        "/auth/oauth/{provider}/authorize",
        response_model=OAuthAuthorizeResponse,
        tags=["oauth"],
    )
    def oauth_authorize(
        provider: str, code_challenge: str
    ) -> OAuthAuthorizeResponse:
        """Generate a provider consent URL and a server-side CSRF state token.

        Args:
            provider: OAuth provider — ``"google"`` or ``"facebook"``.
            code_challenge: PKCE challenge (base64url SHA-256 of the verifier).

        Returns:
            :class:`~auth.models.OAuthAuthorizeResponse`
            with ``state`` and ``authorize_url``.

        Raises:
            HTTPException: 400 if *provider* is not supported.
            HTTPException: 503 if provider credentials are not configured.
        """
        try:
            provider_enum = OAuthProvider(provider)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Unsupported OAuth provider: '{}'".format(provider),
            )
        from config import get_settings

        settings = get_settings()
        if (
            provider_enum == OAuthProvider.google
            and not settings.google_client_id
        ):
            raise HTTPException(
                status_code=503, detail="Google SSO is not configured."
            )
        if (
            provider_enum == OAuthProvider.facebook
            and not settings.facebook_app_id
        ):
            raise HTTPException(
                status_code=503, detail="Facebook SSO is not configured."
            )
        oauth_svc = _helpers._get_oauth_svc()
        try:
            state, authorize_url = oauth_svc.generate_authorize_url(
                provider_enum.value, code_challenge
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        _logger.info("OAuth authorize: provider=%s", provider)
        return OAuthAuthorizeResponse(state=state, authorize_url=authorize_url)

    @router.post(
        "/auth/oauth/callback", response_model=TokenResponse, tags=["oauth"]
    )
    @limiter.limit(oauth_limit)
    def oauth_callback(
        request: Request,
        body: OAuthCallbackRequest,
        service: AuthService = Depends(get_auth_service),
    ) -> TokenResponse:
        """Exchange an OAuth authorization code for our own JWT pair.

        Args:
            body: :class:`~auth.models.OAuthCallbackRequest`.
            service: Injected :class:`~auth.service.AuthService`.

        Returns:
            A :class:`~auth.models.TokenResponse`.

        Raises:
            HTTPException: 400 if the state or code exchange fails.
            HTTPException: 403 if the resulting user is deactivated.
        """
        oauth_svc = _helpers._get_oauth_svc()
        repo = _helpers._get_repo()
        if not oauth_svc.validate_state(body.state, body.provider.value):
            _logger.warning(
                "Invalid OAuth state token: provider=%s", body.provider
            )
            raise HTTPException(
                status_code=400, detail="Invalid or expired OAuth state token."
            )
        try:
            if body.provider == OAuthProvider.google:
                user_info = oauth_svc.exchange_google_code(
                    body.code, body.code_verifier or ""
                )
            else:
                user_info = oauth_svc.exchange_facebook_code(body.code)
        except Exception as exc:
            _logger.error(
                "OAuth code exchange failed: provider=%s error=%s",
                body.provider,
                exc,
            )
            raise HTTPException(
                status_code=400,
                detail="OAuth token exchange failed. Please try again.",
            )
        user = repo.get_or_create_by_oauth(
            provider=user_info["provider"],
            oauth_sub=user_info["sub"],
            email=user_info["email"],
            full_name=user_info["full_name"],
            picture_url=user_info.get("picture"),
        )
        if not user.get("is_active", False):
            raise HTTPException(
                status_code=403, detail="Account is deactivated."
            )
        repo.update(
            user["user_id"], {"last_login_at": datetime.now(timezone.utc)}
        )
        access = service.create_access_token(
            user_id=user["user_id"],
            email=user["email"],
            role=user["role"],
        )
        refresh = service.create_refresh_token(
            user_id=user["user_id"],
        )
        repo.append_audit_event(
            "OAUTH_LOGIN",
            actor_user_id=user["user_id"],
            target_user_id=user["user_id"],
            metadata={
                "provider": body.provider.value,
                "email": user["email"],
            },
        )
        service.register_session(
            user_id=user["user_id"],
            refresh_token=refresh,
            ip_address=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", ""),
        )
        _logger.info(
            "OAuth login: user_id=%s provider=%s",
            user["user_id"],
            body.provider,
        )
        resp = JSONResponse(
            content=TokenResponse(
                access_token=access,
                refresh_token=refresh,
            ).model_dump(),
        )
        _set_refresh_cookie(resp, refresh)
        return resp
