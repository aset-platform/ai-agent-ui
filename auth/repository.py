"""Backward-compatible shim.

Re-exports UserRepository from auth.repo.
IcebergUserRepository is a deprecated alias.
"""

from auth.repo.repository import UserRepository  # noqa: F401

# Deprecated alias — kept for scripts and tests that
# still reference the old name.
IcebergUserRepository = UserRepository

_all_exports = ["UserRepository", "IcebergUserRepository"]
__all__ = _all_exports
