"""Iceberg catalog loader and table-accessor helpers for auth tables.

Functions
---------
- :func:`get_catalog`
- :func:`users_table`
- :func:`audit_table`
- :func:`scan_all_users`
"""

import logging
from typing import Any, Dict, List

from auth.repo.schemas import _USERS_TABLE, _AUDIT_LOG_TABLE, _row_to_dict

# Module-level logger; not mutable state — safe to keep at module level.
_logger = logging.getLogger(__name__)


def get_catalog(root: str):
    """Load and return the Iceberg catalog from ``.pyiceberg.yaml``.

    Args:
        root: Absolute path to the project root directory.  The working
            directory is temporarily set to *root* so that the relative
            ``sqlite:///data/iceberg/catalog.db`` URI resolves correctly.

    Returns:
        A :class:`pyiceberg.catalog.sql.SqlCatalog` instance.

    Raises:
        RuntimeError: If the catalog cannot be loaded.
    """
    import os
    os.chdir(root)
    from pyiceberg.catalog import load_catalog

    try:
        cat = load_catalog("local")
        _logger.debug("Iceberg catalog loaded.")
        return cat
    except Exception as exc:
        raise RuntimeError(
            "Failed to load Iceberg catalog. "
            "Check that .pyiceberg.yaml exists and data/iceberg/ is writable."
        ) from exc


def users_table(cat):
    """Return the open ``auth.users`` Iceberg table.

    Args:
        cat: The loaded Iceberg catalog.

    Returns:
        The ``auth.users`` :class:`pyiceberg.table.Table`.
    """
    return cat.load_table(_USERS_TABLE)


def audit_table(cat):
    """Return the open ``auth.audit_log`` Iceberg table.

    Args:
        cat: The loaded Iceberg catalog.

    Returns:
        The ``auth.audit_log`` :class:`pyiceberg.table.Table`.
    """
    return cat.load_table(_AUDIT_LOG_TABLE)


def scan_all_users(cat) -> List[Dict[str, Any]]:
    """Read every row from ``auth.users`` as a list of plain dicts.

    Args:
        cat: The loaded Iceberg catalog.

    Returns:
        A list of user dicts; empty list if the table has no rows.
    """
    tbl = users_table(cat)
    arrow = tbl.scan().to_arrow()
    return [_row_to_dict(row) for row in arrow.to_pylist()]