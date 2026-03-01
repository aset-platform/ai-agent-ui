"""Write operations on the ``auth.users`` Iceberg table.

Implements copy-on-write update semantics: read the full table as a pandas
DataFrame, mutate in-place, then overwrite.

Functions
---------
- :func:`create`
- :func:`update`
- :func:`delete`
"""

import json as _json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict

import pyarrow as pa

from auth.repo.schemas import (
    _USERS_PA_SCHEMA,
    _USER_TS_COLS,
    _now_utc,
    _to_ts,
    _from_ts,
)
from auth.repo.catalog import users_table
from auth.repo.user_reads import get_by_email

# Module-level logger; kept here as a module-level constant (immutable binding).
_logger = logging.getLogger(__name__)


def create(cat, user_data: Dict[str, Any]) -> Dict[str, Any]:
    """Append a new user row to the ``auth.users`` table.

    Args:
        cat: The loaded Iceberg catalog.
        user_data: Dict with at minimum ``email``, ``hashed_password``,
            ``full_name``, and ``role``.

    Returns:
        The full user dict as stored, including generated fields.

    Raises:
        ValueError: If a user with the same email already exists.
    """
    if get_by_email(cat, user_data["email"]) is not None:
        raise ValueError(f"User with email '{user_data['email']}' already exists.")

    now = _now_utc()
    row = {
        "user_id": user_data.get("user_id", str(uuid.uuid4())),
        "email": user_data["email"],
        "hashed_password": user_data["hashed_password"],
        "full_name": user_data["full_name"],
        "role": user_data.get("role", "general"),
        "is_active": user_data.get("is_active", True),
        "created_at": _to_ts(user_data.get("created_at", now)),
        "updated_at": _to_ts(user_data.get("updated_at", now)),
        "last_login_at": _to_ts(user_data.get("last_login_at")),
        "password_reset_token": user_data.get("password_reset_token"),
        "password_reset_expiry": _to_ts(user_data.get("password_reset_expiry")),
        "oauth_provider": user_data.get("oauth_provider"),
        "oauth_sub": user_data.get("oauth_sub"),
        "profile_picture_url": user_data.get("profile_picture_url"),
        "page_permissions": (
            _json.dumps(user_data["page_permissions"])
            if isinstance(user_data.get("page_permissions"), dict)
            else user_data.get("page_permissions")
        ),
    }

    arrow_table = pa.table({k: [v] for k, v in row.items()}, schema=_USERS_PA_SCHEMA)
    tbl = users_table(cat)
    tbl.append(arrow_table)
    _logger.info("Created user user_id=%s email=%s", row["user_id"], row["email"])

    stored = dict(row)
    for ts_col in _USER_TS_COLS:
        stored[ts_col] = _from_ts(stored[ts_col])
    return stored


def update(cat, user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Apply *updates* to the user identified by *user_id* (copy-on-write).

    Args:
        cat: The loaded Iceberg catalog.
        user_id: UUID string of the user to update.
        updates: Dict of fields to overwrite.

    Returns:
        The full updated user dict.

    Raises:
        ValueError: If no user with the given *user_id* exists.
    """
    import pandas as pd

    tbl = users_table(cat)
    arrow_table = tbl.scan().to_arrow()
    df: pd.DataFrame = arrow_table.to_pandas()

    mask = df["user_id"] == user_id
    if not mask.any():
        raise ValueError(f"User '{user_id}' not found.")

    immutable = {"user_id", "created_at"}
    for field, value in updates.items():
        if field in immutable:
            continue
        if field in ("last_login_at", "password_reset_expiry"):
            df.loc[mask, field] = _to_ts(value)
        elif field == "page_permissions" and isinstance(value, dict):
            df.loc[mask, field] = _json.dumps(value)
        else:
            df.loc[mask, field] = value

    df.loc[mask, "updated_at"] = _to_ts(_now_utc())

    # Ensure every column in the PA schema is present in the DataFrame
    # (guards against tables created before a schema migration was applied).
    for pa_field in _USERS_PA_SCHEMA:
        if pa_field.name not in df.columns:
            df[pa_field.name] = None

    new_arrow = pa.Table.from_pandas(df, schema=_USERS_PA_SCHEMA, preserve_index=False)
    tbl.overwrite(new_arrow)
    _logger.info("Updated user user_id=%s fields=%s", user_id, list(updates.keys()))

    updated_row = df[mask].iloc[0].to_dict()
    from auth.repo.schemas import _row_to_dict
    return _row_to_dict(updated_row)


def delete(cat, user_id: str) -> None:
    """Soft-delete a user by setting ``is_active = False``.

    Args:
        cat: The loaded Iceberg catalog.
        user_id: UUID string of the user to deactivate.

    Raises:
        ValueError: If no user with the given *user_id* exists.
    """
    update(cat, user_id, {"is_active": False})
    _logger.info("Soft-deleted user user_id=%s", user_id)