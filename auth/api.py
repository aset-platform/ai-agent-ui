"""Backward-compatible shim.

Re-exports create_auth_router from auth.endpoints.
"""

import logging

from auth.endpoints import (  # noqa: F401
    create_auth_router,
    get_ticker_router,
)

logger = logging.getLogger(__name__)

# Module-level export list — kept here for package compatibility
_all_exports = [
    "create_auth_router",
    "get_ticker_router",
]

__all__ = _all_exports
