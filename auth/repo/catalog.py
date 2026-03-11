"""Iceberg catalog loader and table-accessor helpers for auth tables.

Functions
---------
- :func:`get_catalog`
- :func:`users_table`
- :func:`audit_table`
- :func:`user_tickers_table`
- :func:`scan_all_users`
"""

import logging
import os
from typing import Any, Dict, List

from auth.repo.schemas import (
    _AUDIT_LOG_TABLE,
    _USER_TICKERS_TABLE,
    _USERS_TABLE,
    _row_to_dict,
)

# Module-level logger; not mutable state — safe to keep at module level.
_logger = logging.getLogger(__name__)

# Fix #12: module-level singleton catalog avoids repeated catalog loading
# across auth operations within the same process lifetime.  The catalog is
# set by the first successful call to ``get_catalog()`` and reused thereafter.
_catalog_singleton = None


def get_catalog(root: str):
    """Load and return the Iceberg catalog, caching it as a process singleton.

    On the first call the catalog is loaded from ``.pyiceberg.yaml`` using an
    absolute ``sqlite://`` URI derived from *root*, so no ``os.chdir`` is
    needed.  Subsequent calls return the cached instance immediately.

    Args:
        root: Absolute path to the project root directory.

    Returns:
        A :class:`pyiceberg.catalog.sql.SqlCatalog` instance.

    Raises:
        RuntimeError: If the catalog cannot be loaded.
    """
    global _catalog_singleton  # noqa: PLW0603 — module-level singleton pattern
    if _catalog_singleton is not None:
        return _catalog_singleton

    from pyiceberg.catalog import load_catalog

    # Fix #12: resolve the SQLite URI with an absolute path so we never need
    # os.chdir() (which has global side effects on the whole process).
    # Paths centralised in backend/paths.py; the *root* parameter is now
    # only used as a fallback for the legacy cwd-based catalog load below.
    try:
        from paths import ICEBERG_CATALOG_URI, ICEBERG_WAREHOUSE_URI

        cat = load_catalog(
            "local",
            **{
                "type": "sql",
                "uri": ICEBERG_CATALOG_URI,
                "warehouse": str(ICEBERG_WAREHOUSE_URI),
            },
        )
        _logger.debug("Iceberg catalog loaded (singleton).")
        _catalog_singleton = cat
        return cat
    except Exception as exc:
        _logger.warning(
            "Absolute-URI catalog load failed (%s);"
            " falling back to load_catalog('local').",
            exc,
        )

    # Fallback: temporarily set cwd so the relative URI
    # in .pyiceberg.yaml resolves.
    orig_cwd = os.getcwd()
    try:
        os.chdir(root)
        cat = load_catalog("local")
        _logger.debug("Iceberg catalog loaded via cwd fallback.")
        _catalog_singleton = cat
        return cat
    except Exception as exc:
        raise RuntimeError(
            "Failed to load Iceberg catalog. "
            "Check that .pyiceberg.yaml exists and data/iceberg/ is writable."
        ) from exc
    finally:
        os.chdir(orig_cwd)


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


def user_tickers_table(cat):
    """Return the open ``auth.user_tickers`` Iceberg table.

    Args:
        cat: The loaded Iceberg catalog.

    Returns:
        The ``auth.user_tickers``
        :class:`pyiceberg.table.Table`.
    """
    return cat.load_table(_USER_TICKERS_TABLE)


def scan_all_users(cat) -> List[Dict[str, Any]]:
    """Read every row from ``auth.users`` as a list of plain dicts.

    Fix #11: streams the Arrow record batches instead of materialising the
    entire table into a single in-memory Arrow table, then yields them one at
    a time.  The final result is still a ``list`` (required by callers), but
    peak memory during the scan is proportional to a single batch rather than
    the whole table.

    Args:
        cat: The loaded Iceberg catalog.

    Returns:
        A list of user dicts; empty list if the table has no rows.
    """
    tbl = users_table(cat)
    result: List[Dict[str, Any]] = []
    # Fix #11: iterate over Arrow record batches instead
    # of converting the whole
    # table to a Python list in one shot — keeps peak memory proportional to a
    # single batch rather than all rows simultaneously.
    for batch in tbl.scan().to_arrow().to_batches():
        for row in batch.to_pylist():
            result.append(_row_to_dict(row))
    return result
