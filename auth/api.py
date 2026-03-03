"""Backward-compatible shim — re-exports create_auth_router from auth.endpoints."""

import logging

from auth.endpoints import create_auth_router  # noqa: F401

logger = logging.getLogger(__name__)

# Module-level export list — kept here for package compatibility
_all_exports = ["create_auth_router"]

__all__ = _all_exports
