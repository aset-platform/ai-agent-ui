"""Read-only user queries against the ``auth.users`` Iceberg table.

Functions
---------
- :func:`get_by_email`
- :func:`get_by_id`
- :func:`list_all`
"""

from __future__ import annotations

import logging
from typing import Any

from auth.repo.catalog import scan_all_users, users_table
from auth.repo.schemas import _row_to_dict

# Module-level logger; kept here as a module-level
# constant (immutable binding).
_logger = logging.getLogger(__name__)


def get_by_email(cat, email: str) -> dict[str, Any] | None:
    """Fetch a single user by email address.

    Attempts a predicate-push-down scan first; falls back to a full scan
    on failure.

    Args:
        cat: The loaded Iceberg catalog.
        email: The email address to search for.

    Returns:
        A user dict if found, otherwise ``None``.
    """
    tbl = users_table(cat)
    try:
        from pyiceberg.expressions import EqualTo

        arrow = tbl.scan(row_filter=EqualTo("email", email)).to_arrow()
        rows = arrow.to_pylist()
        if not rows:
            return None
        return _row_to_dict(rows[0])
    except Exception as exc:
        _logger.error(
            "get_by_email predicate scan failed,"
            " falling back to full scan: %s",
            exc,
        )
        for row in scan_all_users(cat):
            if row.get("email") == email:
                return row
        return None


def get_by_id(cat, user_id: str) -> dict[str, Any] | None:
    """Fetch a single user by UUID.

    Args:
        cat: The loaded Iceberg catalog.
        user_id: The UUID string of the user to retrieve.

    Returns:
        A user dict if found, otherwise ``None``.
    """
    tbl = users_table(cat)
    try:
        from pyiceberg.expressions import EqualTo

        arrow = tbl.scan(row_filter=EqualTo("user_id", user_id)).to_arrow()
        rows = arrow.to_pylist()
        if not rows:
            return None
        return _row_to_dict(rows[0])
    except Exception as exc:
        _logger.error(
            "get_by_id predicate scan failed, falling back to full scan: %s",
            exc,
        )
        for row in scan_all_users(cat):
            if row.get("user_id") == user_id:
                return row
        return None


def list_all(cat) -> list[dict[str, Any]]:
    """Return all users from the ``auth.users`` table.

    Args:
        cat: The loaded Iceberg catalog.

    Returns:
        A list of user dicts (may be empty).
    """
    return scan_all_users(cat)
