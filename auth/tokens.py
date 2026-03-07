"""JWT creation, validation, and revocation helpers.

All functions accept explicit parameters (no ``self``) so they can be used
independently of :class:`~auth.service.AuthService`
and are easily unit-testable.

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
from typing import Any, Dict, Optional, Set

from fastapi import HTTPException
from jose import JWTError, jwt

# Module-level logger; kept at module scope as a private constant to avoid
# mutable global state in class instances while remaining accessible to all
# module-level functions.
_logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_ACCESS_TOKEN_TYPE = "access"
_REFRESH_TOKEN_TYPE = "refresh"


def create_access_token(
    user_id: str, email: str, role: str, secret_key: str, expire_minutes: int
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
    _logger.debug("Access token created for user_id=%s", user_id)
    return token


def create_refresh_token(
    user_id: str, secret_key: str, expire_days: int
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
    _logger.debug("Refresh token created for user_id=%s", user_id)
    return token


def decode_token(
    token: str,
    secret_key: str,
    deny_list: Set[str],
    expected_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Decode and validate a JWT, raising HTTP 401 on any failure.

    Args:
        token: The raw JWT string.
        secret_key: HMAC-SHA256 secret.
        deny_list: Set of revoked JTI strings.
        expected_type: If provided, the decoded ``type`` claim must match.

    Returns:
        The decoded payload dict.

    Raises:
        HTTPException: 401 if the token is invalid, expired, revoked, or
            of the wrong type.

    Example:
        >>> t = create_access_token("uid", "u@x.com", "general", "a"*32, 60)
        >>> p = decode_token(t, "a"*32, set(), expected_type="access")
        >>> p["role"]
        'general'
    """
    try:
        payload = jwt.decode(token, secret_key, algorithms=[_ALGORITHM])
    except JWTError as exc:
        _logger.warning("JWT decode failed: %s", exc)
        raise HTTPException(
            status_code=401, detail="Invalid or expired token"
        ) from exc

    jti = payload.get("jti", "")
    if jti in deny_list:
        raise HTTPException(status_code=401, detail="Token has been revoked")

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
    token: str, secret_key: str, deny_list: Set[str]
) -> None:
    """Add a refresh token's JTI to the deny-list.

    Args:
        token: The raw refresh JWT string to revoke.
        secret_key: HMAC-SHA256 secret.
        deny_list: Mutable set of revoked JTI strings to update in-place.

    Example:
        >>> deny = set()
        >>> t = create_refresh_token("uid", "a"*32, 7)
        >>> revoke_refresh_token(t, "a"*32, deny)
        >>> len(deny) == 1
        True
    """
    try:
        payload = jwt.decode(token, secret_key, algorithms=[_ALGORITHM])
        jti = payload.get("jti", "")
        if jti:
            deny_list.add(jti)
            _logger.info("Refresh token revoked (jti=%s).", jti)
    except JWTError:
        _logger.debug("revoke_refresh_token: token already invalid, skipping.")


def is_token_revoked(token: str, secret_key: str, deny_list: Set[str]) -> bool:
    """Check whether a token's JTI is in the deny-list.

    Args:
        token: The raw JWT string to check.
        secret_key: HMAC-SHA256 secret.
        deny_list: Set of revoked JTI strings.

    Returns:
        ``True`` if the token has been revoked, ``False`` otherwise.

    Example:
        >>> deny = set()
        >>> t = create_refresh_token("uid", "a"*32, 7)
        >>> is_token_revoked(t, "a"*32, deny)
        False
        >>> revoke_refresh_token(t, "a"*32, deny)
        >>> is_token_revoked(t, "a"*32, deny)
        True
    """
    try:
        payload = jwt.decode(token, secret_key, algorithms=[_ALGORITHM])
        return payload.get("jti", "") in deny_list
    except JWTError:
        return True
