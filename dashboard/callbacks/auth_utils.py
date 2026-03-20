"""JWT authentication and backend API helpers for the dashboard callbacks.

Provides token validation, unauthenticated/forbidden notice components, and
a thin HTTP wrapper around the FastAPI backend.

Example::

    from dashboard.callbacks.auth_utils import _validate_token, _api_call
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import parse_qs

from dash import html

# Module-level logger — mutable but kept here as a module-level singleton
logger = logging.getLogger(__name__)

_FRONTEND_LOGIN_URL = (
    os.environ.get("FRONTEND_URL", "http://localhost:3000") + "/login"
)
# Module-level configuration constant — kept module-level for shared access
_BACKEND_HOST = os.environ.get(
    "BACKEND_URL",
    "http://127.0.0.1:8181",
)
_BACKEND_URL = f"{_BACKEND_HOST}/v1"


def _validate_token(token: str | None) -> dict[str, Any] | None:
    """Decode and validate a JWT access token.

    Reads ``JWT_SECRET_KEY`` from the environment.  Returns the decoded
    payload if the token is valid and of type ``"access"``; returns ``None``
    for any failure (missing key, invalid signature, expired token, wrong
    type).

    Args:
        token: Raw JWT string, or ``None``.

    Returns:
        Decoded payload dict, or ``None`` if invalid.
    """
    if not token:
        return None
    secret = os.environ.get("JWT_SECRET_KEY", "")
    if not secret:
        logger.warning(
            "_validate_token: JWT_SECRET_KEY not set"
            " — all dashboard requests will be denied."
        )
        return None
    try:
        from jose import jwt as _jwt

        payload = _jwt.decode(token, secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            return None
        return payload
    except Exception as exc:
        logger.debug("Token validation failed: %s", exc)
        return None


def _unauth_notice() -> html.Div:
    """Return a Dash layout component shown when the user is not authenticated.

    Displays a centred card with a link back to the Next.js login page.

    Returns:
        A :class:`~dash.html.Div` containing the unauthenticated UI.
    """
    return html.Div(
        html.Div(
            [
                html.Div(
                    "🔒",
                    style={"fontSize": "2.5rem", "marginBottom": "0.75rem"},
                ),
                html.H5(
                    "Authentication required", className="mb-2 fw-semibold"
                ),
                html.P(
                    "Your session has expired or you are not signed in.",
                    className="text-muted mb-3",
                    style={"fontSize": "0.9rem"},
                ),
                html.A(
                    "Sign in →",
                    href=_FRONTEND_LOGIN_URL,
                    target="_top",
                    className="btn btn-primary btn-sm px-4",
                ),
            ],
            style={
                "background": "var(--card-bg)",
                "border": "1px solid var(--border)",
                "borderRadius": "1rem",
                "padding": "2.5rem",
                "maxWidth": "360px",
                "textAlign": "center",
            },
        ),
        style={
            "display": "flex",
            "alignItems": "center",
            "justifyContent": "center",
            "minHeight": "60vh",
        },
    )


def _admin_forbidden() -> html.Div:
    """Return a Dash layout for non-superuser /admin/* access.

    Returns:
        A :class:`~dash.html.Div` with a 403-style message and a back link.
    """
    return html.Div(
        html.Div(
            [
                html.Div(
                    "⛔",
                    style={"fontSize": "2.5rem", "marginBottom": "0.75rem"},
                ),
                html.H5("Access denied", className="mb-2 fw-semibold"),
                html.P(
                    "This page requires superuser privileges.",
                    className="text-muted mb-3",
                    style={"fontSize": "0.9rem"},
                ),
                html.A(
                    "← Back to home",
                    href="/",
                    className="btn btn-outline-secondary btn-sm px-4",
                ),
            ],
            style={
                "background": "var(--card-bg)",
                "border": "1px solid var(--border)",
                "borderRadius": "1rem",
                "padding": "2.5rem",
                "maxWidth": "360px",
                "textAlign": "center",
            },
        ),
        style={
            "display": "flex",
            "alignItems": "center",
            "justifyContent": "center",
            "minHeight": "60vh",
        },
    )


def _resolve_token(
    stored_token: str | None,
    url_search: str | None,
) -> str | None:
    """Return the best available JWT, preferring the URL query parameter.

    Args:
        stored_token: Token persisted in ``auth-token-store`` localStorage.
        url_search: URL query string, e.g. ``"?token=eyJ..."``.

    Returns:
        JWT string or ``None`` if neither source has a token.
    """
    token = stored_token
    if url_search:
        qs = parse_qs(url_search.lstrip("?"))
        url_token = qs.get("token", [None])[0]
        if url_token:
            token = url_token
    return token


def _api_call(
    method: str,
    path: str,
    token: str | None,
    json_body: dict[str, Any] | None = None,
) -> Any | None:
    """Make an authenticated HTTP request to the FastAPI backend.

    Args:
        method: HTTP method — ``"get"``, ``"post"``, ``"patch"``,
            or ``"delete"``.
        path: URL path starting with ``"/"`` (e.g. ``"/users"``).
        token: JWT access token; ``None`` causes an immediate ``None`` return.
        json_body: Optional JSON-serialisable request body for POST/PATCH.

    Returns:
        The :mod:`requests` ``Response`` object, or ``None`` on connection
        error or missing token.
    """
    if not token:
        return None
    try:
        import requests as _req  # lazy import — avoids startup cost

        url = f"{_BACKEND_URL}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        kwargs: dict[str, Any] = {"headers": headers, "timeout": 10}
        if json_body is not None:
            kwargs["json"] = json_body
        fn = getattr(_req, method.lower())
        return fn(url, **kwargs)
    except Exception as exc:
        logger.error("API call %s %s failed: %s", method.upper(), path, exc)
        return None


def _fetch_user_tickers(
    token: str | None,
) -> set | None:
    """Fetch the authenticated user's linked tickers.

    Calls ``GET /users/me/tickers`` and returns the set
    of ticker symbols.  Returns ``None`` when there is
    no token or the API call fails — callers should fall
    back to showing all tickers.

    Args:
        token: JWT access token, or ``None``.

    Returns:
        Set of ticker strings, or ``None`` on failure.
    """
    if not token:
        return None
    resp = _api_call("get", "/users/me/tickers", token)
    if resp is None or not resp.ok:
        return None
    data = resp.json()
    if isinstance(data, list):
        return set(data)
    if isinstance(data, dict):
        return set(data.get("tickers", []))
    return None
