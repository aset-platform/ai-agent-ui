"""auth.endpoints — modular router sub-package for all auth API routes.

The public API is :func:`create_auth_router`.  Call once from
``backend/main.py`` and pass the result to ``app.include_router()``::

    from auth.endpoints import create_auth_router
    app.include_router(create_auth_router())
"""

import logging

from fastapi import APIRouter

from auth.endpoints import (
    admin_routes,
    auth_routes,
    oauth_routes,
    profile_routes,
    session_routes,
    subscription_routes,
    ticker_routes,
    user_routes,
)

logger = logging.getLogger(__name__)

# Module-level export list; kept here as required by the package public API.
_all_public = ["create_auth_router", "get_ticker_router"]

__all__ = _all_public


def create_auth_router() -> APIRouter:
    """Build and return the auth / user management :class:`~fastapi.APIRouter`.

    Calls each sub-module's ``register(router)`` function to attach all
    endpoints to a single router, then returns it.

    Returns:
        A configured :class:`~fastapi.APIRouter` with all auth, user, profile,
        OAuth, and admin endpoints registered.

    Example:
        >>> from auth.endpoints import create_auth_router
        >>> router = create_auth_router()  # doctest: +SKIP
    """
    router = APIRouter()
    auth_routes.register(router)
    user_routes.register(router)
    profile_routes.register(router)
    oauth_routes.register(router)
    admin_routes.register(router)
    session_routes.register(router)
    subscription_routes.register(router)
    logger.debug(
        "Auth router created with all"
        " sub-routes registered.",
    )
    return router


def get_ticker_router() -> APIRouter:
    """Return the user-ticker management router.

    This router uses its own ``/users/me`` prefix and is
    mounted separately from the main auth router.

    Returns:
        The ticker :class:`~fastapi.APIRouter`.
    """
    return ticker_routes.router
