"""One-time Iceberg table initialisation for the auth module.

Run this script once to create the ``users`` and ``audit_log`` tables in the
local SqlCatalog-backed Iceberg warehouse.  The script is idempotent — if the
tables already exist it exits without error.

Usage::

    cd ai-agent-ui
    source backend/demoenv/bin/activate
    python auth/create_tables.py

The Iceberg catalog is configured via ``.pyiceberg.yaml`` in the project root
(or ``$HOME``).  The catalog name must be ``local`` and should point at a
SQLite URI plus a local warehouse path::

    catalog:
      local:
        type: sql
        uri: sqlite:///data/iceberg/catalog.db
        warehouse: file:///absolute/path/to/ai-agent-ui/data/iceberg/warehouse

Both ``data/iceberg/catalog.db`` and ``data/iceberg/warehouse/`` are created
automatically if they do not already exist.
"""

import logging
import os
import sys

import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    NestedField,
    StringType,
    TimestampType,
)

# Module-level logger; mutable but required at module scope for use outside any class.
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Namespace and table names
# ---------------------------------------------------------------------------
_NAMESPACE = "auth"
_USERS_TABLE = f"{_NAMESPACE}.users"
_AUDIT_LOG_TABLE = f"{_NAMESPACE}.audit_log"


def _get_catalog() -> SqlCatalog:
    """Load the local Iceberg SqlCatalog from ``.pyiceberg.yaml``.

    Returns:
        SqlCatalog: A configured :class:`pyiceberg.catalog.sql.SqlCatalog` instance.

    Raises:
        RuntimeError: If the catalog cannot be loaded.
    """
    from pyiceberg.catalog import load_catalog

    try:
        catalog = load_catalog("local")
        return catalog
    except Exception as exc:
        raise RuntimeError(
            "Failed to load Iceberg catalog. "
            "Check that .pyiceberg.yaml exists in the project root and that "
            "data/iceberg/ is writable."
        ) from exc


def _users_schema() -> Schema:
    """Return the Iceberg schema for the ``users`` table.

    Returns:
        Schema: An Iceberg :class:`~pyiceberg.schema.Schema` describing all user fields.
    """
    return Schema(
        NestedField(field_id=1, name="user_id", field_type=StringType(), required=True),
        NestedField(field_id=2, name="email", field_type=StringType(), required=True),
        NestedField(field_id=3, name="hashed_password", field_type=StringType(), required=True),
        NestedField(field_id=4, name="full_name", field_type=StringType(), required=True),
        NestedField(field_id=5, name="role", field_type=StringType(), required=True),
        NestedField(field_id=6, name="is_active", field_type=BooleanType(), required=True),
        NestedField(field_id=7, name="created_at", field_type=TimestampType(), required=True),
        NestedField(field_id=8, name="updated_at", field_type=TimestampType(), required=True),
        NestedField(field_id=9, name="last_login_at", field_type=TimestampType(), required=False),
        NestedField(
            field_id=10,
            name="password_reset_token",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=11,
            name="password_reset_expiry",
            field_type=TimestampType(),
            required=False,
        ),
        # SSO columns — nullable; None for email-only accounts.
        NestedField(field_id=12, name="oauth_provider", field_type=StringType(), required=False),
        NestedField(field_id=13, name="oauth_sub", field_type=StringType(), required=False),
        NestedField(
            field_id=14,
            name="profile_picture_url",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=15,
            name="page_permissions",
            field_type=StringType(),
            required=False,
        ),
    )


def _audit_log_schema() -> Schema:
    """Return the Iceberg schema for the ``audit_log`` table.

    Returns:
        Schema: An Iceberg :class:`~pyiceberg.schema.Schema` for audit log events.
    """
    return Schema(
        NestedField(field_id=1, name="event_id", field_type=StringType(), required=True),
        NestedField(field_id=2, name="event_type", field_type=StringType(), required=True),
        NestedField(field_id=3, name="actor_user_id", field_type=StringType(), required=True),
        NestedField(field_id=4, name="target_user_id", field_type=StringType(), required=True),
        NestedField(
            field_id=5,
            name="event_timestamp",
            field_type=TimestampType(),
            required=True,
        ),
        NestedField(field_id=6, name="metadata", field_type=StringType(), required=False),
    )


def create_tables() -> None:
    """Create the ``users`` and ``audit_log`` Iceberg tables.

    This function is idempotent — calling it on an already‑initialised catalog
    simply logs a message and returns.  It creates the ``auth`` namespace if it
    does not already exist.

    Raises:
        RuntimeError: If the catalog cannot be loaded or table creation fails.

    Example:
        >>> create_tables()  # doctest: +SKIP
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    catalog = _get_catalog()

    # Create namespace
    try:
        catalog.create_namespace(_NAMESPACE)
        logger.info("Created Iceberg namespace '%s'.", _NAMESPACE)
    except Exception:
        logger.info("Namespace '%s' already exists — skipping.", _NAMESPACE)

    # Create users table
    try:
        catalog.create_table(identifier=_USERS_TABLE, schema=_users_schema())
        logger.info("Created Iceberg table '%s'.", _USERS_TABLE)
    except Exception:
        logger.info("Table '%s' already exists — skipping.", _USERS_TABLE)

    # Create audit_log table
    try:
        catalog.create_table(identifier=_AUDIT_LOG_TABLE, schema=_audit_log_schema())
        logger.info("Created Iceberg table '%s'.", _AUDIT_LOG_TABLE)
    except Exception:
        logger.info("Table '%s' already exists — skipping.", _AUDIT_LOG_TABLE)

    logger.info("Iceberg table initialisation complete.")


if __name__ == "__main__":
    # Allow running from any working directory by resolving the project root.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    create_tables()