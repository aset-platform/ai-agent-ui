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

import bcrypt
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# bcrypt cost factor 12 — good balance of security and speed (~250ms per hash)
_BCRYPT_ROUNDS = 12


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
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


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
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def validate_password_strength(password: str) -> None:
    """Raise HTTP 400 if *password* does not meet minimum requirements.

    Requirements:
    - At least 8 characters.
    - At least one digit.
    - At least one uppercase letter.
    - At least one special character.

    Args:
        password: The plaintext password to validate.

    Raises:
        HTTPException: 400 if the password is too weak.

    Example:
        >>> validate_password_strength("Abc123!x")  # passes
        >>> validate_password_strength("abcdefgh")  # doctest: +SKIP
        Traceback (most recent call last):
            ...
        fastapi.HTTPException: 400
    """
    if len(password) < 8:
        logger.debug("Password validation failed: fewer than 8 chars.")
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters.",
        )
    if not any(c.isdigit() for c in password):
        logger.debug("Password validation failed: no digit.")
        raise HTTPException(
            status_code=400,
            detail="Password must contain at least one digit.",
        )
    if not any(c.isupper() for c in password):
        logger.debug("Password validation failed: no uppercase.")
        raise HTTPException(
            status_code=400,
            detail=("Password must contain at least one" " uppercase letter."),
        )
    if not any(not c.isalnum() for c in password):
        logger.debug("Password validation failed: no special char.")
        raise HTTPException(
            status_code=400,
            detail=(
                "Password must contain at least one" " special character."
            ),
        )
