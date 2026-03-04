"""Reusable *Refresh Data & Run Analysis* button component.

Provides :func:`refresh_button_group`, a factory that returns a Dash layout
fragment consisting of a button, a ``dcc.Loading(type="dot")`` spinner, and
a status icon ``<span>``.  The ``id_prefix`` parameter makes all element IDs
unique so the same component can be placed on multiple dashboard pages.

IDs generated (given ``id_prefix="forecast-refresh"``)::

    forecast-refresh-btn      — the button
    forecast-refresh-status   — inner status span (callback target)

Usage::

    from dashboard.components.refresh_button import refresh_button_group

    # In a layout function:
    refresh_button_group("forecast-refresh")

    # In a callback:
    Input("forecast-refresh-btn", "n_clicks")
    Output("forecast-refresh-status", "children")
"""

import dash_bootstrap_components as dbc
from dash import dcc, html


def refresh_button_group(
    id_prefix: str,
    label: str = "Refresh Data & Run Analysis",
    color: str = "success",
    size: str = "sm",
    icon_only: bool = False,
    tooltip: str = "Refresh Data & Run Analysis",
) -> html.Div:
    """Return a button + dot-spinner + status-icon layout fragment.

    The ``dcc.Loading`` spinner and the status icon live inside a
    fixed-width wrapper so they never overlap the button.

    When *icon_only* is ``True``, the button renders as a compact
    circular icon with a Bootstrap tooltip on hover.

    Args:
        id_prefix: Unique prefix for element IDs
            (e.g. ``"forecast-refresh"``).
        label: Button label text (ignored when *icon_only*).
        color: Bootstrap colour name for the button.
        size: Bootstrap size string (``"sm"``, ``"md"``).
        icon_only: If ``True``, render a compact circular
            icon button instead of a labelled button.
        tooltip: Hover tooltip text (used when *icon_only*).

    Returns:
        :class:`~dash.html.Div` containing the button, loading
        spinner, and status icon.
    """
    btn_id = f"{id_prefix}-btn"
    status_id = f"{id_prefix}-status"

    if icon_only:
        btn = dbc.Button(
            "\u21bb",
            id=btn_id,
            color=color,
            size=size,
            className="refresh-icon-btn",
        )
        btn_with_tooltip = html.Span(
            [
                btn,
                dbc.Tooltip(
                    tooltip,
                    target=btn_id,
                    placement="top",
                ),
            ],
        )
    else:
        btn_with_tooltip = dbc.Button(
            label,
            id=btn_id,
            color=color,
            size=size,
        )

    return html.Div(
        [
            btn_with_tooltip,
            # Fixed-width container keeps the spinner / icon
            # from overlapping the button.
            html.Div(
                dcc.Loading(
                    id=f"{id_prefix}-loading",
                    type="dot",
                    color="#198754",
                    children=html.Span(id=status_id),
                ),
                className="refresh-status-box",
            ),
        ],
        className=("d-flex align-items-center" " refresh-btn-group"),
    )
