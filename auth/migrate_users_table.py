"""Iceberg schema migration — add OAuth columns to ``auth.users``.

Run once after pulling this branch to add the three SSO columns to an
existing ``auth.users`` table.  The script is idempotent — columns that
already exist are skipped without error.

Usage::

    cd ai-agent-ui
    source ~/.ai-agent-ui/venv/bin/activate
    python auth/migrate_users_table.py

New columns added
-----------------
- ``oauth_provider`` (StringType, nullable) — ``"google"`` / ``"facebook"``
  / ``None`` for email-only accounts.
- ``oauth_sub`` (StringType, nullable) — provider-specific unique user ID.
- ``profile_picture_url`` (StringType, nullable) — avatar URL refreshed on
  each SSO login.
"""

from __future__ import annotations

import logging
import os

from pyiceberg.types import StringType

# Module-level logger (non-mutable).
# Prefixed with '_' to indicate internal use.
_logger = logging.getLogger(__name__)

_USERS_TABLE = "auth.users"

# Immutable collection of new columns to add.
# Prefixed with '_' as internal constant.
_NEW_COLUMNS = (
    ("oauth_provider", StringType()),
    ("oauth_sub", StringType()),
    ("profile_picture_url", StringType()),
    ("page_permissions", StringType()),
)


def migrate() -> None:
    """Add the three OAuth columns to ``auth.users`` if they are absent.

    Uses Iceberg schema evolution (``update_schema()``) so existing data
    rows are unaffected — new columns read as ``None`` for pre-migration
    rows.

    Args:
        None.

    Returns:
        None.

    Raises:
        RuntimeError: If the Iceberg catalog cannot be loaded.

    Example:
        >>> migrate()  # doctest: +SKIP
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
        _logger.info("Migration complete — no changes needed (all columns present).")


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
