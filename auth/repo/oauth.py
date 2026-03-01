"""OAuth user lookup and upsert helpers for the ``auth.users`` Iceberg table.

Functions
---------
- :func:`get_by_oauth_sub`
- :func:`get_or_create_by_oauth`
"""

import logging
import secrets as _secrets
from typing import Any, Dict, Optional

from auth.repo.schemas import _now_utc
from auth.repo.catalog import scan_all_users
from auth.repo.user_reads import get_by_email, get_by_id
from auth.repo.user_writes import create, update

# Module-level logger; kept at module scope as logging configuration is immutable.
_logger = logging.getLogger(__name__)


def get_by_oauth_sub(cat, provider: str, oauth_sub: str) -> Optional[Dict[str, Any]]:
    """Fetch a user matched by OAuth provider + subject ID.

    Args:
        cat: The loaded Iceberg catalog.
        provider: OAuth provider name, e.g. ``"google"``.
        oauth_sub: Provider-specific unique user ID.

    Returns:
        A user dict if a matching account is found, otherwise ``None``.
    """
    for row in scan_all_users(cat):
        if row.get("oauth_provider") == provider and row.get("oauth_sub") == oauth_sub:
            return row
    return None


def get_or_create_by_oauth(
    cat,
    provider: str,
    oauth_sub: str,
    email: str,
    full_name: str,
    picture_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Return an existing user or create a new SSO-only account.

    Lookup order:
    1. Match on ``(oauth_sub, oauth_provider)`` — returning SSO user.
    2. Match on ``email`` — link OAuth to existing email account.
    3. No match — create a new account with a sentinel password hash.

    Args:
        cat: The loaded Iceberg catalog.
        provider: OAuth provider name (``"google"`` or ``"facebook"``).
        oauth_sub: Provider-specific unique user ID.
        email: Email address returned by the provider.
        full_name: Display name returned by the provider.
        picture_url: Avatar URL from the provider, or ``None``.

    Returns:
        The full user dict after upsert.
    """
    now = _now_utc()

    existing = get_by_oauth_sub(cat, provider, oauth_sub)
    if existing is not None:
        # Only refresh the SSO avatar if the user has not uploaded a custom one.
        sso_updates = {"last_login_at": now}
        if not existing.get("profile_picture_url") and picture_url:
            sso_updates["profile_picture_url"] = picture_url
        update(cat, existing["user_id"], sso_updates)
        refreshed = get_by_id(cat, existing["user_id"])
        return refreshed or existing

    by_email = get_by_email(cat, email)
    if by_email is not None:
        email_updates: Dict[str, Any] = {
            "oauth_provider": provider,
            "oauth_sub": oauth_sub,
            "last_login_at": now,
        }
        if not by_email.get("profile_picture_url") and picture_url:
            email_updates["profile_picture_url"] = picture_url
        update(cat, by_email["user_id"], email_updates)
        refreshed = get_by_id(cat, by_email["user_id"])
        return refreshed or by_email

    sentinel = "!sso_only_" + _secrets.token_hex(32)
    new_user = create(
        cat,
        {
            "email": email,
            "hashed_password": sentinel,
            "full_name": full_name,
            "role": "general",
            "oauth_provider": provider,
            "oauth_sub": oauth_sub,
            "profile_picture_url": picture_url,
        },
    )
    _logger.info(
        "Created SSO account: user_id=%s provider=%s", new_user["user_id"], provider
    )
    return new_user