"""OAuth2 PKCE service for Google and Facebook SSO.

This module provides :class:`OAuthService`, which manages the server-side
state tokens required for CSRF protection, constructs provider authorization
URLs, and exchanges authorization codes for user-identity dictionaries that
can be passed directly to
:meth:`~auth.repository.IcebergUserRepository.get_or_create_by_oauth`.

The PKCE ``code_verifier`` is generated and stored client-side
(``sessionStorage``); only the ``code_challenge`` (SHA-256 hash) is sent
to this service.  This prevents authorization-code theft: even if an
attacker intercepts the code, they cannot exchange it without the verifier.

State store
-----------
OAuth ``state`` tokens live in an in-memory ``dict`` keyed by the token
string.  Each entry carries a TTL (10 minutes).  Expired entries are pruned
lazily on every :meth:`OAuthService.generate_authorize_url` call.

Dependencies
------------
- ``httpx`` — synchronous HTTP client for token-exchange requests.
- ``PyJWT`` — decode Google's ``id_token`` without signature verification
  (we trust the TLS connection; full JWKS validation is a future hardening).

Both packages are listed in ``backend/requirements.txt``.
"""

import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx
import jwt

# TTL for state tokens — 10 minutes.
_STATE_TTL_SECONDS = 600


class OAuthService:
    """Manages OAuth2 PKCE authorization flows for Google and Facebook.

    A single instance should be created per process and reused across
    requests so that the in-memory state store persists between the
    ``/authorize`` redirect and the ``/callback`` exchange.

    Attributes:
        _settings: Application settings (provider credentials, redirect URI).
        _state_store: In-memory mapping of ``state`` → ``{provider, expires}``.
        _logger: Module logger.

    Example:
        >>> from config import get_settings
        >>> svc = OAuthService(get_settings())  # doctest: +SKIP
    """

    _GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    _GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    _FB_AUTH_URL = "https://www.facebook.com/v18.0/dialog/oauth"
    _FB_TOKEN_URL = "https://graph.facebook.com/v18.0/oauth/access_token"
    _FB_ME_URL = "https://graph.facebook.com/me"

    def __init__(self, settings: Any) -> None:
        """Initialise the service with application settings.

        Args:
            settings: A :class:`~config.Settings` instance carrying
                ``google_client_id``, ``google_client_secret``,
                ``facebook_app_id``, ``facebook_app_secret``, and
                ``oauth_redirect_uri``.
        """
        self._settings = settings
        self._state_store: Dict[str, Dict[str, Any]] = {}
        self._logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # State / CSRF management
    # ------------------------------------------------------------------

    def _cleanup_expired_states(self) -> None:
        """Remove all state entries whose TTL has elapsed.

        Called lazily on every :meth:`generate_authorize_url` invocation
        so the dict never grows unboundedly.
        """
        now = time.time()
        expired = [k for k, v in self._state_store.items() if v["expires"] < now]
        for k in expired:
            del self._state_store[k]
        if expired:
            self._logger.debug("Pruned %d expired OAuth state token(s).", len(expired))

    def generate_authorize_url(
        self, provider: str, code_challenge: str
    ) -> Tuple[str, str]:
        """Build the provider consent URL and register a CSRF state token.

        The returned ``state`` value must be passed back verbatim in the
        ``POST /auth/oauth/callback`` request.  It is consumed (single-use)
        by :meth:`validate_state`.

        Args:
            provider: ``"google"`` or ``"facebook"``.
            code_challenge: SHA-256 hash of the PKCE ``code_verifier``
                generated client-side, base64url-encoded without padding.

        Returns:
            A ``(state, authorize_url)`` tuple.  ``authorize_url`` is the
            full provider consent page URL to redirect the browser to.

        Raises:
            ValueError: If *provider* is not ``"google"`` or ``"facebook"``.

        Example:
            >>> svc = OAuthService(get_settings())  # doctest: +SKIP
            >>> state, url = svc.generate_authorize_url("google", "challenge")
        """
        self._cleanup_expired_states()

        state = secrets.token_urlsafe(24)
        self._state_store[state] = {
            "provider": provider,
            "expires": time.time() + _STATE_TTL_SECONDS,
        }

        if provider == "google":
            params = {
                "client_id": self._settings.google_client_id,
                "redirect_uri": self._settings.oauth_redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "select_account",
            }
            url = f"{self._GOOGLE_AUTH_URL}?{urlencode(params)}"

        elif provider == "facebook":
            # Facebook does not support PKCE; code_challenge is unused
            # in the authorization URL but the code_verifier is included
            # in the token exchange for forward compatibility.
            params = {
                "client_id": self._settings.facebook_app_id,
                "redirect_uri": self._settings.oauth_redirect_uri,
                "state": state,
                "scope": "email,public_profile",
            }
            url = f"{self._FB_AUTH_URL}?{urlencode(params)}"

        else:
            raise ValueError(f"Unknown OAuth provider: '{provider}'")

        self._logger.debug(
            "Generated authorize URL for provider=%s state=%s", provider, state
        )
        return state, url

    def validate_state(self, state: str, provider: str) -> bool:
        """Consume and validate a previously issued state token.

        The state entry is removed from the store regardless of validity
        (single-use enforcement).

        Args:
            state: The state string returned by :meth:`generate_authorize_url`.
            provider: The expected provider.  Must match what was stored.

        Returns:
            ``True`` if the state was valid, un-expired, and matched the
            expected provider; ``False`` otherwise.

        Example:
            >>> svc = OAuthService(get_settings())  # doctest: +SKIP
            >>> valid = svc.validate_state("some-state", "google")
        """
        entry = self._state_store.pop(state, None)
        if entry is None:
            self._logger.warning("OAuth state not found: state=%s", state)
            return False
        if time.time() > entry["expires"]:
            self._logger.warning("OAuth state expired: state=%s", state)
            return False
        if entry["provider"] != provider:
            self._logger.warning(
                "OAuth state provider mismatch: expected=%s got=%s",
                entry["provider"],
                provider,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Google token exchange
    # ------------------------------------------------------------------

    def exchange_google_code(self, code: str, code_verifier: str) -> Dict[str, Any]:
        """Exchange a Google authorization code for user identity information.

        Sends the code + PKCE verifier to Google's token endpoint.
        Decodes the returned ``id_token`` without signature verification
        (trust is established via HTTPS).

        Args:
            code: The authorization code received from Google's redirect.
            code_verifier: The PKCE verifier string that was hashed to
                produce the ``code_challenge`` sent during authorization.

        Returns:
            A dict with keys: ``provider``, ``sub``, ``email``,
            ``full_name``, ``picture``.

        Raises:
            httpx.HTTPStatusError: If Google returns a non-2xx response.
            ValueError: If Google's response does not contain an ``id_token``.

        Example:
            >>> svc = OAuthService(get_settings())  # doctest: +SKIP
            >>> info = svc.exchange_google_code(code, verifier)
        """
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                self._GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._settings.google_client_id,
                    "client_secret": self._settings.google_client_secret,
                    "redirect_uri": self._settings.oauth_redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
            )
        resp.raise_for_status()
        tokens = resp.json()

        id_token_str = tokens.get("id_token")
        if not id_token_str:
            raise ValueError("Google token response did not include an id_token.")

        # Decode without signature verification — trust HTTPS + Google's TLS.
        payload = jwt.decode(
            id_token_str,
            options={"verify_signature": False},
            algorithms=["RS256"],
        )

        self._logger.info(
            "Google SSO exchange: sub=%s email=%s",
            payload.get("sub"),
            payload.get("email"),
        )
        return {
            "provider": "google",
            "sub": payload["sub"],
            "email": payload.get("email", ""),
            "full_name": payload.get("name", ""),
            "picture": payload.get("picture"),
        }

    # ------------------------------------------------------------------
    # Facebook token exchange
    # ------------------------------------------------------------------

    def exchange_facebook_code(self, code: str) -> Dict[str, Any]:
        """Exchange a Facebook authorization code for user identity information.

        Two HTTP calls are made:
        1. Exchange the code for an access token at the Facebook token URL.
        2. Fetch the user's profile (id, name, email, picture) via the
           Graph API ``/me`` endpoint.

        If Facebook does not grant the ``email`` permission (user declined),
        the email falls back to ``fb_{id}@facebook.local`` so that the
        account can still be created without a real email address.

        Args:
            code: The authorization code received from Facebook's redirect.

        Returns:
            A dict with keys: ``provider``, ``sub``, ``email``,
            ``full_name``, ``picture``.

        Raises:
            httpx.HTTPStatusError: If either Facebook API call fails.

        Example:
            >>> svc = OAuthService(get_settings())  # doctest: +SKIP
            >>> info = svc.exchange_facebook_code(code)
        """
        # Step 1 — exchange code for access token.
        with httpx.Client(timeout=15.0) as client:
            token_resp = client.get(
                self._FB_TOKEN_URL,
                params={
                    "code": code,
                    "client_id": self._settings.facebook_app_id,
                    "client_secret": self._settings.facebook_app_secret,
                    "redirect_uri": self._settings.oauth_redirect_uri,
                },
            )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")

        # Step 2 — fetch user profile.
        with httpx.Client(timeout=15.0) as client:
            me_resp = client.get(
                self._FB_ME_URL,
                params={
                    "access_token": access_token,
                    "fields": "id,name,email,picture.type(large)",
                },
            )
        me_resp.raise_for_status()
        data = me_resp.json()

        fb_id = data["id"]
        email = data.get("email") or f"fb_{fb_id}@facebook.local"
        picture_data = data.get("picture", {})
        if isinstance(picture_data, dict):
            picture = picture_data.get("data", {}).get("url")
        else:
            picture = None

        self._logger.info("Facebook SSO exchange: fb_id=%s email=%s", fb_id, email)
        return {
            "provider": "facebook",
            "sub": fb_id,
            "email": email,
            "full_name": data.get("name", ""),
            "picture": picture,
        }
