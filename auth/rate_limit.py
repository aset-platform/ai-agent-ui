"""Rate limiting for authentication endpoints.

Provides a :data:`limiter` singleton and a custom 429 handler
that returns a JSON response matching the project's error format.

Usage::

    from auth.rate_limit import limiter

    @router.post("/auth/login")
    @limiter.limit("30/15minutes")
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
