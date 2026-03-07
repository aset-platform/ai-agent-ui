"""Reusable error-overlay banner for the Dash dashboard.

Provides :func:`error_overlay_container` (the layout placeholder)
and :func:`make_error_banner` (the banner factory).  The banner
renders as a fixed-position Bootstrap alert at the top of the
viewport.  It auto-dismisses after 8 seconds via
``dbc.Alert(duration=8000)`` and can also be dismissed manually.

IDs::

    error-overlay-container   — outer wrapper (callback target)

Usage in a callback::

    from dashboard.components.error_overlay import (
        make_error_banner,
    )
    return make_error_banner("RELIANCE.NS: Network error")
"""

import dash_bootstrap_components as dbc
from dash import html


def error_overlay_container() -> html.Div:
    """Return the hidden placeholder that callbacks populate.

    Place this once at the top of ``app.layout``.

    Returns:
        Empty :class:`~dash.html.Div` with
        ``id="error-overlay-container"``.
    """
    return html.Div(id="error-overlay-container")


def make_error_banner(message: str) -> html.Div:
    """Build a dismissible error banner that auto-fades.

    The banner is ``position: fixed`` at the top of the
    viewport so it overlays all page content.  Uses
    ``dbc.Alert(duration=8000)`` for built-in auto-dismiss
    (no separate timer or callback needed).

    Args:
        message: User-friendly error string to display.

    Returns:
        :class:`~dash.html.Div` containing the alert.
    """
    return html.Div(
        dbc.Alert(
            [
                html.Span(
                    "\u2717",
                    className="me-2 fw-bold",
                ),
                html.Span(message),
            ],
            color="danger",
            dismissable=True,
            is_open=True,
            duration=8000,
            className="error-overlay-banner mb-0",
        ),
        className="error-overlay-wrapper",
    )
