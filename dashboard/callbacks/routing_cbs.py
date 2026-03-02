"""Routing and auth-token Dash callbacks for the AI Stock Analysis Dashboard.

Registers the ``store_token_from_url`` callback (extracts JWT from
``?token=`` query parameter and persists it to ``localStorage``), the
``auth-token-store`` callback group, and the ``update_navbar_page_name``
callback that keeps the navbar brand suffix in sync with the current route.

Example::

    from dashboard.callbacks.routing_cbs import register
    register(app)
"""

import logging
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

from dash import Input, Output, State, no_update

# Module-level logger; kept at module scope as a conventional singleton.
logger = logging.getLogger(__name__)

# Module-level mapping of routes to display suffixes.  Prefixed with '_' to
# signal that it is an internal implementation detail of this module.
_PAGE_NAMES = {
    "/": "",
    "/analysis": " → Analysis",
    "/forecast": " → Forecast",
    "/compare": " → Compare Stocks",
    "/insights": " → Insights",
    "/admin/users": " → Admin",
}


def register(app) -> None:
    """Register routing and auth-store callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """

    @app.callback(
        Output("auth-token-store", "data"),
        Input("url", "search"),
        prevent_initial_call=False,
    )
    def store_token_from_url(search: Optional[str]) -> Optional[str]:
        """Persist a JWT access token from the URL query string to localStorage.

        When the Next.js frontend embeds the dashboard in an ``<iframe>`` it
        appends ``?token=<jwt>`` to the URL.  This callback intercepts the
        query parameter and writes the token to the ``auth-token-store``
        (``storage_type="local"``), so it survives page navigation within
        the dashboard without the token re-appearing in the URL.

        If the URL does not contain a ``token`` parameter the callback
        returns :data:`~dash.no_update` so any previously stored value is
        preserved.

        Args:
            search: The query string portion of the current URL, e.g.
                ``"?token=eyJ..."``.  May be ``None`` or an empty string.

        Returns:
            The raw JWT string to store, or :data:`~dash.no_update` when
            the URL contains no ``token`` parameter.
        """
        if not search:
            return no_update
        qs = parse_qs(search.lstrip("?"))
        token_list = qs.get("token")
        if not token_list:
            return no_update
        logger.debug("JWT token extracted from URL query string.")
        return token_list[0]

    @app.callback(
        Output("navbar-page-name", "children"),
        Input("url", "pathname"),
    )
    def update_navbar_page_name(pathname: Optional[str]) -> str:
        """Update the page-name suffix in the navbar brand label.

        Reads the current URL pathname and returns the matching human-readable
        suffix (e.g. ``" → Analysis"`` for ``/analysis``).  The suffix is
        rendered inside the :data:`~dashboard.layouts.navbar.NAVBAR` brand
        element's ``navbar-page-name`` span.

        Args:
            pathname: Current URL path from :class:`~dash.dcc.Location`.

        Returns:
            The page-name suffix string, or an empty string for the home route.
        """
        resolved = pathname or "/"
        page_name = _PAGE_NAMES.get(resolved, "")
        logger.debug(
            "Navbar page name updated to %r for pathname %r.", page_name, resolved
        )
        return page_name

    @app.callback(
        Output("nav-item-insights", "style"),
        Output("nav-item-admin", "style"),
        Input("user-profile-store", "data"),
    )
    def update_nav_visibility(profile: Optional[Dict[str, Any]]):
        """Show or hide Insights and Admin nav links based on user role/permissions.

        Superusers always see both links.  General users see a link only when
        the corresponding ``page_permissions`` key is ``True``.  While the
        profile is loading (``None``) both links are hidden.

        Args:
            profile: User profile dict from ``user-profile-store``, or ``None``.

        Returns:
            Tuple of (insights style dict, admin style dict).
        """
        _hide: Dict[str, str] = {"display": "none"}
        _show: Dict[str, str] = {}

        if not profile:
            return _hide, _hide

        role = profile.get("role", "general")
        perms: Dict[str, Any] = profile.get("page_permissions") or {}

        insights_ok = role == "superuser" or bool(perms.get("insights"))
        admin_ok = role == "superuser" or bool(perms.get("admin"))

        return (_show if insights_ok else _hide), (_show if admin_ok else _hide)
