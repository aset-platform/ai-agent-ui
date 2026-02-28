"""Page layout definitions for the AI Stock Analysis Dashboard.

Provides four page-layout factory functions (home, analysis, forecast,
compare) plus the global navigation bar.  All layouts define only static
component skeletons with stable IDs; interactive content is populated by
callbacks in :mod:`dashboard.callbacks`.

Data is read directly from the local ``data/`` parquet files and the
``data/metadata/stock_registry.json`` registry — no HTTP calls to the
backend API.

Example::

    from dashboard.layouts import home_layout, NAVBAR
    app.layout = html.Div([NAVBAR, home_layout()])
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

import dash_bootstrap_components as dbc
from dash import dcc, html

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"
_REGISTRY_PATH = _DATA_METADATA / "stock_registry.json"

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_registry() -> dict:
    """Load the stock registry from disk.

    Returns:
        Dictionary mapping ticker symbols to registry metadata records.
        Returns an empty dict if the file is missing or unparsable.
    """
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        with open(_REGISTRY_PATH) as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("Could not load registry: %s", exc)
        return {}


def _get_available_tickers() -> List[str]:
    """Return sorted list of ticker symbols from the stock registry.

    Returns:
        Alphabetically sorted list of ticker strings.
    """
    return sorted(_load_registry().keys())


# ---------------------------------------------------------------------------
# Global navigation bar
# ---------------------------------------------------------------------------

NAVBAR = dbc.NavbarSimple(
    children=[
        dbc.NavItem(dbc.NavLink("Home", href="/", className="nav-link-custom")),
        dbc.NavItem(dbc.NavLink("Analysis", href="/analysis", className="nav-link-custom")),
        dbc.NavItem(dbc.NavLink("Forecast", href="/forecast", className="nav-link-custom")),
        dbc.NavItem(dbc.NavLink("Compare", href="/compare", className="nav-link-custom")),
        dbc.NavItem(dbc.NavLink("Insights", href="/insights", className="nav-link-custom")),
        dbc.NavItem(dbc.NavLink("Admin", href="/admin/users", className="nav-link-custom")),
        dbc.NavItem(
            dbc.Button(
                "🔑 Change Password",
                id="open-change-password-btn",
                size="sm",
                color="outline-secondary",
                className="ms-2",
            )
        ),
    ],
    brand="📊 AI Stock Analysis",
    brand_href="/",
    color="light",
    dark=False,
    className="mb-0",
    fluid=True,
)

# ---------------------------------------------------------------------------
# Page 1: Home / Stock Overview
# ---------------------------------------------------------------------------


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
        # ── Header ───────────────────────────────────────────────────────
        dbc.Row(dbc.Col([
            html.H2("AI Stock Analysis Dashboard", className="mb-1 fw-bold"),
            html.P(
                "Fetch, analyse, forecast, and compare stocks with AI-powered insights.",
                className="text-muted mb-4",
            ),
        ])),

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


# ---------------------------------------------------------------------------
# Page 2: Price Analysis
# ---------------------------------------------------------------------------


def analysis_layout() -> html.Div:
    """Build the Price Analysis page layout.

    Provides a ticker dropdown, date-range slider, overlay-toggle switches,
    a placeholder for the 3-panel Plotly chart (price / RSI / MACD), and a
    summary-stats row.

    Returns:
        :class:`~dash.html.Div` representing the full analysis page.
    """
    tickers = _get_available_tickers()
    ticker_options = [{"label": t, "value": t} for t in tickers]
    default_ticker = tickers[0] if tickers else None

    return html.Div([
        # ── Controls ──────────────────────────────────────────────────────
        dbc.Row([
            dbc.Col([
                html.Label("Ticker", className="text-muted small fw-semibold"),
                dcc.Dropdown(
                    id="analysis-ticker-dropdown",
                    options=ticker_options,
                    value=default_ticker,
                    clearable=False,
                    className="dropdown-dark",
                ),
            ], xs=12, md=3, className="mb-3"),

            dbc.Col([
                html.Label("Date Range", className="text-muted small fw-semibold"),
                dcc.Slider(
                    id="date-range-slider",
                    min=0, max=5, step=1,
                    marks={0: "1M", 1: "3M", 2: "6M", 3: "1Y", 4: "3Y", 5: "Max"},
                    value=5,
                    className="mt-1",
                    tooltip={"always_visible": False},
                ),
            ], xs=12, md=4, className="mb-3"),

            dbc.Col([
                html.Label("Overlays", className="text-muted small fw-semibold"),
                dbc.Checklist(
                    id="overlay-toggles",
                    options=[
                        {"label": "SMA 50",          "value": "sma50"},
                        {"label": "SMA 200",         "value": "sma200"},
                        {"label": "Bollinger Bands", "value": "bb"},
                        {"label": "Volume",          "value": "volume"},
                    ],
                    value=["sma50", "sma200"],
                    inline=True,
                    switch=True,
                    className="mt-1",
                ),
            ], xs=12, md=5, className="mb-3"),
        ], className="bg-light rounded p-3 mb-4 align-items-center border"),

        # ── Chart ─────────────────────────────────────────────────────────
        dcc.Loading(
            id="loading-analysis",
            type="circle",
            color="#4f46e5",
            children=dcc.Graph(
                id="analysis-chart",
                config={"displayModeBar": True, "scrollZoom": True},
                style={"height": "800px"},
            ),
        ),

        # ── Summary stats ─────────────────────────────────────────────────
        html.Div(id="analysis-stats-row", className="mt-4"),
    ])


# ---------------------------------------------------------------------------
# Page 3: Forecast
# ---------------------------------------------------------------------------


def forecast_layout() -> html.Div:
    """Build the Forecast page layout.

    Contains a ticker dropdown, forecast-horizon radio buttons, the forecast
    chart, price-target cards, model accuracy row, and a *Run New Analysis*
    button that triggers the backend Prophet pipeline.

    Returns:
        :class:`~dash.html.Div` representing the full forecast page.
    """
    tickers = _get_available_tickers()
    ticker_options = [{"label": t, "value": t} for t in tickers]
    default_ticker = tickers[0] if tickers else None

    return html.Div([
        # ── Controls ──────────────────────────────────────────────────────
        dbc.Row([
            dbc.Col([
                html.Label("Ticker", className="text-muted small fw-semibold"),
                dcc.Dropdown(
                    id="forecast-ticker-dropdown",
                    options=ticker_options,
                    value=default_ticker,
                    clearable=False,
                    className="dropdown-dark",
                ),
            ], xs=12, md=4, className="mb-3"),

            dbc.Col([
                html.Label("Forecast Horizon", className="text-muted small fw-semibold"),
                dbc.RadioItems(
                    id="forecast-horizon-radio",
                    options=[
                        {"label": "3 Months", "value": "3"},
                        {"label": "6 Months", "value": "6"},
                        {"label": "9 Months", "value": "9"},
                    ],
                    value="9",
                    inline=True,
                    className="mt-1",
                ),
            ], xs=12, md=4, className="mb-3"),

            dbc.Col([
                html.Label("\u00a0", className="d-block small"),
                dbc.Button(
                    "Run New Analysis",
                    id="run-analysis-btn",
                    color="success",
                    size="sm",
                    className="w-100",
                ),
            ], xs=12, md=4, className="mb-3"),
        ], className="bg-light rounded p-3 mb-4 align-items-end border"),

        # ── Status message ────────────────────────────────────────────────
        html.Div(id="run-analysis-status", className="mb-3"),

        # ── Forecast chart ────────────────────────────────────────────────
        dcc.Loading(
            id="loading-forecast",
            type="circle",
            color="#4f46e5",
            children=dcc.Graph(
                id="forecast-chart",
                config={"displayModeBar": True},
                style={"height": "550px"},
            ),
        ),

        # ── Price target cards ────────────────────────────────────────────
        html.Div(id="forecast-target-cards", className="mt-4"),

        # ── Accuracy row ──────────────────────────────────────────────────
        html.Div(id="forecast-accuracy-row", className="mt-3"),

        # ── Hidden stores ─────────────────────────────────────────────────
        dcc.Store(id="forecast-refresh-store", data=0),
        dcc.Store(id="accuracy-store", data=None),
    ])


# ---------------------------------------------------------------------------
# Page 4: Compare Stocks
# ---------------------------------------------------------------------------


def compare_layout() -> html.Div:
    """Build the Compare Stocks page layout.

    Provides a multi-select dropdown (2–5 stocks), a normalised performance
    chart, a metrics comparison table, and a returns correlation heatmap.

    Returns:
        :class:`~dash.html.Div` representing the full compare page.
    """
    tickers = _get_available_tickers()
    ticker_options = [{"label": t, "value": t} for t in tickers]
    default_selection = tickers[:2] if len(tickers) >= 2 else tickers

    return html.Div([
        # ── Controls ──────────────────────────────────────────────────────
        dbc.Row([
            dbc.Col([
                html.Label(
                    "Select Stocks to Compare (2–5)",
                    className="text-muted small fw-semibold",
                ),
                dcc.Dropdown(
                    id="compare-ticker-dropdown",
                    options=ticker_options,
                    value=default_selection,
                    multi=True,
                    clearable=False,
                    className="dropdown-dark",
                ),
            ]),
        ], className="bg-light rounded p-3 mb-4 border"),

        # ── Normalised performance chart ───────────────────────────────────
        html.H6("Normalised Performance (Base = 100)", className="text-muted mb-2"),
        dcc.Loading(
            children=dcc.Graph(
                id="compare-perf-chart",
                config={"displayModeBar": True},
                style={"height": "450px"},
            ),
        ),

        # ── Metrics table + heatmap ────────────────────────────────────────
        dbc.Row([
            dbc.Col([
                html.H6("Metrics Comparison", className="text-muted mb-2 mt-4"),
                html.Div(id="compare-metrics-container"),
            ], xs=12, lg=8),
            dbc.Col([
                html.H6("Returns Correlation", className="text-muted mb-2 mt-4"),
                dcc.Loading(
                    children=dcc.Graph(
                        id="compare-heatmap",
                        style={"height": "380px"},
                        config={"displayModeBar": False},
                    ),
                ),
            ], xs=12, lg=4),
        ]),
    ])


# ---------------------------------------------------------------------------
# Page 5: Admin — User Management
# ---------------------------------------------------------------------------


def admin_users_layout() -> html.Div:
    """Build the Admin / User Management page layout.

    Displays two tabs: a user management table with Add / Edit / Deactivate
    controls, and an audit log viewer.  Only rendered for superusers — the
    ``display_page`` callback in ``app.py`` enforces the role guard before
    calling this function.

    Returns:
        :class:`~dash.html.Div` representing the full admin page.
    """
    return html.Div([
        # ── Header ───────────────────────────────────────────────────────
        dbc.Row(dbc.Col([
            html.H2("User Management", className="mb-1 fw-bold"),
            html.P(
                "Create, edit, and deactivate user accounts.  View the full audit log.",
                className="text-muted mb-4",
            ),
        ])),

        # ── Tabs: Users | Audit Log ───────────────────────────────────────
        dbc.Tabs(
            id="admin-tabs",
            active_tab="users-tab",
            children=[
                # ── Tab 1: Users ──────────────────────────────────────────
                dbc.Tab(
                    label="Users",
                    tab_id="users-tab",
                    children=[
                        html.Div(className="mt-3", children=[
                            dbc.Row([
                                dbc.Col(
                                    html.H5("All Accounts", className="text-muted my-auto"),
                                ),
                                dbc.Col(
                                    dbc.Button(
                                        "+ Add User",
                                        id="add-user-btn",
                                        color="primary",
                                        size="sm",
                                        className="float-end",
                                    ),
                                    className="text-end",
                                ),
                            ], className="mb-3 align-items-center"),

                            # Status message from save/delete operations
                            html.Div(id="users-action-status", className="mb-3"),

                            # Search filter
                            dbc.Input(
                                id="users-search",
                                placeholder="Search by name, email or role…",
                                debounce=True,
                                size="sm",
                                className="mb-3",
                            ),

                            dcc.Loading(
                                id="loading-users",
                                type="circle",
                                color="#4f46e5",
                                children=html.Div(id="users-table-container"),
                            ),

                            # Users pagination row
                            dbc.Row([
                                dbc.Col(html.Small(id="users-count-text", className="text-muted"), width="auto", className="my-auto"),
                                dbc.Col(
                                    dbc.Pagination(id="users-pagination", max_value=1, active_page=1,
                                                   fully_expanded=False, size="sm", className="justify-content-end mb-0"),
                                    className="d-flex justify-content-end my-auto",
                                ),
                                dbc.Col(
                                    dbc.Select(
                                        id="users-page-size",
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
                            ], className="mt-2 align-items-center"),
                        ]),
                    ],
                ),

                # ── Tab 2: Audit Log ──────────────────────────────────────
                dbc.Tab(
                    label="Audit Log",
                    tab_id="audit-tab",
                    children=[
                        html.Div(className="mt-3", children=[
                            html.H5("Audit Log", className="text-muted mb-3"),

                            # Search filter
                            dbc.Input(
                                id="audit-search",
                                placeholder="Search by event type, actor ID or details…",
                                debounce=True,
                                size="sm",
                                className="mb-3",
                            ),

                            dcc.Loading(
                                id="loading-audit",
                                type="circle",
                                color="#4f46e5",
                                children=html.Div(id="audit-log-container"),
                            ),

                            # Audit pagination row
                            dbc.Row([
                                dbc.Col(html.Small(id="audit-count-text", className="text-muted"), width="auto", className="my-auto"),
                                dbc.Col(
                                    dbc.Pagination(id="audit-pagination", max_value=1, active_page=1,
                                                   fully_expanded=False, size="sm", className="justify-content-end mb-0"),
                                    className="d-flex justify-content-end my-auto",
                                ),
                                dbc.Col(
                                    dbc.Select(
                                        id="audit-page-size",
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
                            ], className="mt-2 align-items-center"),
                        ]),
                    ],
                ),
            ],
        ),

        # ── User add / edit modal ─────────────────────────────────────────
        dbc.Modal(
            id="user-modal",
            is_open=False,
            backdrop="static",
            children=[
                dbc.ModalHeader(
                    dbc.ModalTitle(id="user-modal-title"),
                    close_button=False,
                ),
                dbc.ModalBody([
                    html.Div(
                        id="modal-error",
                        className="text-danger small mb-2",
                    ),
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Full Name"),
                            dbc.Input(
                                id="modal-full-name",
                                type="text",
                                placeholder="Jane Doe",
                            ),
                        ]),
                    ], className="mb-3"),
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Email"),
                            dbc.Input(
                                id="modal-email",
                                type="email",
                                placeholder="jane@example.com",
                            ),
                        ]),
                    ], className="mb-3"),
                    # Password row — visible only when adding a new user
                    html.Div(
                        id="modal-password-row",
                        children=[
                            dbc.Row([
                                dbc.Col([
                                    dbc.Label("Password"),
                                    dbc.Input(
                                        id="modal-password",
                                        type="password",
                                        placeholder="Min 8 chars, at least one digit",
                                    ),
                                ]),
                            ], className="mb-3"),
                        ],
                    ),
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Role"),
                            dbc.Select(
                                id="modal-role",
                                options=[
                                    {"label": "General User", "value": "general"},
                                    {"label": "Superuser",    "value": "superuser"},
                                ],
                                value="general",
                            ),
                        ]),
                    ], className="mb-3"),
                    # Active toggle — visible only when editing an existing user
                    html.Div(
                        id="modal-active-row",
                        children=[
                            dbc.Row([
                                dbc.Col([
                                    dbc.Checklist(
                                        id="modal-is-active",
                                        options=[{"label": "Active account", "value": "active"}],
                                        value=["active"],
                                        switch=True,
                                    ),
                                ]),
                            ], className="mb-2"),
                        ],
                        style={"display": "none"},
                    ),
                ]),
                dbc.ModalFooter([
                    dbc.Button(
                        "Cancel",
                        id="modal-cancel-btn",
                        color="secondary",
                        outline=True,
                        size="sm",
                        className="me-2",
                    ),
                    dbc.Button(
                        "Save",
                        id="modal-save-btn",
                        color="primary",
                        size="sm",
                    ),
                ]),
            ],
        ),

        # ── Hidden stores ─────────────────────────────────────────────────
        dcc.Store(id="users-store", data=[]),
        dcc.Store(id="user-modal-store", data=None),
        dcc.Store(id="users-refresh-store", data=0),
        dcc.Store(id="audit-data-store", data=None),
    ])


# ---------------------------------------------------------------------------
# Page 6: Stock Screener (Iceberg-backed)
# ---------------------------------------------------------------------------


def insights_layout() -> html.Div:
    """Build the unified Insights page with six analysis tabs.

    Combines Screener, Price Targets, Dividends, Risk Metrics, Sector
    Analysis, and Returns Correlation into a single tabbed page, matching
    the visual style of the Admin page.  All data is sourced from the
    Iceberg ``stocks.*`` tables.

    Returns:
        :class:`~dash.html.Div` representing the full Insights page.
    """
    tickers = _get_available_tickers()
    ticker_options = (
        [{"label": "All tickers", "value": "all"}]
        + [{"label": t, "value": t} for t in tickers]
    )

    return html.Div([
        # ── Header ───────────────────────────────────────────────────────
        dbc.Row(dbc.Col([
            html.H2("Insights", className="mb-1 fw-bold"),
            html.P(
                "Deep-dive analytics powered by the Iceberg data warehouse.",
                className="text-muted mb-4",
            ),
        ])),

        # ── Tabs ─────────────────────────────────────────────────────────
        dbc.Tabs(
            id="insights-tabs",
            active_tab="screener-tab",
            children=[

                # ── Tab 1: Screener ───────────────────────────────────────
                dbc.Tab(label="Screener", tab_id="screener-tab", children=[
                    html.Div(className="mt-3", children=[
                        html.P(
                            "Screen all tracked stocks by technical signals and performance metrics.",
                            className="text-muted mb-3",
                        ),
                        dbc.Row([
                            dbc.Col([
                                html.Label("RSI Signal", className="text-muted small fw-semibold"),
                                dbc.RadioItems(
                                    id="screener-rsi-filter",
                                    options=[
                                        {"label": "All",               "value": "all"},
                                        {"label": "Oversold (< 30)",   "value": "oversold"},
                                        {"label": "Neutral (30–70)",   "value": "neutral"},
                                        {"label": "Overbought (> 70)", "value": "overbought"},
                                    ],
                                    value="all",
                                    inline=True,
                                    className="mt-1",
                                ),
                            ], xs=12, md=6, className="mb-3"),
                            dbc.Col([
                                html.Label("Market", className="text-muted small fw-semibold"),
                                dbc.RadioItems(
                                    id="screener-market-filter",
                                    options=[
                                        {"label": "All",       "value": "all"},
                                        {"label": "🇮🇳 India", "value": "india"},
                                        {"label": "🇺🇸 US",    "value": "us"},
                                    ],
                                    value="all",
                                    inline=True,
                                    className="mt-1",
                                ),
                            ], xs=12, md=6, className="mb-3"),
                        ], className="bg-light rounded p-3 mb-4 border"),
                        dcc.Loading(
                            id="loading-screener",
                            type="circle",
                            color="#4f46e5",
                            children=html.Div(id="screener-table-container"),
                        ),
                    ]),
                ]),

                # ── Tab 2: Price Targets ──────────────────────────────────
                dbc.Tab(label="Price Targets", tab_id="targets-tab", children=[
                    html.Div(className="mt-3", children=[
                        html.P(
                            "Latest AI-generated price targets from Prophet forecasts.",
                            className="text-muted mb-3",
                        ),
                        dbc.Row([
                            dbc.Col([
                                html.Label("Ticker", className="text-muted small fw-semibold"),
                                dcc.Dropdown(
                                    id="targets-ticker-dropdown",
                                    options=ticker_options,
                                    value="all",
                                    clearable=False,
                                ),
                            ], xs=12, md=4, className="mb-3"),
                        ], className="bg-light rounded p-3 mb-4 border"),
                        dcc.Loading(
                            type="circle",
                            color="#4f46e5",
                            children=html.Div(id="targets-table-container"),
                        ),
                    ]),
                ]),

                # ── Tab 3: Dividends ──────────────────────────────────────
                dbc.Tab(label="Dividends", tab_id="dividends-tab", children=[
                    html.Div(className="mt-3", children=[
                        html.P(
                            "Full dividend payment history for all tracked stocks.",
                            className="text-muted mb-3",
                        ),
                        dbc.Row([
                            dbc.Col([
                                html.Label("Ticker", className="text-muted small fw-semibold"),
                                dcc.Dropdown(
                                    id="dividends-ticker-dropdown",
                                    options=ticker_options,
                                    value="all",
                                    clearable=False,
                                ),
                            ], xs=12, md=4, className="mb-3"),
                        ], className="bg-light rounded p-3 mb-4 border"),
                        dcc.Loading(
                            type="circle",
                            color="#4f46e5",
                            children=html.Div(id="dividends-table-container"),
                        ),
                    ]),
                ]),

                # ── Tab 4: Risk Metrics ───────────────────────────────────
                dbc.Tab(label="Risk Metrics", tab_id="risk-tab", children=[
                    html.Div(className="mt-3", children=[
                        html.P(
                            "Volatility, drawdown, and risk-adjusted return metrics for all tracked stocks.",
                            className="text-muted mb-3",
                        ),
                        dbc.Row([
                            dbc.Col([
                                html.Label("Sort By", className="text-muted small fw-semibold"),
                                dbc.RadioItems(
                                    id="risk-sort-by",
                                    options=[
                                        {"label": "Sharpe Ratio",       "value": "sharpe_ratio"},
                                        {"label": "Max Drawdown",        "value": "max_drawdown_pct"},
                                        {"label": "Volatility",          "value": "annualized_volatility_pct"},
                                        {"label": "Annualised Return",   "value": "annualized_return_pct"},
                                    ],
                                    value="sharpe_ratio",
                                    inline=True,
                                    className="mt-1",
                                ),
                            ], xs=12, className="mb-3"),
                        ], className="bg-light rounded p-3 mb-4 border"),
                        dcc.Loading(
                            type="circle",
                            color="#4f46e5",
                            children=html.Div(id="risk-table-container"),
                        ),
                    ]),
                ]),

                # ── Tab 5: Sectors ────────────────────────────────────────
                dbc.Tab(label="Sectors", tab_id="sectors-tab", children=[
                    html.Div(className="mt-3", children=[
                        html.P(
                            "Average technical signals and returns grouped by sector.",
                            className="text-muted mb-3",
                        ),
                        dbc.Row([
                            dbc.Col([
                                dcc.Loading(
                                    type="circle",
                                    color="#4f46e5",
                                    children=dcc.Graph(
                                        id="sectors-bar-chart",
                                        config={"displayModeBar": False},
                                        style={"height": "420px"},
                                    ),
                                ),
                            ], xs=12, lg=8),
                            dbc.Col([
                                dcc.Loading(
                                    type="circle",
                                    color="#4f46e5",
                                    children=html.Div(id="sectors-table-container"),
                                ),
                            ], xs=12, lg=4),
                        ]),
                    ]),
                ]),

                # ── Tab 6: Correlation ────────────────────────────────────
                dbc.Tab(label="Correlation", tab_id="correlation-tab", children=[
                    html.Div(className="mt-3", children=[
                        html.P(
                            "Pairwise daily-returns correlation across all tracked stocks.",
                            className="text-muted mb-3",
                        ),
                        dbc.Row([
                            dbc.Col([
                                html.Label("Lookback Period", className="text-muted small fw-semibold"),
                                dbc.RadioItems(
                                    id="corr-period-filter",
                                    options=[
                                        {"label": "1 Year",  "value": "1y"},
                                        {"label": "3 Years", "value": "3y"},
                                        {"label": "All",     "value": "all"},
                                    ],
                                    value="1y",
                                    inline=True,
                                    className="mt-1",
                                ),
                            ], xs=12, className="mb-3"),
                        ], className="bg-light rounded p-3 mb-4 border"),
                        dcc.Loading(
                            type="circle",
                            color="#4f46e5",
                            children=dcc.Graph(
                                id="correlation-heatmap",
                                config={"displayModeBar": False},
                                style={"height": "600px"},
                            ),
                        ),
                    ]),
                ]),

            ],
        ),
    ])
