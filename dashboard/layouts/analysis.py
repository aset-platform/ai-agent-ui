"""Price Analysis page layout for the AI Stock Analysis Dashboard.

Provides :func:`analysis_layout` and :func:`analysis_tabs_layout`, which
build the analysis page containing ticker dropdown, date-range slider,
overlay-toggle switches, the 3-panel Plotly chart, and summary-stats row.
"""

import time as _time

import dash_bootstrap_components as dbc
from dash import dcc, html

from dashboard.layouts.helpers import _get_available_tickers

# Fix #17: module-level cache for ticker list (5-min TTL)
_TICKER_OPTIONS_CACHE: dict = {"options": None, "expiry": 0.0}
_TICKER_OPTIONS_TTL = 300  # seconds


def _get_available_tickers_cached() -> list:
    """Return sorted ticker list from registry, cached for ``_TICKER_OPTIONS_TTL`` seconds.

    Avoids re-reading the registry JSON on every analysis page render.

    Returns:
        Sorted list of ticker symbol strings.
    """
    now = _time.monotonic()
    if (
        _TICKER_OPTIONS_CACHE["options"] is not None
        and now < _TICKER_OPTIONS_CACHE["expiry"]
    ):
        return _TICKER_OPTIONS_CACHE["options"]
    options = _get_available_tickers()
    _TICKER_OPTIONS_CACHE.update(
        {"options": options, "expiry": now + _TICKER_OPTIONS_TTL}
    )
    return options


def analysis_layout() -> html.Div:
    """Build the Price Analysis page layout.

    Provides a ticker dropdown, date-range slider, overlay-toggle switches,
    a placeholder for the 3-panel Plotly chart (price / RSI / MACD), and a
    summary-stats row.

    Returns:
        :class:`~dash.html.Div` representing the full analysis page.
    """
    tickers = _get_available_tickers_cached()
    ticker_options = [{"label": t, "value": t} for t in tickers]
    default_ticker = tickers[0] if tickers else None

    return html.Div(
        [
            # ── Controls ──────────────────────────────────────────────────────
            dbc.Row(
                [
                    dbc.Col(
                        [
                            html.Label(
                                "Ticker", className="text-muted small fw-semibold"
                            ),
                            dcc.Dropdown(
                                id="analysis-ticker-dropdown",
                                options=ticker_options,
                                value=default_ticker,
                                clearable=False,
                                className="dropdown-dark",
                            ),
                        ],
                        xs=12,
                        md=3,
                        className="mb-3",
                    ),
                    dbc.Col(
                        [
                            html.Label(
                                "Date Range", className="text-muted small fw-semibold"
                            ),
                            dcc.Slider(
                                id="date-range-slider",
                                min=0,
                                max=5,
                                step=1,
                                marks={
                                    0: "1M",
                                    1: "3M",
                                    2: "6M",
                                    3: "1Y",
                                    4: "3Y",
                                    5: "Max",
                                },
                                value=5,
                                className="mt-1",
                                tooltip={"always_visible": False},
                            ),
                        ],
                        xs=12,
                        md=4,
                        className="mb-3",
                    ),
                    dbc.Col(
                        [
                            html.Label(
                                "Overlays", className="text-muted small fw-semibold"
                            ),
                            dbc.Checklist(
                                id="overlay-toggles",
                                options=[
                                    {"label": "SMA 50", "value": "sma50"},
                                    {"label": "SMA 200", "value": "sma200"},
                                    {"label": "Bollinger Bands", "value": "bb"},
                                    {"label": "Volume", "value": "volume"},
                                ],
                                value=["sma50", "sma200"],
                                inline=True,
                                switch=True,
                                className="mt-1",
                            ),
                        ],
                        xs=12,
                        md=5,
                        className="mb-3",
                    ),
                ],
                className="bg-light rounded p-3 mb-4 align-items-center border",
            ),
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
        ]
    )


def analysis_tabs_layout() -> html.Div:
    """Build the unified Analysis / Forecast / Compare tabbed page layout.

    Provides three tabs — Price Analysis, Forecast, and Compare Stocks — all
    accessible from the single ``/analysis`` route.

    Returns:
        :class:`~dash.html.Div` representing the full tabbed analysis page.
    """
    from dashboard.layouts.compare import compare_layout  # noqa: PLC0415
    from dashboard.layouts.forecast import forecast_layout  # noqa: PLC0415

    return html.Div(
        [
            dbc.Tabs(
                id="analysis-page-tabs",
                active_tab="analysis-tab",
                children=[
                    dbc.Tab(
                        label="Price Analysis",
                        tab_id="analysis-tab",
                        children=[analysis_layout()],
                    ),
                    dbc.Tab(
                        label="Forecast",
                        tab_id="forecast-tab",
                        children=[forecast_layout()],
                    ),
                    dbc.Tab(
                        label="Compare Stocks",
                        tab_id="compare-tab",
                        children=[compare_layout()],
                    ),
                ],
            ),
        ]
    )
