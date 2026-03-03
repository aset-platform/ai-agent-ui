"""auth.repo — Iceberg-backed repository sub-package for user and audit data.

The public API is :class:`IcebergUserRepository`.  All callers should import
from here or from :mod:`auth.repository` (the backward-compatible shim)::

    from auth.repo import IcebergUserRepository
    # or
    from auth.repository import IcebergUserRepository
"""

import logging

from auth.repo.repository import IcebergUserRepository

logger = logging.getLogger(__name__)

# Module-level export list — kept here as required by Python packaging conventions.
_all_exports = ["IcebergUserRepository"]

__all__ = _all_exports
