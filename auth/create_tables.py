"""One-time Iceberg table initialisation for the auth module.

Creates ``audit_log`` and ``usage_history`` in the local
SqlCatalog-backed Iceberg warehouse.  Users, user_tickers,
and payment_transactions have been migrated to PostgreSQL.
The script is idempotent -- if tables already exist it exits
without error.

Usage::

    cd ai-agent-ui
    source ~/.ai-agent-ui/venv/bin/activate
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

from __future__ import annotations

import logging
import os
import sys

# Ensure backend/ is on sys.path so paths module can be imported
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.join(_SCRIPT_DIR, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from paths import ICEBERG_CATALOG_URI, ICEBERG_WAREHOUSE_URI  # noqa: E402

os.environ.setdefault("PYICEBERG_CATALOG__LOCAL__URI", ICEBERG_CATALOG_URI)
os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__WAREHOUSE", ICEBERG_WAREHOUSE_URI
)

import pyarrow as pa  # noqa: E402, F401
from pyiceberg.catalog.sql import SqlCatalog  # noqa: E402
from pyiceberg.schema import Schema  # noqa: E402
from pyiceberg.types import (  # noqa: E402
    IntegerType,
    NestedField,
    StringType,
    TimestampType,
)

# Module-level logger; mutable but required at module scope
# for use outside any class.
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Namespace and table names
# ---------------------------------------------------------------------------
_NAMESPACE = "auth"
_AUDIT_LOG_TABLE = f"{_NAMESPACE}.audit_log"
_USAGE_HISTORY_TABLE = f"{_NAMESPACE}.usage_history"


def _get_catalog() -> SqlCatalog:
    """Load the local Iceberg SqlCatalog from ``.pyiceberg.yaml``.

    Returns:
        SqlCatalog: A configured
            :class:`pyiceberg.catalog.sql.SqlCatalog`.

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


def _audit_log_schema() -> Schema:
    """Return the Iceberg schema for the ``audit_log`` table.

    Returns:
        Schema: An Iceberg :class:`~pyiceberg.schema.Schema`
            for audit log events.
    """
    return Schema(
        NestedField(
            field_id=1, name="event_id", field_type=StringType(), required=True
        ),
        NestedField(
            field_id=2,
            name="event_type",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=3,
            name="actor_user_id",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=4,
            name="target_user_id",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=5,
            name="event_timestamp",
            field_type=TimestampType(),
            required=True,
        ),
        NestedField(
            field_id=6,
            name="metadata",
            field_type=StringType(),
            required=False,
        ),
    )


def _usage_history_schema() -> Schema:
    """Return the Iceberg schema for ``usage_history``."""
    return Schema(
        NestedField(
            field_id=1,
            name="user_id",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=2,
            name="month",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=3,
            name="usage_count",
            field_type=IntegerType(),
            required=True,
        ),
        NestedField(
            field_id=4,
            name="tier",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=5,
            name="archived_at",
            field_type=TimestampType(),
            required=True,
        ),
    )


def create_tables() -> None:
    """Create auth Iceberg tables (audit_log, usage_history).

    Users, user_tickers, and payment_transactions have been
    migrated to PostgreSQL and are no longer created here.

    This function is idempotent -- calling it on an
    already-initialised catalog simply logs and returns.

    Raises:
        RuntimeError: If the catalog cannot be loaded.
    """
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)-8s"
            " | %(name)s | %(message)s"
        ),
    )

    catalog = _get_catalog()

    # Create namespace
    try:
        catalog.create_namespace(_NAMESPACE)
        logger.info(
            "Created Iceberg namespace '%s'.",
            _NAMESPACE,
        )
    except Exception:
        logger.info(
            "Namespace '%s' already exists -- skipping.",
            _NAMESPACE,
        )

    # Create audit_log table
    try:
        catalog.create_table(
            identifier=_AUDIT_LOG_TABLE,
            schema=_audit_log_schema(),
        )
        logger.info(
            "Created Iceberg table '%s'.",
            _AUDIT_LOG_TABLE,
        )
    except Exception:
        logger.info(
            "Table '%s' already exists -- skipping.",
            _AUDIT_LOG_TABLE,
        )

    # Create usage_history table
    try:
        catalog.create_table(
            identifier=_USAGE_HISTORY_TABLE,
            schema=_usage_history_schema(),
        )
        logger.info(
            "Created Iceberg table '%s'.",
            _USAGE_HISTORY_TABLE,
        )
    except Exception:
        logger.info(
            "Table '%s' already exists -- skipping.",
            _USAGE_HISTORY_TABLE,
        )

    logger.info("Iceberg table initialisation complete.")


if __name__ == "__main__":
    # Allow running from any working directory by resolving the project root.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    create_tables()
