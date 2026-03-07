"""Forecast page layout for the AI Stock Analysis Dashboard.

Provides :func:`forecast_layout`, which builds the forecast page containing
a ticker dropdown, forecast-horizon radio buttons, the forecast chart,
price-target cards, model accuracy row, and a *Refresh Data & Run Analysis*
button.
"""

import dash_bootstrap_components as dbc
from dash import dcc, html

from dashboard.components.refresh_button import refresh_button_group
from dashboard.layouts.helpers import _get_available_tickers


def forecast_layout() -> html.Div:
    """Build the Forecast page layout.

    Contains a ticker dropdown, forecast-horizon radio buttons, the forecast
    chart, price-target cards, model accuracy row, and a
    *Refresh Data & Run Analysis* button that triggers the full stock data
    refresh pipeline.

    Returns:
        :class:`~dash.html.Div` representing the full forecast page.
    """
    tickers = _get_available_tickers()
    ticker_options = [{"label": t, "value": t} for t in tickers]
    default_ticker = tickers[0] if tickers else None

    return html.Div(
        [
            # ── Controls ─────────────────────────────────────────────────
            dbc.Row(
                [
                    dbc.Col(
                        [
                            html.Label(
                                "Ticker",
                                className="text-muted small fw-semibold",
                            ),
                            dcc.Dropdown(
                                id="forecast-ticker-dropdown",
                                options=ticker_options,
                                value=default_ticker,
                                clearable=False,
                                className="dropdown-dark",
                            ),
                        ],
                        xs=12,
                        md=4,
                        className="mb-3",
                    ),
                    dbc.Col(
                        [
                            html.Label(
                                "Forecast Horizon",
                                className="text-muted small fw-semibold",
                            ),
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
                        ],
                        xs=12,
                        md=4,
                        className="mb-3",
                    ),
                    dbc.Col(
                        [
                            html.Label("\u00a0", className="d-block small"),
                            refresh_button_group("forecast-refresh"),
                        ],
                        xs=12,
                        md=4,
                        className="mb-3",
                    ),
                ],
                className=(
                    "bg-light rounded p-3" " mb-4 align-items-end border"
                ),
            ),
            # ── Status (inline with button via dcc.Loading) ────────────────
            # ── Forecast chart ─────────────────────────────────────────────
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
            # ── Price target cards ─────────────────────────────────────────
            html.Div(id="forecast-target-cards", className="mt-4"),
            # ── Accuracy row ─────────────────────────────────────────────
            html.Div(id="forecast-accuracy-row", className="mt-3"),
            # ── Hidden stores ──────────────────────────────────────────────
            dcc.Store(id="forecast-refresh-store", data=0),
            dcc.Store(id="accuracy-store", data=None),
        ]
    )
