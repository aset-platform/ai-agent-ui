"""JWT creation, validation, and revocation helpers.

All functions accept explicit parameters (no ``self``) so they can be
used independently of :class:`~auth.service.AuthService` and are
easily unit-testable.

The deny-list parameter is a :class:`~auth.token_store.TokenStore`
instance (in-memory or Redis).

Functions
---------
- :func:`create_access_token`
- :func:`create_refresh_token`
- :func:`decode_token`
- :func:`revoke_refresh_token`
- :func:`is_token_revoked`
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import HTTPException
from jose import JWTError, jwt

from auth.token_store import TokenStore

_logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_ACCESS_TOKEN_TYPE = "access"
_REFRESH_TOKEN_TYPE = "refresh"


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    secret_key: str,
    expire_minutes: int,
) -> str:
    """Create a signed JWT access token.

    Args:
        user_id: UUID string of the authenticated user.
        email: Email address to embed in the token.
        role: User role (``"superuser"`` or ``"general"``).
        secret_key: HMAC-SHA256 secret.
        expire_minutes: Access token lifetime in minutes.

    Returns:
        A signed JWT string.

    Example:
        >>> token = create_access_token(
        ...     "uid", "u@x.com", "general", "a"*32, 60
        ... )
        >>> isinstance(token, str)
        True
    """
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "type": _ACCESS_TOKEN_TYPE,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    token = jwt.encode(payload, secret_key, algorithm=_ALGORITHM)
    _logger.debug(
        "Access token created for user_id=%s", user_id,
    )
    return token


def create_refresh_token(
    user_id: str, secret_key: str, expire_days: int,
) -> str:
    """Create a signed JWT refresh token.

    Args:
        user_id: UUID string of the authenticated user.
        secret_key: HMAC-SHA256 secret.
        expire_days: Refresh token lifetime in days.

    Returns:
        A signed JWT string.

    Example:
        >>> token = create_refresh_token("uid", "a"*32, 7)
        >>> isinstance(token, str)
        True
    """
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "type": _REFRESH_TOKEN_TYPE,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(days=expire_days),
    }
    token = jwt.encode(payload, secret_key, algorithm=_ALGORITHM)
    _logger.debug(
        "Refresh token created for user_id=%s", user_id,
    )
    return token


def decode_token(
    token: str,
    secret_key: str,
    store: TokenStore,
    expected_type: str | None = None,
) -> Dict[str, Any]:
    """Decode and validate a JWT, raising HTTP 401 on failure.

    Args:
        token: The raw JWT string.
        secret_key: HMAC-SHA256 secret.
        store: Token store for deny-list lookups.
        expected_type: If provided, the decoded ``type`` claim
            must match.

    Returns:
        The decoded payload dict.

    Raises:
        HTTPException: 401 if the token is invalid, expired,
            revoked, or of the wrong type.
    """
    try:
        payload = jwt.decode(
            token, secret_key, algorithms=[_ALGORITHM],
        )
    except JWTError as exc:
        _logger.warning("JWT decode failed: %s", exc)
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
        ) from exc

    jti = payload.get("jti", "")
    if jti and store.contains(jti):
        raise HTTPException(
            status_code=401,
            detail="Token has been revoked",
        )

    if expected_type and payload.get("type") != expected_type:
        raise HTTPException(
            status_code=401,
            detail=(
                f"Expected token type"
                f" '{expected_type}', got"
                f" '{payload.get('type')}'"
            ),
        )

    return payload


def revoke_refresh_token(
    token: str,
    secret_key: str,
    store: TokenStore,
    refresh_expire_days: int = 7,
) -> None:
    """Add a refresh token's JTI to the deny store.

    The TTL matches the token's remaining lifetime so entries
    auto-expire.

    Args:
        token: The raw refresh JWT string to revoke.
        secret_key: HMAC-SHA256 secret.
        store: Token store backend.
        refresh_expire_days: Max refresh token lifetime (days).
    """
    try:
        payload = jwt.decode(
            token, secret_key, algorithms=[_ALGORITHM],
        )
        jti = payload.get("jti", "")
        if jti:
            ttl = refresh_expire_days * 86400
            store.add(jti, ttl)
            _logger.info(
                "Refresh token revoked (jti=%s).", jti,
            )
    except JWTError:
        _logger.debug(
            "revoke_refresh_token: token already invalid,"
            " skipping.",
        )


def is_token_revoked(
    token: str,
    secret_key: str,
    store: TokenStore,
) -> bool:
    """Check whether a token's JTI is in the deny store.

    Args:
        token: The raw JWT string to check.
        secret_key: HMAC-SHA256 secret.
        store: Token store backend.

    Returns:
        ``True`` if the token has been revoked.
    """
    try:
        payload = jwt.decode(
            token, secret_key, algorithms=[_ALGORITHM],
        )
        return store.contains(payload.get("jti", ""))
    except JWTError:
        return True
