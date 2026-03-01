"""Root layout and page-routing callback for the Dash dashboard.

Functions
---------
- :func:`build_layout` — attach the root layout and ``display_page`` callback.
"""

import logging
from typing import Optional
from urllib.parse import parse_qs

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html

from dashboard.callbacks import _admin_forbidden, _unauth_notice, _validate_token
from dashboard.layouts import (
    NAVBAR,
    admin_users_layout,
    analysis_tabs_layout,
    compare_layout,
    forecast_layout,
    home_layout,
    insights_layout,
)

# Module-level logger; intentionally module-scoped for use inside nested callback.
_logger = logging.getLogger(__name__)


def build_layout(app: dash.Dash) -> None:
    """Attach the root layout and page-routing callback to *app*.

    Sets ``app.layout`` to a :class:`~dash.html.Div` containing global stores,
    the navigation bar, the page-content container, the auto-refresh interval,
    and the change-password modal.  Registers the ``display_page`` routing
    callback.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """
    app.layout = html.Div(
        [
            dcc.Location(id="url", refresh=False),
            dcc.Store(id="nav-ticker-store", data=None),
            dcc.Store(id="auth-token-store", storage_type="local"),
            dcc.Store(id="user-profile-store", storage_type="session"),
            NAVBAR,
            html.Div(
                id="page-content",
                className="container-fluid px-4 py-3",
                style={"paddingBottom": "5rem"},
            ),
            dcc.Interval(
                id="registry-refresh",
                interval=30 * 60 * 1000,  # Fix #20: 30 min (was 5 min)
                n_intervals=0,
            ),
            # Change Password modal — triggered by the NAVBAR change-password
            # button (admin_cbs2.toggle_change_password_modal).
            dbc.Modal(
                id="change-password-modal",
                is_open=False,
                backdrop="static",
                children=[
                    dbc.ModalHeader(
                        dbc.ModalTitle("Change Password"),
                        close_button=False,
                    ),
                    dbc.ModalBody([
                        html.Div(id="change-pw-error", className="text-danger small mb-2"),
                        dbc.Row([
                            dbc.Col([
                                dbc.Label("New Password"),
                                dbc.Input(
                                    id="change-pw-new",
                                    type="password",
                                    placeholder="Min 8 chars, at least one digit",
                                ),
                            ]),
                        ], className="mb-3"),
                        dbc.Row([
                            dbc.Col([
                                dbc.Label("Confirm New Password"),
                                dbc.Input(
                                    id="change-pw-confirm",
                                    type="password",
                                    placeholder="Repeat new password",
                                ),
                            ]),
                        ], className="mb-3"),
                    ]),
                    dbc.ModalFooter([
                        dbc.Button(
                            "Cancel",
                            id="change-pw-cancel-btn",
                            color="secondary",
                            outline=True,
                            size="sm",
                            className="me-2",
                        ),
                        dbc.Button("Save", id="change-pw-save-btn", color="primary", size="sm"),
                    ]),
                ],
            ),
        ]
    )

    @app.callback(
        Output("page-content", "children"),
        Input("url", "pathname"),
        Input("url", "search"),
        State("auth-token-store", "data"),
        State("user-profile-store", "data"),
    )
    def display_page(
        pathname: str,
        search: Optional[str],
        stored_token: Optional[str],
        profile_store: Optional[dict],
    ) -> html.Div:
        """Route the current URL pathname to the appropriate page layout.

        Checks for a valid JWT before rendering any page.  The token is read
        first from the ``?token=`` query parameter (takes precedence), then
        from the persistent ``auth-token-store``.  RBAC is enforced for
        ``/insights`` (requires ``insights`` page permission or superuser) and
        ``/admin/users`` (requires superuser or ``admin`` page permission).

        Args:
            pathname: Current URL path provided by :class:`~dash.dcc.Location`.
            search: Query string portion of the URL (e.g. ``"?token=xxx"``).
            stored_token: JWT string from ``localStorage``.
            profile_store: Cached user profile dict from ``user-profile-store``.

        Returns:
            The page-level :class:`~dash.html.Div` layout, or the unauthenticated
            notice if the token is missing or invalid.
        """
        token: Optional[str] = stored_token
        if search:
            qs = parse_qs(search.lstrip("?"))
            url_token = qs.get("token", [None])[0]
            if url_token:
                token = url_token

        payload = _validate_token(token)
        if payload is None:
            _logger.debug("display_page: invalid or missing token for pathname=%s", pathname)
            return _unauth_notice()

        role = payload.get("role", "general")
        perms = (profile_store or {}).get("page_permissions") or {}

        if pathname == "/analysis":
            return analysis_tabs_layout()
        if pathname == "/forecast":
            return forecast_layout()
        if pathname == "/compare":
            return compare_layout()
        if pathname == "/insights":
            if role != "superuser" and not perms.get("insights"):
                _logger.warning(
                    "display_page: access denied to /insights for role=%s", role
                )
                return _admin_forbidden()
            return insights_layout()
        if pathname == "/admin/users":
            if role != "superuser" and not perms.get("admin"):
                _logger.warning(
                    "display_page: access denied to /admin/users for role=%s", role
                )
                return _admin_forbidden()
            return admin_users_layout()
        return home_layout()