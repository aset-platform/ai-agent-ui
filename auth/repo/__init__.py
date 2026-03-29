"""auth.repo -- PostgreSQL-backed user repository + Iceberg audit data.

The public API is :class:`UserRepository`.  All callers should import
from here or from :mod:`auth.repository` (the backward-compatible shim)::

    from auth.repo import UserRepository
    # or
    from auth.repository import UserRepository
"""

import logging

from auth.repo.repository import (  # noqa: F401
    IcebergUserRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)

# Module-level export list -- kept here as required
# by Python packaging conventions.
_all_exports = ["UserRepository", "IcebergUserRepository"]

__all__ = _all_exports
