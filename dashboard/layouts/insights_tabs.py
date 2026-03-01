"""Individual tab panel builders for the Insights page layout.

Provides one factory function per tab panel used in the unified Insights
page.  Each function returns the ``children`` list passed to the
corresponding :class:`~dash_bootstrap_components.Tab`.

Tab builders exported here:
- :func:`_screener_tab` — Stock Screener (RSI + market filters)
- :func:`_targets_tab` — AI-generated Price Targets
- :func:`_dividends_tab` — Dividend Payment History
- :func:`_risk_tab` — Risk Metrics (Sharpe, drawdown, volatility)
- :func:`_sectors_tab` — Sector Analysis charts
- :func:`_correlation_tab` — Pairwise Returns Correlation heatmap
"""

from typing import List

import dash_bootstrap_components as dbc
from dash import dcc, html


def _screener_tab(ticker_options: List[dict]) -> List:
    """Build the Screener tab panel children.

    Args:
        ticker_options: List of ``{"label": ..., "value": ...}`` dicts for
            the ticker dropdown (unused in this tab but kept for API
            consistency with other tab builders).

    Returns:
        List of Dash components for the Screener tab body.
    """
    return [
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
            dbc.Row([
                dbc.Col(html.Small(id="screener-count-text", className="text-muted"), width="auto", className="my-auto"),
                dbc.Col(
                    dbc.Pagination(id="screener-pagination", max_value=1, active_page=1,
                                   fully_expanded=False, size="sm", className="justify-content-end mb-0"),
                    className="d-flex justify-content-end my-auto",
                ),
                dbc.Col(
                    dbc.Select(
                        id="screener-page-size",
                        options=[
                            {"label": "10 / page", "value": "10"},
                            {"label": "25 / page", "value": "25"},
                            {"label": "50 / page", "value": "50"},
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
    ]


def _targets_tab(ticker_options: List[dict]) -> List:
    """Build the Price Targets tab panel children.

    Args:
        ticker_options: List of ``{"label": ..., "value": ...}`` dicts
            including an "All tickers" sentinel, used to populate the
            ticker filter dropdown.

    Returns:
        List of Dash components for the Price Targets tab body.
    """
    return [
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
                ], xs=12, md=6, className="mb-3"),
                dbc.Col([
                    html.Label("Market", className="text-muted small fw-semibold"),
                    dbc.RadioItems(
                        id="targets-market-filter",
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
                type="circle",
                color="#4f46e5",
                children=html.Div(id="targets-table-container"),
            ),
            dbc.Row([
                dbc.Col(html.Small(id="targets-count-text", className="text-muted"), width="auto", className="my-auto"),
                dbc.Col(
                    dbc.Pagination(id="targets-pagination", max_value=1, active_page=1,
                                   fully_expanded=False, size="sm", className="justify-content-end mb-0"),
                    className="d-flex justify-content-end my-auto",
                ),
                dbc.Col(
                    dbc.Select(
                        id="targets-page-size",
                        options=[
                            {"label": "10 / page", "value": "10"},
                            {"label": "25 / page", "value": "25"},
                            {"label": "50 / page", "value": "50"},
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
    ]


def _dividends_tab(ticker_options: List[dict]) -> List:
    """Build the Dividends tab panel children.

    Args:
        ticker_options: List of ``{"label": ..., "value": ...}`` dicts
            including an "All tickers" sentinel, used to populate the
            ticker filter dropdown.

    Returns:
        List of Dash components for the Dividends tab body.
    """
    return [
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
                ], xs=12, md=6, className="mb-3"),
                dbc.Col([
                    html.Label("Market", className="text-muted small fw-semibold"),
                    dbc.RadioItems(
                        id="dividends-market-filter",
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
                type="circle",
                color="#4f46e5",
                children=html.Div(id="dividends-table-container"),
            ),
            dbc.Row([
                dbc.Col(html.Small(id="dividends-count-text", className="text-muted"), width="auto", className="my-auto"),
                dbc.Col(
                    dbc.Pagination(id="dividends-pagination", max_value=1, active_page=1,
                                   fully_expanded=False, size="sm", className="justify-content-end mb-0"),
                    className="d-flex justify-content-end my-auto",
                ),
                dbc.Col(
                    dbc.Select(
                        id="dividends-page-size",
                        options=[
                            {"label": "10 / page", "value": "10"},
                            {"label": "25 / page", "value": "25"},
                            {"label": "50 / page", "value": "50"},
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
    ]


def _risk_tab(ticker_options: List[dict]) -> List:
    """Build the Risk Metrics tab panel children.

    Args:
        ticker_options: List of ``{"label": ..., "value": ...}`` dicts
            (unused in this tab but kept for API consistency with other
            tab builders).

    Returns:
        List of Dash components for the Risk Metrics tab body.
    """
    return [
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
                ], xs=12, md=8, className="mb-3"),
                dbc.Col([
                    html.Label("Market", className="text-muted small fw-semibold"),
                    dbc.RadioItems(
                        id="risk-market-filter",
                        options=[
                            {"label": "All",       "value": "all"},
                            {"label": "🇮🇳 India", "value": "india"},
                            {"label": "🇺🇸 US",    "value": "us"},
                        ],
                        value="all",
                        inline=True,
                        className="mt-1",
                    ),
                ], xs=12, md=4, className="mb-3"),
            ], className="bg-light rounded p-3 mb-4 border"),
            dcc.Loading(
                type="circle",
                color="#4f46e5",
                children=html.Div(id="risk-table-container"),
            ),
            dbc.Row([
                dbc.Col(html.Small(id="risk-count-text", className="text-muted"), width="auto", className="my-auto"),
                dbc.Col(
                    dbc.Pagination(id="risk-pagination", max_value=1, active_page=1,
                                   fully_expanded=False, size="sm", className="justify-content-end mb-0"),
                    className="d-flex justify-content-end my-auto",
                ),
                dbc.Col(
                    dbc.Select(
                        id="risk-page-size",
                        options=[
                            {"label": "10 / page", "value": "10"},
                            {"label": "25 / page", "value": "25"},
                            {"label": "50 / page", "value": "50"},
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
    ]


def _sectors_tab(ticker_options: List[dict]) -> List:
    """Build the Sector Analysis tab panel children.

    Args:
        ticker_options: List of ``{"label": ..., "value": ...}`` dicts
            (unused in this tab but kept for API consistency with other
            tab builders).

    Returns:
        List of Dash components for the Sectors tab body.
    """
    return [
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
    ]


def _correlation_tab(ticker_options: List[dict]) -> List:
    """Build the Returns Correlation tab panel children.

    Args:
        ticker_options: List of ``{"label": ..., "value": ...}`` dicts
            (unused in this tab but kept for API consistency with other
            tab builders).

    Returns:
        List of Dash components for the Correlation tab body.
    """
    return [
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
    ]
