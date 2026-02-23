"""Plotly Dash entry point for the AI Stock Analysis Dashboard.

Bootstraps the application with the DARKLY dark theme, registers all four
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

# ---------------------------------------------------------------------------
# Add project root to sys.path so backend.tools.* can be imported later
# (by the 'Run New Analysis' callback in dashboard/callbacks.py)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, dcc, html

from dashboard.callbacks import register_callbacks
from dashboard.layouts import (
    NAVBAR,
    analysis_layout,
    compare_layout,
    forecast_layout,
    home_layout,
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
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
    title="AI Stock Analysis Dashboard",
    meta_tags=[
        {"name": "viewport", "content": "width=device-width, initial-scale=1"},
    ],
)

# Flask server — usable by gunicorn
server = app.server

app.layout = html.Div(
    [
        # URL tracker (no full-page reload on navigation)
        dcc.Location(id="url", refresh=False),

        # Global store — carries the ticker chosen on the Home page so the
        # Analysis / Forecast dropdowns can pre-select it on navigation
        dcc.Store(id="nav-ticker-store", data=None),

        # Top navigation bar (defined in layouts.py)
        NAVBAR,

        # Page content — swapped by the display_page callback below
        html.Div(
            id="page-content",
            className="container-fluid px-4 py-3",
        ),

        # Auto-refresh interval: rebuilds Home stock cards every 5 minutes
        dcc.Interval(
            id="registry-refresh",
            interval=5 * 60 * 1000,   # milliseconds
            n_intervals=0,
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
)
def display_page(pathname: str) -> html.Div:
    """Route the current URL pathname to the appropriate page layout.

    Args:
        pathname: Current URL path provided by :class:`~dash.dcc.Location`.

    Returns:
        The page-level :class:`~dash.html.Div` layout for the matched route.
        Defaults to :func:`~dashboard.layouts.home_layout` for unknown paths.
    """
    if pathname == "/analysis":
        return analysis_layout()
    if pathname == "/forecast":
        return forecast_layout()
    if pathname == "/compare":
        return compare_layout()
    return home_layout()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting AI Stock Analysis Dashboard on http://127.0.0.1:8050")
    app.run(debug=True, port=8050)
