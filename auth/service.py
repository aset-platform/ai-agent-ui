"""Authentication service — thin façade over auth.password and auth.tokens.

:class:`AuthService` holds the only stateful piece: the JWT deny-list,
which is persisted to a JSON file so that revocations survive restarts.
All cryptographic operations are delegated to :mod:`auth.password` and
:mod:`auth.tokens`.

Usage::

    service = AuthService(
        secret_key="your-32-char-random-secret",
        access_expire_minutes=60,
        refresh_expire_days=7,
    )
    hashed = service.hash_password("my-secret-password")
    access = service.create_access_token(
        user_id="...", email="...", role="general",
    )
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set

import auth.password as _pw
import auth.tokens as _tk

# Module-level logger; mutable only in the sense that logging configuration
# may change at runtime, but the reference itself is fixed after import.
_logger = logging.getLogger(__name__)


_DENY_LIST_DIR = Path(os.environ.get("DATA_DIR", "data"))
_DENY_LIST_FILE = _DENY_LIST_DIR / "auth_deny_list.json"


class AuthService:
    """Stateful auth façade for JWT lifecycle and passwords.

    One instance should be created per process and reused across
    all requests.

    Attributes:
        _secret_key: HMAC secret for signing/verifying JWTs.
        _access_expire_minutes: Access token lifetime (minutes).
        _refresh_expire_days: Refresh token lifetime (days).
        _deny_list: Set of revoked refresh-token JTI strings.
    """

    def __init__(
        self,
        secret_key: str,
        access_expire_minutes: int = 60,
        refresh_expire_days: int = 7,
    ) -> None:
        """Initialise the service with signing credentials and TTLs.

        Args:
            secret_key: HMAC-SHA256 secret (>= 32 characters).
            access_expire_minutes: Access token lifetime in minutes.
            refresh_expire_days: Refresh token lifetime in days.

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
        self._deny_list: Set[str] = self._load_deny_list()
        self._logger = logging.getLogger(__name__)
        self._logger.info(
            "AuthService initialised (access_ttl=%dm,"
            " refresh_ttl=%dd, revoked=%d).",
            access_expire_minutes,
            refresh_expire_days,
            len(self._deny_list),
        )

    @staticmethod
    def _load_deny_list() -> Set[str]:
        """Load persisted deny-list from disk.

        Returns:
            Set of revoked JTI strings, or empty set if the
            file does not exist or is unreadable.
        """
        try:
            if _DENY_LIST_FILE.exists():
                data = json.loads(_DENY_LIST_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return set(data)
        except Exception:
            _logger.warning(
                "Could not load deny-list from %s;"
                " starting with empty set.",
                _DENY_LIST_FILE,
            )
        return set()

    def _save_deny_list(self) -> None:
        """Persist the current deny-list to disk."""
        try:
            _DENY_LIST_DIR.mkdir(parents=True, exist_ok=True)
            _DENY_LIST_FILE.write_text(
                json.dumps(sorted(self._deny_list)),
                encoding="utf-8",
            )
        except Exception:
            self._logger.warning(
                "Could not persist deny-list to %s.",
                _DENY_LIST_FILE,
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
        return _tk.create_refresh_token(
            user_id, self._secret_key, self._refresh_expire_days
        )

    def decode_token(
        self, token: str, expected_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Delegate to :func:`auth.tokens.decode_token`.

        Args:
            token: The raw JWT string.
            expected_type: If provided, the decoded ``type`` claim must match.

        Returns:
            The decoded payload dict.

        Raises:
            HTTPException: 401 on any validation failure.
        """
        return _tk.decode_token(
            token, self._secret_key, self._deny_list, expected_type
        )

    def revoke_refresh_token(self, token: str) -> None:
        """Revoke a refresh token and persist the deny-list.

        Args:
            token: The raw refresh JWT string to revoke.
        """
        prev = len(self._deny_list)
        _tk.revoke_refresh_token(token, self._secret_key, self._deny_list)
        if len(self._deny_list) > prev:
            self._save_deny_list()

    def is_token_revoked(self, token: str) -> bool:
        """Delegate to :func:`auth.tokens.is_token_revoked`.

        Args:
            token: The raw JWT string to check.

        Returns:
            ``True`` if the token has been revoked.
        """
        return _tk.is_token_revoked(token, self._secret_key, self._deny_list)
