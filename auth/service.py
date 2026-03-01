"""Authentication service — thin façade over auth.password and auth.tokens.

:class:`AuthService` holds the only stateful piece: the in-memory JWT
deny-list.  All cryptographic operations are delegated to
:mod:`auth.password` and :mod:`auth.tokens`.

Usage::

    service = AuthService(
        secret_key="your-32-char-random-secret",
        access_expire_minutes=60,
        refresh_expire_days=7,
    )
    hashed = service.hash_password("my-secret-password")
    access = service.create_access_token(user_id="...", email="...", role="general")

Note on the deny-list
---------------------
The deny-list lives in memory and is cleared on process restart.  For
multi-server deployments it should be backed by Redis or an Iceberg table.
"""

import logging
from typing import Any, Dict, Optional, Set

import auth.password as _pw
import auth.tokens as _tk

# Module-level logger; mutable only in the sense that logging configuration
# may change at runtime, but the reference itself is fixed after import.
_logger = logging.getLogger(__name__)


class AuthService:
    """Stateful authentication façade for JWT lifecycle and password management.

    One instance should be created per process and reused across all requests.

    Attributes:
        _secret_key: HMAC secret used to sign and verify JWTs.
        _access_expire_minutes: Lifetime of an access token in minutes.
        _refresh_expire_days: Lifetime of a refresh token in days.
        _deny_list: Set of revoked refresh token JTI strings.
    """

    def __init__(
        self,
        secret_key: str,
        access_expire_minutes: int = 60,
        refresh_expire_days: int = 7,
    ) -> None:
        """Initialise the service with signing credentials and TTLs.

        Args:
            secret_key: HMAC-SHA256 secret (at least 32 characters).
            access_expire_minutes: Access token lifetime in minutes.
            refresh_expire_days: Refresh token lifetime in days.

        Raises:
            ValueError: If *secret_key* is empty or shorter than 32 characters.
        """
        if not secret_key or len(secret_key) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        self._secret_key = secret_key
        self._access_expire_minutes = access_expire_minutes
        self._refresh_expire_days = refresh_expire_days
        self._deny_list: Set[str] = set()
        self._logger = logging.getLogger(__name__)
        self._logger.info(
            "AuthService initialised (access_ttl=%dm, refresh_ttl=%dd).",
            access_expire_minutes,
            refresh_expire_days,
        )

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

    def create_access_token(self, user_id: str, email: str, role: str) -> str:
        """Delegate to :func:`auth.tokens.create_access_token`.

        Args:
            user_id: UUID string of the authenticated user.
            email: Email address to embed in the token.
            role: User role.

        Returns:
            A signed JWT string.
        """
        return _tk.create_access_token(
            user_id, email, role, self._secret_key, self._access_expire_minutes
        )

    def create_refresh_token(self, user_id: str) -> str:
        """Delegate to :func:`auth.tokens.create_refresh_token`.

        Args:
            user_id: UUID string of the authenticated user.

        Returns:
            A signed JWT string.
        """
        return _tk.create_refresh_token(user_id, self._secret_key, self._refresh_expire_days)

    def decode_token(self, token: str, expected_type: Optional[str] = None) -> Dict[str, Any]:
        """Delegate to :func:`auth.tokens.decode_token`.

        Args:
            token: The raw JWT string.
            expected_type: If provided, the decoded ``type`` claim must match.

        Returns:
            The decoded payload dict.

        Raises:
            HTTPException: 401 on any validation failure.
        """
        return _tk.decode_token(token, self._secret_key, self._deny_list, expected_type)

    def revoke_refresh_token(self, token: str) -> None:
        """Delegate to :func:`auth.tokens.revoke_refresh_token`.

        Args:
            token: The raw refresh JWT string to revoke.
        """
        _tk.revoke_refresh_token(token, self._secret_key, self._deny_list)

    def is_token_revoked(self, token: str) -> bool:
        """Delegate to :func:`auth.tokens.is_token_revoked`.

        Args:
            token: The raw JWT string to check.

        Returns:
            ``True`` if the token has been revoked.
        """
        return _tk.is_token_revoked(token, self._secret_key, self._deny_list)