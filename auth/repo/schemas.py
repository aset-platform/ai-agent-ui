"""PyArrow schemas and timestamp helpers for auth Iceberg tables.

Defines the immutable schema constants used when reading from and
writing to the ``auth.users``, ``auth.audit_log``, and
``auth.user_tickers`` Iceberg tables, plus helper functions for
datetime normalisation.

Constants
---------
- :data:`_USERS_TABLE`
- :data:`_AUDIT_LOG_TABLE`
- :data:`_USERS_PA_SCHEMA`
- :data:`_AUDIT_PA_SCHEMA`
- :data:`_USER_TS_COLS`
- :data:`_USER_TICKERS_TABLE`
- :data:`_USER_TICKERS_PA_SCHEMA`
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
_USERS_TABLE = f"{_NAMESPACE}.users"
_AUDIT_LOG_TABLE = f"{_NAMESPACE}.audit_log"
_USER_TICKERS_TABLE = f"{_NAMESPACE}.user_tickers"
_USAGE_HISTORY_TABLE = f"{_NAMESPACE}.usage_history"
_PAYMENT_TXN_TABLE = f"{_NAMESPACE}.payment_transactions"

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
        # Subscription fields (field_ids 16–24)
        pa.field(
            "subscription_tier", pa.string(), nullable=True,
        ),
        pa.field(
            "subscription_status", pa.string(), nullable=True,
        ),
        pa.field(
            "razorpay_customer_id", pa.string(), nullable=True,
        ),
        pa.field(
            "razorpay_subscription_id",
            pa.string(),
            nullable=True,
        ),
        pa.field(
            "stripe_customer_id", pa.string(), nullable=True,
        ),
        pa.field(
            "stripe_subscription_id",
            pa.string(),
            nullable=True,
        ),
        pa.field(
            "monthly_usage_count", pa.int32(), nullable=True,
        ),
        pa.field(
            "usage_month", pa.string(), nullable=True,
        ),
        pa.field(
            "subscription_start_at", _TS, nullable=True,
        ),
        pa.field(
            "subscription_end_at", _TS, nullable=True,
        ),
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

# _USER_TICKERS_PA_SCHEMA is an immutable constant; kept
# module-level as a shared schema reference.
_USER_TICKERS_PA_SCHEMA = pa.schema(
    [
        pa.field("user_id", pa.string(), nullable=False),
        pa.field("ticker", pa.string(), nullable=False),
        pa.field("linked_at", _TS, nullable=False),
        pa.field("source", pa.string(), nullable=False),
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

_PAYMENT_TXN_PA_SCHEMA = pa.schema(
    [
        pa.field(
            "transaction_id", pa.string(),
            nullable=False,
        ),
        pa.field(
            "user_id", pa.string(), nullable=False,
        ),
        pa.field(
            "gateway", pa.string(), nullable=False,
        ),
        pa.field(
            "event_type", pa.string(), nullable=False,
        ),
        pa.field(
            "gateway_event_id", pa.string(),
            nullable=True,
        ),
        pa.field(
            "subscription_id", pa.string(),
            nullable=True,
        ),
        pa.field(
            "customer_id", pa.string(), nullable=True,
        ),
        pa.field(
            "amount", pa.float64(), nullable=True,
        ),
        pa.field(
            "currency", pa.string(), nullable=True,
        ),
        pa.field(
            "tier_before", pa.string(), nullable=True,
        ),
        pa.field(
            "tier_after", pa.string(), nullable=True,
        ),
        pa.field(
            "status", pa.string(), nullable=False,
        ),
        pa.field(
            "raw_payload", pa.string(), nullable=True,
        ),
        pa.field(
            "created_at", _TS, nullable=False,
        ),
    ]
)

_USER_TS_COLS = (
    "created_at",
    "updated_at",
    "last_login_at",
    "password_reset_expiry",
    "subscription_start_at",
    "subscription_end_at",
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


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert an Iceberg scan row to a plain Python dict with aware datetimes.

    Args:
        row: A mapping-like object from ``to_pylist()`` or a pandas Series.

    Returns:
        A plain :class:`dict` with Python-native values.
    """
    d: dict[str, Any] = dict(row)
    for ts_col in _USER_TS_COLS:
        if ts_col in d:
            d[ts_col] = _from_ts(d[ts_col])
    return d
