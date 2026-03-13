"""Authentication service — thin facade over auth.password and auth.tokens.

:class:`AuthService` delegates cryptographic operations to
:mod:`auth.password` and :mod:`auth.tokens`.  Token revocation is
backed by a pluggable :class:`~auth.token_store.TokenStore` (Redis
or in-memory).

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

import logging
from typing import Any, Dict

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
    ) -> str:
        """Delegate to :func:`auth.tokens.create_access_token`.

        Args:
            user_id: UUID string of the authenticated user.
            email: Email address to embed in the token.
            role: User role.

        Returns:
            A signed JWT string.
        """
        return _tk.create_access_token(
            user_id,
            email,
            role,
            self._secret_key,
            self._access_expire_minutes,
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
