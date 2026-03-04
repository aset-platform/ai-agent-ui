"""Profile store callback for the dashboard.

Handles:

- Fetching ``GET /auth/me`` to populate the ``user-profile-store``.

The profile store is consumed by:

- ``display_page`` (RBAC routing) — reads ``role`` and ``page_permissions``.

All other profile management (Edit Profile, Change Password, Sign Out) is
handled by the frontend chat application (not the Dash dashboard).

Example::

    from dashboard.callbacks.profile_cbs import register
    register(app)
"""

import logging
from typing import Any, Dict, Optional

import dash

from dashboard.callbacks.auth_utils import _api_call, _validate_token

# Module-level logger; kept at module scope intentionally (not inside a class).
_logger = logging.getLogger(__name__)


def register(app: dash.Dash) -> None:
    """Register the minimal profile store callback with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """

    @app.callback(
        dash.Output("user-profile-store", "data"),
        dash.Input("auth-token-store", "data"),
        prevent_initial_call=False,
    )
    def load_user_profile(token: Optional[str]) -> Optional[Dict[str, Any]]:
        """Fetch and cache the authenticated user's profile.

        Used by the ``display_page`` RBAC routing callback to check role and
        ``page_permissions`` without re-decoding the JWT.

        Args:
            token: JWT access token from ``auth-token-store``.

        Returns:
            User profile dict from ``GET /auth/me``, or ``None`` on failure.
        """
        if not token or _validate_token(token) is None:
            _logger.debug(
                "load_user_profile: missing or invalid token; returning None."
            )
            return None
        resp = _api_call("get", "/auth/me", token)
        if resp and resp.status_code == 200:
            try:
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "load_user_profile: failed to decode JSON from /auth/me: %s",
                    exc,
                )
                return None
        _logger.warning(
            "load_user_profile: unexpected response from /auth/me: %s",
            getattr(resp, "status_code", "no response"),
        )
        return None
