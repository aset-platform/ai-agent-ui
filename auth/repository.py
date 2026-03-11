"""Backward-compatible shim.

Re-exports IcebergUserRepository from auth.repo.
"""

from auth.repo.repository import IcebergUserRepository  # noqa: F401

# Module-level export list — kept here as required
# by Python packaging conventions.
_all_exports = ["IcebergUserRepository"]
__all__ = _all_exports
