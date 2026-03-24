"""Authentication service — thin facade over auth.password and auth.tokens.

:class:`AuthService` delegates cryptographic operations to
:mod:`auth.password` and :mod:`auth.tokens`.  Token revocation is
backed by a pluggable :class:`~auth.token_store.TokenStore` (Redis
or in-memory).

Session management is layered on the same token store using
``session:{user_id}:{jti}`` keys with JSON metadata (IP address,
user-agent, timestamps).

Usage::

    from auth.token_store import InMemoryTokenStore

    store = InMemoryTokenStore()
    service = AuthService(
        secret_key="your-32-char-random-secret",
        access_expire_minutes=60,
        refresh_expire_days=7,
        token_store=store,
    )
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

import auth.password as _pw
import auth.tokens as _tk
from auth.token_store import InMemoryTokenStore, TokenStore

_logger = logging.getLogger(__name__)


class AuthService:
    """Stateful auth facade for JWT lifecycle and passwords.

    One instance should be created per process and reused across
    all requests.

    Attributes:
        _secret_key: HMAC secret for signing/verifying JWTs.
        _access_expire_minutes: Access token lifetime (minutes).
        _refresh_expire_days: Refresh token lifetime (days).
        _store: Pluggable deny-list backend.
    """

    def __init__(
        self,
        secret_key: str,
        access_expire_minutes: int = 60,
        refresh_expire_days: int = 7,
        token_store: TokenStore | None = None,
    ) -> None:
        """Initialise the service with signing credentials.

        Args:
            secret_key: HMAC-SHA256 secret (>= 32 characters).
            access_expire_minutes: Access token lifetime.
            refresh_expire_days: Refresh token lifetime.
            token_store: Pluggable deny-list backend.  Defaults
                to :class:`InMemoryTokenStore` if ``None``.

        Raises:
            ValueError: If *secret_key* is shorter than 32 chars.
        """
        if not secret_key or len(secret_key) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters."
                " Generate one with: python -c"
                ' "import secrets;'
                ' print(secrets.token_hex(32))"'
            )
        self._secret_key = secret_key
        self._access_expire_minutes = access_expire_minutes
        self._refresh_expire_days = refresh_expire_days
        self._store: TokenStore = token_store or InMemoryTokenStore()
        self._logger = logging.getLogger(__name__)
        self._logger.info(
            "AuthService initialised (access_ttl=%dm,"
            " refresh_ttl=%dd, store=%s).",
            access_expire_minutes,
            refresh_expire_days,
            type(self._store).__name__,
        )

    # ----------------------------------------------------------
    # Store health
    # ----------------------------------------------------------

    def store_health(self) -> Dict[str, object]:
        """Return token-store backend type and ping status.

        Returns:
            A dict with ``backend`` (class name) and ``ok``
            (``True`` when the store is reachable) keys.
        """
        return {
            "backend": type(self._store).__name__,
            "ok": self._store.ping(),
        }

    # ----------------------------------------------------------
    # Password helpers
    # ----------------------------------------------------------

    def hash_password(self, plain: str) -> str:
        """Delegate to :func:`auth.password.hash_password`.

        Args:
            plain: The plaintext password.

        Returns:
            A bcrypt hash string.
        """
        return _pw.hash_password(plain)

    def verify_password(self, plain: str, hashed: str) -> bool:
        """Delegate to :func:`auth.password.verify_password`.

        Args:
            plain: The plaintext password.
            hashed: The bcrypt hash.

        Returns:
            ``True`` if the password matches.
        """
        return _pw.verify_password(plain, hashed)

    @staticmethod
    def validate_password_strength(password: str) -> None:
        """Delegate to :func:`auth.password.validate_password_strength`.

        Args:
            password: The plaintext password to validate.

        Raises:
            HTTPException: 400 if the password is too weak.
        """
        _pw.validate_password_strength(password)

    # ----------------------------------------------------------
    # Token creation
    # ----------------------------------------------------------

    def create_access_token(
        self,
        user_id: str,
        email: str,
        role: str,
        subscription_tier: str = "free",
        subscription_status: str = "active",
        usage_remaining: int | None = None,
    ) -> str:
        """Delegate to :func:`auth.tokens.create_access_token`.

        Args:
            user_id: UUID string of the authenticated user.
            email: Email address to embed in the token.
            role: User role.
            subscription_tier: ``"free"``, ``"pro"``, or
                ``"premium"``.
            subscription_status: Subscription state.
            usage_remaining: Analyses left this month.

        Returns:
            A signed JWT string.
        """
        return _tk.create_access_token(
            user_id,
            email,
            role,
            self._secret_key,
            self._access_expire_minutes,
            subscription_tier=subscription_tier,
            subscription_status=subscription_status,
            usage_remaining=usage_remaining,
        )

    def create_refresh_token(self, user_id: str) -> str:
        """Delegate to :func:`auth.tokens.create_refresh_token`.

        Args:
            user_id: UUID string of the authenticated user.

        Returns:
            A signed JWT string.
        """
        return _tk.create_refresh_token(
            user_id,
            self._secret_key,
            self._refresh_expire_days,
        )

    # ----------------------------------------------------------
    # Token validation / revocation
    # ----------------------------------------------------------

    def decode_token(
        self,
        token: str,
        expected_type: str | None = None,
    ) -> Dict[str, Any]:
        """Decode and validate a JWT.

        Args:
            token: The raw JWT string.
            expected_type: If provided, the decoded ``type``
                claim must match.

        Returns:
            The decoded payload dict.

        Raises:
            HTTPException: 401 on any validation failure.
        """
        return _tk.decode_token(
            token,
            self._secret_key,
            self._store,
            expected_type,
        )

    def revoke_refresh_token(self, token: str) -> None:
        """Revoke a refresh token via the token store.

        Args:
            token: The raw refresh JWT string to revoke.
        """
        _tk.revoke_refresh_token(
            token,
            self._secret_key,
            self._store,
            self._refresh_expire_days,
        )

    def is_token_revoked(self, token: str) -> bool:
        """Check whether a token's JTI is revoked.

        Args:
            token: The raw JWT string to check.

        Returns:
            ``True`` if the token has been revoked.
        """
        return _tk.is_token_revoked(
            token,
            self._secret_key,
            self._store,
        )

    # ----------------------------------------------------------
    # Session management
    # ----------------------------------------------------------

    def _session_key(self, user_id: str, jti: str) -> str:
        """Build the session store key.

        Args:
            user_id: UUID string.
            jti: Refresh token JTI.

        Returns:
            The store key ``session:{user_id}:{jti}``.
        """
        return f"session:{user_id}:{jti}"

    def register_session(
        self,
        user_id: str,
        refresh_token: str,
        ip_address: str = "",
        user_agent: str = "",
    ) -> None:
        """Record a new session tied to a refresh token.

        Args:
            user_id: UUID string of the authenticated user.
            refresh_token: The raw refresh JWT string.
            ip_address: Client IP address.
            user_agent: Client User-Agent header.
        """
        from jose import jwt as _jose_jwt

        try:
            payload = _jose_jwt.decode(
                refresh_token,
                self._secret_key,
                algorithms=["HS256"],
            )
        except Exception:
            return
        jti = payload.get("jti", "")
        if not jti:
            return
        now = datetime.now(timezone.utc).isoformat()
        meta = json.dumps(
            {
                "session_id": jti,
                "user_id": user_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "created_at": now,
                "last_activity_at": now,
            }
        )
        ttl = self._refresh_expire_days * 86400
        key = self._session_key(user_id, jti)
        self._store.add_json(key, meta, ttl)
        self._logger.debug(
            "Session registered: user_id=%s jti=%s",
            user_id,
            jti,
        )

    def list_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """Return all active sessions for a user.

        Args:
            user_id: UUID string.

        Returns:
            A list of session metadata dicts.
        """
        prefix = f"session:{user_id}:"
        keys = self._store.keys_by_prefix(prefix)
        sessions: List[Dict[str, Any]] = []
        for k in keys:
            raw = self._store.get_json(k)
            if raw:
                try:
                    sessions.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        return sessions

    def revoke_session(self, user_id: str, session_id: str) -> bool:
        """Revoke a single session by its session_id (JTI).

        Adds the JTI to the deny-list and removes the
        session metadata.

        Args:
            user_id: UUID string.
            session_id: The JTI of the refresh token.

        Returns:
            ``True`` if the session was found and revoked.
        """
        key = self._session_key(user_id, session_id)
        raw = self._store.get_json(key)
        if not raw:
            return False
        ttl = self._refresh_expire_days * 86400
        self._store.add(session_id, ttl)
        self._store.remove(key)
        self._logger.info(
            "Session revoked: user_id=%s jti=%s",
            user_id,
            session_id,
        )
        return True

    def revoke_all_sessions(self, user_id: str) -> int:
        """Revoke all sessions for a user.

        Args:
            user_id: UUID string.

        Returns:
            Number of sessions revoked.
        """
        prefix = f"session:{user_id}:"
        keys = self._store.keys_by_prefix(prefix)
        count = 0
        ttl = self._refresh_expire_days * 86400
        for k in keys:
            raw = self._store.get_json(k)
            if raw:
                try:
                    meta = json.loads(raw)
                    jti = meta.get("session_id", "")
                    if jti:
                        self._store.add(jti, ttl)
                except json.JSONDecodeError:
                    pass
            self._store.remove(k)
            count += 1
        self._logger.info(
            "All sessions revoked: user_id=%s count=%d",
            user_id,
            count,
        )
        return count
