"""Rate limiting for authentication endpoints.

Provides a :data:`limiter` singleton, per-endpoint limit callables,
and a custom 429 handler that returns a JSON response matching the
project's error format.

Limit strings are read from :class:`~config.Settings` so they can be
overridden via environment variables (e.g. ``RATE_LIMIT_LOGIN``).

Usage::

    from auth.rate_limit import limiter, login_limit

    @router.post("/auth/login")
    @limiter.limit(login_limit)
    def login(request: Request, ...):
        ...
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

_logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


# ------------------------------------------------------------------
# Dynamic limit callables — read from Settings at request time so
# env-var overrides (e.g. RATE_LIMIT_LOGIN=200/15minutes) take
# effect without restarting.
# ------------------------------------------------------------------


def _get_settings():
    """Lazy import to avoid circular dependency."""
    from config import get_settings

    return get_settings()


def login_limit(*_args, **_kwargs) -> str:
    """Return the login rate-limit string from config."""
    return _get_settings().rate_limit_login


def register_limit(*_args, **_kwargs) -> str:
    """Return the register/password-reset rate-limit from config."""
    return _get_settings().rate_limit_register


def oauth_limit(*_args, **_kwargs) -> str:
    """Return the OAuth callback rate-limit from config."""
    return _get_settings().rate_limit_oauth


def rate_limit_exceeded_handler(
    request: Request,
    exc: RateLimitExceeded,
) -> JSONResponse:
    """Return a 429 JSON response when a rate limit is exceeded.

    Args:
        request: The incoming HTTP request.
        exc: The rate-limit exception raised by slowapi.

    Returns:
        A :class:`~fastapi.responses.JSONResponse` with status 429.
    """
    _logger.warning(
        "Rate limit exceeded: path=%s remote=%s",
        request.url.path,
        request.client.host if request.client else "unknown",
    )
    return JSONResponse(
        status_code=429,
        content={
            "detail": ("Too many requests. Please try again later."),
        },
    )
