"""Dash application factory for the AI Stock Analysis Dashboard.

Functions
---------
- :func:`create_app` — create and configure the :class:`~dash.Dash` instance.
"""

import logging

import dash
import dash_bootstrap_components as dbc

# Module-level logger; kept at module scope as a private sentinel for the factory.
_logger = logging.getLogger(__name__)


def create_app() -> dash.Dash:
    """Create and configure the :class:`~dash.Dash` application.

    Initialises Dash with the FLATLY Bootstrap theme, sets a custom title,
    adds a viewport meta tag, and attaches the ``allow_iframe`` after-request
    hook so the dashboard can be embedded in an iframe from any origin.

    Returns:
        A configured :class:`~dash.Dash` instance.  The underlying Flask
        WSGI object is accessible as ``app.server``.
    """
    app = dash.Dash(
        __name__,
        external_stylesheets=[dbc.themes.FLATLY],
        suppress_callback_exceptions=True,
        title="AI Stock Analysis Dashboard",
        meta_tags=[
            {"name": "viewport", "content": "width=device-width, initial-scale=1"},
        ],
    )

    @app.server.after_request
    def allow_iframe(response):
        """Allow embedding in an iframe from any origin.

        Args:
            response: The Flask response object.

        Returns:
            The response with iframe-embedding headers set.
        """
        response.headers["X-Frame-Options"] = "ALLOWALL"
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        return response

    _logger.debug("Dash application created")
    return app
