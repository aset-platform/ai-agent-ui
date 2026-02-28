"""Plotly Dash entry point for the AI Stock Analysis Dashboard.

Bootstraps the application with the FLATLY light theme, registers all four
page routes (Home, Analysis, Forecast, Compare), wires interactive callbacks,
and launches the development server on port 8050.

Usage::

    # from the project root with demoenv activated:
    python dashboard/app.py

    # or with explicit port:
    python dashboard/app.py  # always port 8050

The module also exposes a ``server`` attribute (the underlying Flask WSGI
object) for deployment with gunicorn::

    gunicorn "dashboard.app:server" --bind 0.0.0.0:8050
"""

import logging
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs

# ---------------------------------------------------------------------------
# Add project root to sys.path so backend.tools.* can be imported later
# (by the 'Run New Analysis' callback in dashboard/callbacks.py)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Load .env files into os.environ so JWT_SECRET_KEY and other backend
# settings are available to callbacks even when not exported in the shell.
# Mirrors the same pattern used in scripts/seed_admin.py.
# ---------------------------------------------------------------------------
import os as _os


def _load_dotenv(path: Path) -> None:
    """Parse key=value pairs from *path* into os.environ (no-op if absent)."""
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as _fh:
        for _line in _fh:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            _v = _v.strip().strip("'\"")
            if _k and _k not in _os.environ:
                _os.environ[_k] = _v


_load_dotenv(_PROJECT_ROOT / ".env")
_load_dotenv(_PROJECT_ROOT / "backend" / ".env")

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html

from dashboard.callbacks import (
    _admin_forbidden,
    _unauth_notice,
    _validate_token,
    register_callbacks,
)
from dashboard.layouts import (
    NAVBAR,
    admin_users_layout,
    analysis_layout,
    compare_layout,
    forecast_layout,
    home_layout,
    insights_layout,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dash application
# ---------------------------------------------------------------------------

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    suppress_callback_exceptions=True,
    title="AI Stock Analysis Dashboard",
    meta_tags=[
        {"name": "viewport", "content": "width=device-width, initial-scale=1"},
    ],
)

# Flask server — usable by gunicorn
server = app.server


@server.after_request
def allow_iframe(response):
    """Allow this Dash app to be embedded in an iframe from any origin.

    Sets ``X-Frame-Options: ALLOWALL`` and a permissive
    ``Content-Security-Policy: frame-ancestors *`` header on every
    response so the frontend SPA can embed the dashboard in an
    ``<iframe>`` without browser security errors.

    Args:
        response: The Flask response object for the current request.

    Returns:
        The response with iframe-embedding headers added.
    """
    response.headers["X-Frame-Options"] = "ALLOWALL"
    response.headers["Content-Security-Policy"] = "frame-ancestors *"
    return response

app.layout = html.Div(
    [
        # URL tracker (no full-page reload on navigation)
        dcc.Location(id="url", refresh=False),

        # Global store — carries the ticker chosen on the Home page so the
        # Analysis / Forecast dropdowns can pre-select it on navigation
        dcc.Store(id="nav-ticker-store", data=None),

        # Auth store — persists the JWT access token received via ?token= query
        # param from the Next.js iframe src so callbacks can validate requests
        dcc.Store(id="auth-token-store", storage_type="local"),

        # Top navigation bar (defined in layouts.py)
        NAVBAR,

        # Page content — swapped by the display_page callback below
        # Extra bottom padding keeps content clear of the fixed Next.js nav FAB
        # and the Plotly watermark that both sit in the bottom corners.
        html.Div(
            id="page-content",
            className="container-fluid px-4 py-3",
            style={"paddingBottom": "5rem"},
        ),

        # Auto-refresh interval: rebuilds Home stock cards every 5 minutes
        dcc.Interval(
            id="registry-refresh",
            interval=5 * 60 * 1000,   # milliseconds
            n_intervals=0,
        ),

        # ── Global change-password modal (accessible from NAVBAR on any page) ──
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
                    dbc.Button(
                        "Save",
                        id="change-pw-save-btn",
                        color="primary",
                        size="sm",
                    ),
                ]),
            ],
        ),
    ]
)

# Register all interactive callbacks defined in callbacks.py
register_callbacks(app)

# ---------------------------------------------------------------------------
# Page routing callback
# ---------------------------------------------------------------------------


@app.callback(
    Output("page-content", "children"),
    Input("url", "pathname"),
    Input("url", "search"),
    State("auth-token-store", "data"),
)
def display_page(
    pathname: str,
    search: Optional[str],
    stored_token: Optional[str],
) -> html.Div:
    """Route the current URL pathname to the appropriate page layout.

    Checks for a valid JWT before rendering any page.  The token is read
    first from the ``?token=`` query parameter (takes precedence so a
    freshly issued token from the Next.js frontend is always accepted),
    then from the persistent ``auth-token-store`` client-side store.

    Args:
        pathname: Current URL path provided by :class:`~dash.dcc.Location`.
        search: Query string portion of the URL (e.g. ``"?token=xxx"``).
        stored_token: JWT string previously persisted in ``localStorage``
            via the ``auth-token-store``.

    Returns:
        The page-level :class:`~dash.html.Div` layout for the matched route,
        or the unauthenticated notice if the token is missing or invalid.
        Defaults to :func:`~dashboard.layouts.home_layout` for unknown paths.
    """
    # Resolve token — prefer URL param (freshest), fall back to localStorage
    token: Optional[str] = stored_token
    if search:
        qs = parse_qs(search.lstrip("?"))
        url_token = qs.get("token", [None])[0]
        if url_token:
            token = url_token

    if _validate_token(token) is None:
        return _unauth_notice()

    if pathname == "/analysis":
        return analysis_layout()
    if pathname == "/forecast":
        return forecast_layout()
    if pathname == "/compare":
        return compare_layout()
    if pathname == "/insights":
        return insights_layout()
    if pathname == "/admin/users":
        payload = _validate_token(token)
        if payload is None or payload.get("role") != "superuser":
            return _admin_forbidden()
        return admin_users_layout()
    return home_layout()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting AI Stock Analysis Dashboard on http://127.0.0.1:8050")
    app.run(debug=True, port=8050)
