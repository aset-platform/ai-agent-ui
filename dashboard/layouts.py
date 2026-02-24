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
        dbc.Row(id="stock-cards-container"),
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
