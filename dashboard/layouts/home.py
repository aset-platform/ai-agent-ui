"""Home / Stock Overview page layout for the AI Stock Analysis Dashboard.

Provides :func:`home_layout`, which builds the landing page containing
the search bar, registry dropdown, market filter buttons, stock cards
container, and pagination controls.
"""

import dash_bootstrap_components as dbc
from dash import dcc, html

from dashboard.layouts.helpers import _load_registry


def home_layout() -> html.Div:
    """Build the Home / Stock Overview page layout.

    Contains a search bar, a dropdown populated from the registry, and a
    stock-cards container that is filled by the ``refresh_stock_cards``
    callback.

    Returns:
        :class:`~dash.html.Div` representing the full home page.
    """
    registry = _load_registry()
    ticker_options = [{"label": t, "value": t} for t in sorted(registry.keys())]

    return html.Div([
        # ── Search / quick-select row ─────────────────────────────────────
        dbc.Row([
            dbc.Col([
                dbc.InputGroup([
                    dbc.Input(
                        id="ticker-search-input",
                        placeholder="Enter ticker symbol (e.g. AAPL, TSLA, RELIANCE.NS)…",
                        type="text",
                        debounce=False,
                    ),
                    dbc.Button(
                        "Analyse",
                        id="search-btn",
                        color="primary",
                        className="px-4",
                    ),
                ], className="mb-3"),
            ], md=6),
            dbc.Col([
                dcc.Dropdown(
                    id="home-registry-dropdown",
                    options=ticker_options,
                    placeholder="Or select an existing stock…",
                    clearable=True,
                    className="dropdown-dark",
                ),
            ], md=6),
        ], className="mb-4"),

        # ── Stock cards ───────────────────────────────────────────────────
        html.H5("Saved Stocks", className="text-muted mb-3"),

        # Market filter buttons
        dbc.Row(
            dbc.Col(
                dbc.ButtonGroup([
                    dbc.Button("🇮🇳 India", id="filter-india-btn", color="primary",          size="sm"),
                    dbc.Button("🇺🇸 US",    id="filter-us-btn",    color="outline-secondary", size="sm"),
                ], className="mb-3"),
            )
        ),

        dbc.Row(id="stock-cards-container"),

        # Pagination row
        dbc.Row([
            dbc.Col(html.Small(id="home-count-text", className="text-muted"), width="auto", className="my-auto"),
            dbc.Col(
                dbc.Pagination(id="home-pagination", max_value=1, active_page=1,
                               fully_expanded=False, size="sm", className="justify-content-end mb-0"),
                className="d-flex justify-content-end my-auto",
            ),
            dbc.Col(
                dbc.Select(
                    id="home-page-size",
                    options=[
                        {"label": "10 / page",  "value": "10"},
                        {"label": "25 / page",  "value": "25"},
                        {"label": "50 / page",  "value": "50"},
                        {"label": "100 / page", "value": "100"},
                    ],
                    value="10",
                    size="sm",
                    style={"width": "120px"},
                ),
                width="auto",
                className="my-auto",
            ),
        ], className="mt-3 align-items-center"),

        # Stores
        dcc.Store(id="stock-raw-data-store"),
        dcc.Store(id="market-filter-store", data="india"),
    ])
