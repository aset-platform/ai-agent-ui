"""Password hashing and strength-validation utilities.

Provides bcrypt-backed password hashing (cost factor 12) and a minimum
password-strength check used at account creation and password reset.

Functions
---------
- :func:`hash_password`
- :func:`verify_password`
- :func:`validate_password_strength`
"""

import logging

from fastapi import HTTPException
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

# bcrypt cost factor 12 — good balance of security and speed (~250ms per hash)
# Module-level constant: shared across all callers; intentionally not per-instance.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt.

    Args:
        plain: The plaintext password supplied by the user.

    Returns:
        A bcrypt hash string safe to store in the database.

    Example:
        >>> h = hash_password("mypassword123")
        >>> h.startswith("$2b$")
        True
    """
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash.

    Args:
        plain: The plaintext password supplied by the user.
        hashed: The bcrypt hash retrieved from the database.

    Returns:
        ``True`` if the password matches the hash, ``False`` otherwise.

    Example:
        >>> h = hash_password("mypassword123")
        >>> verify_password("mypassword123", h)
        True
        >>> verify_password("wrongpassword", h)
        False
    """
    return _pwd_context.verify(plain, hashed)


def validate_password_strength(password: str) -> None:
    """Raise HTTP 400 if *password* does not meet minimum requirements.

    Requirements: at least 8 characters and at least one digit.

    Args:
        password: The plaintext password to validate.

    Raises:
        HTTPException: 400 if the password is too weak.

    Example:
        >>> validate_password_strength("abc123def")  # passes
        >>> validate_password_strength("abcdefgh")  # doctest: +SKIP
        Traceback (most recent call last):
            ...
        fastapi.HTTPException: 400
    """
    if len(password) < 8:
        logger.debug("Password validation failed: fewer than 8 characters.")
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters."
        )
    if not any(c.isdigit() for c in password):
        logger.debug("Password validation failed: no digit present.")
        raise HTTPException(
            status_code=400, detail="Password must contain at least one digit."
        )