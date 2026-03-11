"""Global navigation bar for the AI Stock Analysis Dashboard.

Defines the :data:`NAVBAR` constant used across all pages.  The brand label
includes a :class:`~dash.html.Span` with ``id="navbar-page-name"`` that the
``update_navbar_page_name`` callback (in ``routing_cbs``) updates to show the
current page name after the " → " separator.
"""

import dash_bootstrap_components as dbc
from dash import html

NAVBAR = dbc.Navbar(
    dbc.Container(
        [
            dbc.NavbarBrand(
                [
                    html.Span(
                        "Stock Analysis Dashboard", className="fw-semibold"
                    ),
                    html.Span(
                        id="navbar-page-name",
                        className="text-muted fw-normal ms-1",
                        style={"fontSize": "0.875rem"},
                    ),
                ],
                href="/",
            ),
            dbc.Nav(
                [
                    dbc.NavItem(
                        dbc.NavLink(
                            "Home", href="/", className="nav-link-custom"
                        )
                    ),
                    dbc.NavItem(
                        dbc.NavLink(
                            "Analysis",
                            href="/analysis",
                            className="nav-link-custom",
                        )
                    ),
                    dbc.NavItem(
                        dbc.NavLink(
                            "Insights",
                            href="/insights",
                            className="nav-link-custom",
                        ),
                        id="nav-item-insights",
                    ),
                    dbc.NavItem(
                        dbc.NavLink(
                            "Marketplace",
                            href="/marketplace",
                            className="nav-link-custom",
                        ),
                    ),
                    dbc.NavItem(
                        dbc.NavLink(
                            "Admin",
                            href="/admin/users",
                            className="nav-link-custom",
                        ),
                        id="nav-item-admin",
                    ),
                ],
                navbar=True,
                className="ms-auto",
            ),
        ],
        fluid=True,
    ),
    color="light",
    dark=False,
    className="mb-0",
)
