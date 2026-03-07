"""PyArrow schemas and timestamp helpers for auth Iceberg tables.

Defines the immutable schema constants used when reading from and writing to
the ``auth.users`` and ``auth.audit_log`` Iceberg tables, plus helper
functions for datetime normalisation.

Constants
---------
- :data:`_USERS_TABLE`
- :data:`_AUDIT_LOG_TABLE`
- :data:`_USERS_PA_SCHEMA`
- :data:`_AUDIT_PA_SCHEMA`
- :data:`_USER_TS_COLS`
"""

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pyarrow as pa

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Table identifiers — must match auth/create_tables.py
# ---------------------------------------------------------------------------
_NAMESPACE = "auth"
_USERS_TABLE = f"{_NAMESPACE}.users"
_AUDIT_LOG_TABLE = f"{_NAMESPACE}.audit_log"

# pa.timestamp("us") matches PyIceberg TimestampType (microseconds, no tz).
# _TS is an immutable constant; kept module-level as a shared type reference.
_TS = pa.timestamp("us")

# _USERS_PA_SCHEMA is an immutable constant; kept
# module-level as a shared schema reference.
_USERS_PA_SCHEMA = pa.schema(
    [
        pa.field("user_id", pa.string(), nullable=False),
        pa.field("email", pa.string(), nullable=False),
        pa.field("hashed_password", pa.string(), nullable=False),
        pa.field("full_name", pa.string(), nullable=False),
        pa.field("role", pa.string(), nullable=False),
        pa.field("is_active", pa.bool_(), nullable=False),
        pa.field("created_at", _TS, nullable=False),
        pa.field("updated_at", _TS, nullable=False),
        pa.field("last_login_at", _TS, nullable=True),
        pa.field("password_reset_token", pa.string(), nullable=True),
        pa.field("password_reset_expiry", _TS, nullable=True),
        pa.field("oauth_provider", pa.string(), nullable=True),
        pa.field("oauth_sub", pa.string(), nullable=True),
        pa.field("profile_picture_url", pa.string(), nullable=True),
        pa.field("page_permissions", pa.string(), nullable=True),
    ]
)

# _AUDIT_PA_SCHEMA is an immutable constant; kept
# module-level as a shared schema reference.
_AUDIT_PA_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("actor_user_id", pa.string(), nullable=False),
        pa.field("target_user_id", pa.string(), nullable=False),
        pa.field("event_timestamp", _TS, nullable=False),
        pa.field("metadata", pa.string(), nullable=True),
    ]
)

_USER_TS_COLS = (
    "created_at",
    "updated_at",
    "last_login_at",
    "password_reset_expiry",
)


def _now_utc() -> datetime:
    """Return the current UTC time as a naive datetime.

    PyArrow storage requires naive datetimes, so ``tzinfo``
    is stripped after construction.

    Returns:
        A naive :class:`datetime.datetime` in UTC.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_ts(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise a datetime to naive UTC for PyArrow storage.

    Args:
        dt: Any datetime, or ``None``.

    Returns:
        A naive UTC datetime, or ``None``.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _from_ts(val: Any) -> Optional[datetime]:
    """Convert a raw PyArrow/pandas timestamp to an aware UTC datetime.

    Args:
        val: A datetime, pandas Timestamp, float NaN, or ``None``.

    Returns:
        A timezone-aware UTC datetime, or ``None``.
    """
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    if hasattr(val, "to_pydatetime"):
        val = val.to_pydatetime()
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    return None


def _row_to_dict(row: Any) -> Dict[str, Any]:
    """Convert an Iceberg scan row to a plain Python dict with aware datetimes.

    Args:
        row: A mapping-like object from ``to_pylist()`` or a pandas Series.

    Returns:
        A plain :class:`dict` with Python-native values.
    """
    d: Dict[str, Any] = dict(row)
    for ts_col in _USER_TS_COLS:
        if ts_col in d:
            d[ts_col] = _from_ts(d[ts_col])
    return d
