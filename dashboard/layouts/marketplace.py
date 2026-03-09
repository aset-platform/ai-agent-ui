"""Ticker Marketplace page layout.

Provides :func:`marketplace_layout`, which builds the marketplace
page where users can browse all available tickers from the central
registry and add or remove them from their personal watchlist.
"""

import dash_bootstrap_components as dbc
from dash import dcc, html

_PAGE_SIZE_OPTS = [
    {"label": "10 / page", "value": "10"},
    {"label": "25 / page", "value": "25"},
    {"label": "50 / page", "value": "50"},
    {"label": "100 / page", "value": "100"},
]


def marketplace_layout() -> html.Div:
    """Build the Ticker Marketplace page layout.

    Displays a searchable, sortable, paginated table of all
    tickers in the central registry with Add/Remove buttons
    so users can manage their personal watchlist.

    Returns:
        :class:`~dash.html.Div` representing the full
        marketplace page.
    """
    return html.Div(
        [
            html.H4(
                "Ticker Marketplace",
                className="mb-1",
            ),
            html.P(
                "Browse all available tickers and"
                " add them to your watchlist",
                className="text-muted mb-3",
            ),
            # Alert area for success/error messages
            html.Div(
                id="marketplace-alert",
                className="mb-3",
            ),
            # Search input
            dbc.Input(
                id="marketplace-search",
                placeholder=("Search by ticker, company" " or market\u2026"),
                debounce=True,
                size="sm",
                className="mb-3",
            ),
            # Table with loading spinner
            dcc.Loading(
                id="loading-marketplace",
                type="circle",
                color="#4f46e5",
                children=html.Div(
                    id="marketplace-table-container",
                    **{"data-testid": ("marketplace-grid")},
                ),
            ),
            # Pagination row
            dbc.Row(
                [
                    dbc.Col(
                        html.Small(
                            id="marketplace-count-text",
                            className="text-muted",
                        ),
                        width="auto",
                        className="my-auto",
                    ),
                    dbc.Col(
                        dbc.Pagination(
                            id="marketplace-pagination",
                            max_value=1,
                            active_page=1,
                            fully_expanded=False,
                            size="sm",
                            className=("justify-content-end mb-0"),
                        ),
                        className=("d-flex" " justify-content-end" " my-auto"),
                    ),
                    dbc.Col(
                        dbc.Select(
                            id="marketplace-page-size",
                            options=_PAGE_SIZE_OPTS,
                            value="10",
                            size="sm",
                            style={"width": "120px"},
                        ),
                        width="auto",
                        className="my-auto",
                    ),
                ],
                className="mt-2 align-items-center",
            ),
            # Hidden stores
            dcc.Store(
                id="marketplace-store",
                data=[],
            ),
            dcc.Store(
                id="marketplace-user-tickers",
                data=[],
            ),
            dcc.Store(
                id="marketplace-sort-store",
                data={
                    "col": None,
                    "dir": "none",
                },
            ),
        ]
    )
