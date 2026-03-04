"""Compare Stocks page layout for the Dashboard.

Provides :func:`compare_layout`, which builds the stock
comparison page containing a multi-select dropdown, an
Adj Close price chart, a metrics comparison table, and a
returns correlation heatmap.
"""

import dash_bootstrap_components as dbc
from dash import dcc, html

from dashboard.layouts.helpers import _get_available_tickers


def compare_layout() -> html.Div:
    """Build the Compare Stocks page layout.

    Provides a multi-select dropdown (2-5 stocks), an Adj
    Close price chart, a metrics comparison table, and a
    returns correlation heatmap.

    Returns:
        :class:`~dash.html.Div` for the compare page.
    """
    tickers = _get_available_tickers()
    ticker_options = [{"label": t, "value": t} for t in tickers]
    default_selection = tickers[:2] if len(tickers) >= 2 else tickers

    return html.Div(
        [
            # ── Controls ──────────────────────────────────────────────────────
            dbc.Row(
                [
                    dbc.Col(
                        [
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
                        ]
                    ),
                ],
                className="bg-light rounded p-3 mb-4 border",
            ),
            # ── Adj Close performance chart ──────────────
            html.H6(
                "Adj Close Price Comparison",
                className="text-muted mb-2",
            ),
            dcc.Loading(
                children=dcc.Graph(
                    id="compare-perf-chart",
                    config={"displayModeBar": True},
                    style={"height": "450px"},
                ),
            ),
            # ── Metrics table + heatmap ────────────────────────────────────────
            dbc.Row(
                [
                    dbc.Col(
                        [
                            html.H6(
                                "Metrics Comparison",
                                className="text-muted mb-2 mt-4",
                            ),
                            html.Div(id="compare-metrics-container"),
                        ],
                        xs=12,
                        lg=8,
                    ),
                    dbc.Col(
                        [
                            html.H6(
                                "Returns Correlation",
                                className="text-muted mb-2 mt-4",
                            ),
                            dcc.Loading(
                                children=dcc.Graph(
                                    id="compare-heatmap",
                                    style={"height": "380px"},
                                    config={"displayModeBar": False},
                                ),
                            ),
                        ],
                        xs=12,
                        lg=4,
                    ),
                ]
            ),
        ]
    )
