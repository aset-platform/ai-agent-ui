"""Iceberg schema migration — evolve ``auth.users`` schema.

Adds OAuth columns (v1) and subscription columns (v2) to an
existing ``auth.users`` table.  The script is idempotent — columns
that already exist are skipped without error.

Usage::

    cd ai-agent-ui
    source ~/.ai-agent-ui/venv/bin/activate
    python auth/migrate_users_table.py

Columns added
-------------
**v1 — OAuth:**
- ``oauth_provider`` (StringType, nullable)
- ``oauth_sub`` (StringType, nullable)
- ``profile_picture_url`` (StringType, nullable)
- ``page_permissions`` (StringType, nullable)

**v2 — Subscription:**
- ``subscription_tier`` (StringType, nullable)
- ``subscription_status`` (StringType, nullable)
- ``razorpay_customer_id`` (StringType, nullable)
- ``razorpay_subscription_id`` (StringType, nullable)
- ``stripe_customer_id`` (StringType, nullable)
- ``stripe_subscription_id`` (StringType, nullable)
- ``monthly_usage_count`` (IntegerType, nullable)
- ``subscription_start_at`` (TimestampType, nullable)
- ``subscription_end_at`` (TimestampType, nullable)
"""

from __future__ import annotations

import logging
import os

from pyiceberg.types import IntegerType, StringType, TimestampType

_logger = logging.getLogger(__name__)

_USERS_TABLE = "auth.users"

_NEW_COLUMNS = (
    # v1 — OAuth
    ("oauth_provider", StringType()),
    ("oauth_sub", StringType()),
    ("profile_picture_url", StringType()),
    ("page_permissions", StringType()),
    # v2 — Subscription
    ("subscription_tier", StringType()),
    ("subscription_status", StringType()),
    ("razorpay_customer_id", StringType()),
    ("razorpay_subscription_id", StringType()),
    ("stripe_customer_id", StringType()),
    ("stripe_subscription_id", StringType()),
    ("monthly_usage_count", IntegerType()),
    ("usage_month", StringType()),
    ("subscription_start_at", TimestampType()),
    ("subscription_end_at", TimestampType()),
)


def migrate() -> None:
    """Add OAuth and subscription columns to ``auth.users``.

    Uses Iceberg schema evolution (``update_schema()``) so existing
    data rows are unaffected — new columns read as ``None`` for
    pre-migration rows.

    Raises:
        RuntimeError: If the Iceberg catalog cannot be loaded.
    """
    from pyiceberg.catalog import load_catalog

    catalog = load_catalog("local")
    table = catalog.load_table(_USERS_TABLE)

    existing_names = {f.name for f in table.schema().fields}
    added = []

    with table.update_schema() as upd:
        for col_name, col_type in _NEW_COLUMNS:
            if col_name not in existing_names:
                upd.add_column(col_name, col_type)
                added.append(col_name)
                _logger.info("  + Added column: %s", col_name)
            else:
                _logger.info("  checkmark Column already exists: %s", col_name)

    if added:
        _logger.info("Migration complete — added columns: %s", added)
    else:
        _logger.info(
            "Migration complete — no changes needed"
            " (all columns present).",
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    # Run from any directory — chdir to project root so .pyiceberg.yaml
    # relative paths resolve correctly.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    migrate()
