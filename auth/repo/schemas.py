"""PyArrow schemas and timestamp helpers for auth Iceberg tables.

Defines the immutable schema constants for the Iceberg tables that
remain after the PostgreSQL migration: ``auth.audit_log`` and
``auth.usage_history``.

Constants
---------
- :data:`_AUDIT_LOG_TABLE`
- :data:`_AUDIT_PA_SCHEMA`
- :data:`_USAGE_HISTORY_TABLE`
- :data:`_USAGE_HISTORY_PA_SCHEMA`
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Table identifiers — must match auth/create_tables.py
# ---------------------------------------------------------------------------
_NAMESPACE = "auth"
_AUDIT_LOG_TABLE = f"{_NAMESPACE}.audit_log"
_USAGE_HISTORY_TABLE = f"{_NAMESPACE}.usage_history"

# pa.timestamp("us") matches PyIceberg TimestampType (microseconds, no tz).
# _TS is an immutable constant; kept module-level as a shared type reference.
_TS = pa.timestamp("us")

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

# _USAGE_HISTORY_PA_SCHEMA stores month-on-month usage
# snapshots archived at reset time.
_USAGE_HISTORY_PA_SCHEMA = pa.schema(
    [
        pa.field(
            "user_id", pa.string(), nullable=False,
        ),
        pa.field(
            "month", pa.string(), nullable=False,
        ),
        pa.field(
            "usage_count", pa.int32(), nullable=False,
        ),
        pa.field(
            "tier", pa.string(), nullable=False,
        ),
        pa.field(
            "archived_at", _TS, nullable=False,
        ),
    ]
)

def _now_utc() -> datetime:
    """Return the current UTC time as a naive datetime.

    PyArrow storage requires naive datetimes, so ``tzinfo``
    is stripped after construction.

    Returns:
        A naive :class:`datetime.datetime` in UTC.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_ts(dt: datetime | None) -> datetime | None:
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


def _from_ts(val: Any) -> datetime | None:
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


