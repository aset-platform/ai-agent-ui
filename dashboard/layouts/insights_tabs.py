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
- :func:`_quarterly_tab` — Quarterly Financial Results
"""

from __future__ import annotations

from typing import List

import dash_bootstrap_components as dbc
from dash import dcc, html


def _screener_tab(
    ticker_options: List[dict],
    sector_options: List[dict] | None = None,
) -> List:
    """Build the Screener tab panel children.

    Args:
        ticker_options: Ticker dropdown options (unused but
            kept for API consistency).
        sector_options: Sector dropdown options.

    Returns:
        List of Dash components for the Screener tab body.
    """
    sector_options = sector_options or []
    return [
        html.Div(
            className="mt-3",
            children=[
                html.P(
                    "Screen all tracked stocks by "
                    "technical signals and "
                    "performance metrics.",
                    className="text-muted mb-3",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.Label(
                                    [
                                        "RSI Signal ",
                                        html.Span(
                                            "\u2139",
                                            id=("screener-filter" "-rsi-tip"),
                                            className=("col-info-icon"),
                                        ),
                                        dbc.Tooltip(
                                            "RSI (Relative"
                                            " Strength Index):"
                                            " momentum oscillator"
                                            " (0\u2013100)."
                                            " \u226570 = overbought,"
                                            " \u226430 = oversold.",
                                            target=(
                                                "screener-filter" "-rsi-tip"
                                            ),
                                            placement="top",
                                        ),
                                    ],
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dbc.RadioItems(
                                    id="screener-rsi-filter",
                                    options=[
                                        {
                                            "label": "All",
                                            "value": "all",
                                        },
                                        {
                                            "label": ("Oversold (< 30)"),
                                            "value": "oversold",
                                        },
                                        {
                                            "label": ("Neutral (30\u201370)"),
                                            "value": "neutral",
                                        },
                                        {
                                            "label": ("Overbought (> 70)"),
                                            "value": "overbought",
                                        },
                                    ],
                                    value="all",
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
                                html.Label(
                                    "Market",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dbc.RadioItems(
                                    id="screener-market-filter",
                                    options=[
                                        {
                                            "label": "All",
                                            "value": "all",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1ee"
                                                "\U0001f1f3"
                                                " India"
                                            ),
                                            "value": "india",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1fa" "\U0001f1f8" " US"
                                            ),
                                            "value": "us",
                                        },
                                    ],
                                    value="all",
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
                                html.Label(
                                    "Sector",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dcc.Dropdown(
                                    id=("screener-sector" "-filter"),
                                    options=sector_options,
                                    value="all",
                                    clearable=False,
                                ),
                            ],
                            xs=12,
                            md=4,
                            className="mb-3",
                        ),
                    ],
                    className=("bg-light rounded" " p-3 mb-4 border"),
                ),
                dcc.Loading(
                    id="loading-screener",
                    type="circle",
                    color="#4f46e5",
                    children=html.Div(id="screener-table-container"),
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            html.Small(
                                id="screener-count-text",
                                className="text-muted",
                            ),
                            width="auto",
                            className="my-auto",
                        ),
                        dbc.Col(
                            dbc.Pagination(
                                id="screener-pagination",
                                max_value=1,
                                active_page=1,
                                fully_expanded=False,
                                size="sm",
                                className="justify-content-end mb-0",
                            ),
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
                    ],
                    className="mt-2 align-items-center",
                ),
            ],
        ),
    ]


def _targets_tab(
    ticker_options: List[dict],
    sector_options: List[dict] | None = None,
) -> List:
    """Build the Price Targets tab panel children.

    Args:
        ticker_options: Ticker dropdown options including
            an "All tickers" sentinel.
        sector_options: Sector dropdown options.

    Returns:
        List of Dash components for the Price Targets tab.
    """
    sector_options = sector_options or []
    return [
        html.Div(
            className="mt-3",
            children=[
                html.P(
                    "Latest AI-generated price targets"
                    " from Prophet forecasts.",
                    className="text-muted mb-3",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.Label(
                                    "Ticker",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dcc.Dropdown(
                                    id=("targets-ticker" "-dropdown"),
                                    options=ticker_options,
                                    value="all",
                                    clearable=False,
                                ),
                            ],
                            xs=12,
                            md=4,
                            className="mb-3",
                        ),
                        dbc.Col(
                            [
                                html.Label(
                                    "Market",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dbc.RadioItems(
                                    id="targets-market-filter",
                                    options=[
                                        {
                                            "label": "All",
                                            "value": "all",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1ee"
                                                "\U0001f1f3"
                                                " India"
                                            ),
                                            "value": "india",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1fa" "\U0001f1f8" " US"
                                            ),
                                            "value": "us",
                                        },
                                    ],
                                    value="all",
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
                                html.Label(
                                    "Sector",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dcc.Dropdown(
                                    id=("targets-sector" "-filter"),
                                    options=sector_options,
                                    value="all",
                                    clearable=False,
                                ),
                            ],
                            xs=12,
                            md=4,
                            className="mb-3",
                        ),
                    ],
                    className=("bg-light rounded" " p-3 mb-4 border"),
                ),
                dcc.Loading(
                    type="circle",
                    color="#4f46e5",
                    children=html.Div(id="targets-table-container"),
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            html.Small(
                                id="targets-count-text", className="text-muted"
                            ),
                            width="auto",
                            className="my-auto",
                        ),
                        dbc.Col(
                            dbc.Pagination(
                                id="targets-pagination",
                                max_value=1,
                                active_page=1,
                                fully_expanded=False,
                                size="sm",
                                className="justify-content-end mb-0",
                            ),
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
                    ],
                    className="mt-2 align-items-center",
                ),
            ],
        ),
    ]


def _dividends_tab(
    ticker_options: List[dict],
    sector_options: List[dict] | None = None,
) -> List:
    """Build the Dividends tab panel children.

    Args:
        ticker_options: Ticker dropdown options including
            an "All tickers" sentinel.
        sector_options: Sector dropdown options.

    Returns:
        List of Dash components for the Dividends tab.
    """
    sector_options = sector_options or []
    return [
        html.Div(
            className="mt-3",
            children=[
                html.P(
                    "Full dividend payment history " "for all tracked stocks.",
                    className="text-muted mb-3",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.Label(
                                    "Ticker",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dcc.Dropdown(
                                    id=("dividends-ticker" "-dropdown"),
                                    options=ticker_options,
                                    value="all",
                                    clearable=False,
                                ),
                            ],
                            xs=12,
                            md=4,
                            className="mb-3",
                        ),
                        dbc.Col(
                            [
                                html.Label(
                                    "Market",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dbc.RadioItems(
                                    id=("dividends-market" "-filter"),
                                    options=[
                                        {
                                            "label": "All",
                                            "value": "all",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1ee"
                                                "\U0001f1f3"
                                                " India"
                                            ),
                                            "value": "india",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1fa" "\U0001f1f8" " US"
                                            ),
                                            "value": "us",
                                        },
                                    ],
                                    value="all",
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
                                html.Label(
                                    "Sector",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dcc.Dropdown(
                                    id=("dividends-sector" "-filter"),
                                    options=sector_options,
                                    value="all",
                                    clearable=False,
                                ),
                            ],
                            xs=12,
                            md=4,
                            className="mb-3",
                        ),
                    ],
                    className=("bg-light rounded" " p-3 mb-4 border"),
                ),
                dcc.Loading(
                    type="circle",
                    color="#4f46e5",
                    children=html.Div(id="dividends-table-container"),
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            html.Small(
                                id="dividends-count-text",
                                className="text-muted",
                            ),
                            width="auto",
                            className="my-auto",
                        ),
                        dbc.Col(
                            dbc.Pagination(
                                id="dividends-pagination",
                                max_value=1,
                                active_page=1,
                                fully_expanded=False,
                                size="sm",
                                className="justify-content-end mb-0",
                            ),
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
                    ],
                    className="mt-2 align-items-center",
                ),
            ],
        ),
    ]


def _risk_tab(
    ticker_options: List[dict],
    sector_options: List[dict] | None = None,
) -> List:
    """Build the Risk Metrics tab panel children.

    Args:
        ticker_options: Ticker dropdown options (unused
            but kept for API consistency).
        sector_options: Sector dropdown options.

    Returns:
        List of Dash components for the Risk Metrics tab.
    """
    sector_options = sector_options or []
    return [
        html.Div(
            className="mt-3",
            children=[
                html.P(
                    "Volatility, drawdown, and "
                    "risk-adjusted return metrics "
                    "for all tracked stocks.",
                    className="text-muted mb-3",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.Label(
                                    "Market",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dbc.RadioItems(
                                    id="risk-market-filter",
                                    options=[
                                        {
                                            "label": "All",
                                            "value": "all",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1ee"
                                                "\U0001f1f3"
                                                " India"
                                            ),
                                            "value": "india",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1fa" "\U0001f1f8" " US"
                                            ),
                                            "value": "us",
                                        },
                                    ],
                                    value="all",
                                    inline=True,
                                    className="mt-1",
                                ),
                            ],
                            xs=12,
                            md=6,
                            className="mb-3",
                        ),
                        dbc.Col(
                            [
                                html.Label(
                                    "Sector",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dcc.Dropdown(
                                    id="risk-sector-filter",
                                    options=sector_options,
                                    value="all",
                                    clearable=False,
                                ),
                            ],
                            xs=12,
                            md=6,
                            className="mb-3",
                        ),
                    ],
                    className=("bg-light rounded" " p-3 mb-4 border"),
                ),
                dcc.Loading(
                    type="circle",
                    color="#4f46e5",
                    children=html.Div(id="risk-table-container"),
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            html.Small(
                                id="risk-count-text", className="text-muted"
                            ),
                            width="auto",
                            className="my-auto",
                        ),
                        dbc.Col(
                            dbc.Pagination(
                                id="risk-pagination",
                                max_value=1,
                                active_page=1,
                                fully_expanded=False,
                                size="sm",
                                className="justify-content-end mb-0",
                            ),
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
                    ],
                    className="mt-2 align-items-center",
                ),
            ],
        ),
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
        html.Div(
            className="mt-3",
            children=[
                html.P(
                    "Average technical signals and returns "
                    "grouped by sector.",
                    className="text-muted mb-3",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.Label(
                                    "Market",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dbc.RadioItems(
                                    id="sectors-market-filter",
                                    options=[
                                        {
                                            "label": "All",
                                            "value": "all",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1ee"
                                                "\U0001f1f3"
                                                " India"
                                            ),
                                            "value": "india",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1fa" "\U0001f1f8" " US"
                                            ),
                                            "value": "us",
                                        },
                                    ],
                                    value="india",
                                    inline=True,
                                    className="mt-1",
                                ),
                            ],
                            xs=12,
                            md=6,
                            className="mb-3",
                        ),
                    ],
                    className=("bg-light rounded p-3 mb-4 border"),
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dcc.Loading(
                                    type="circle",
                                    color="#4f46e5",
                                    children=dcc.Graph(
                                        id="sectors-bar-chart",
                                        config={"displayModeBar": False},
                                        style={"height": "340px"},
                                    ),
                                ),
                            ],
                            xs=12,
                            lg=6,
                        ),
                        dbc.Col(
                            [
                                dcc.Loading(
                                    type="circle",
                                    color="#4f46e5",
                                    children=html.Div(
                                        id=("sectors-table" "-container")
                                    ),
                                ),
                            ],
                            xs=12,
                            lg=6,
                        ),
                    ]
                ),
            ],
        ),
    ]


def _correlation_tab(
    ticker_options: List[dict],
    sector_options: List[dict] | None = None,
) -> List:
    """Build the Returns Correlation tab panel children.

    Args:
        ticker_options: Ticker dropdown options (unused
            but kept for API consistency).
        sector_options: Sector dropdown options.

    Returns:
        List of Dash components for the Correlation tab.
    """
    sector_options = sector_options or []
    return [
        html.Div(
            className="mt-3",
            children=[
                html.P(
                    "Pairwise daily-returns correlation"
                    " across all tracked stocks.",
                    className="text-muted mb-3",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.Label(
                                    "Lookback Period",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dbc.RadioItems(
                                    id="corr-period-filter",
                                    options=[
                                        {
                                            "label": "1 Year",
                                            "value": "1y",
                                        },
                                        {
                                            "label": "3 Years",
                                            "value": "3y",
                                        },
                                        {
                                            "label": "All",
                                            "value": "all",
                                        },
                                    ],
                                    value="1y",
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
                                html.Label(
                                    "Market",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dbc.RadioItems(
                                    id="corr-market-filter",
                                    options=[
                                        {
                                            "label": "All",
                                            "value": "all",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1ee"
                                                "\U0001f1f3"
                                                " India"
                                            ),
                                            "value": "india",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1fa" "\U0001f1f8" " US"
                                            ),
                                            "value": "us",
                                        },
                                    ],
                                    value="all",
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
                                html.Label(
                                    "Sector",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dcc.Dropdown(
                                    id="corr-sector-filter",
                                    options=sector_options,
                                    value="all",
                                    clearable=False,
                                ),
                            ],
                            xs=12,
                            md=4,
                            className="mb-3",
                        ),
                    ],
                    className=("bg-light rounded" " p-3 mb-4 border"),
                ),
                dcc.Loading(
                    type="circle",
                    color="#4f46e5",
                    children=dcc.Graph(
                        id="correlation-heatmap",
                        config={"displayModeBar": False},
                        style={"height": "600px"},
                    ),
                ),
            ],
        ),
    ]


def _quarterly_tab(
    ticker_options: List[dict],
    sector_options: List[dict] | None = None,
) -> List:
    """Build the Quarterly Results tab panel children.

    Args:
        ticker_options: Ticker dropdown options including
            an "All tickers" sentinel.
        sector_options: Sector dropdown options.

    Returns:
        List of Dash components for the Quarterly tab.
    """
    sector_options = sector_options or []
    # Default to first Indian ticker, else first ticker
    _default_ticker = "all"
    for opt in ticker_options:
        v = opt.get("value", "")
        if v != "all" and (v.endswith(".NS") or v.endswith(".BO")):
            _default_ticker = v
            break
    if _default_ticker == "all" and len(ticker_options) > 1:
        _default_ticker = ticker_options[1]["value"]
    return [
        html.Div(
            className="mt-3",
            children=[
                html.P(
                    "Quarterly income statement, "
                    "balance sheet, and cash flow "
                    "data for tracked stocks.",
                    className="text-muted mb-3",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.Label(
                                    "Ticker",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dcc.Dropdown(
                                    id=("quarterly-ticker" "-filter"),
                                    options=ticker_options,
                                    value=_default_ticker,
                                    clearable=False,
                                ),
                            ],
                            xs=12,
                            md=3,
                            className="mb-3",
                        ),
                        dbc.Col(
                            [
                                html.Label(
                                    "Market",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dbc.RadioItems(
                                    id=("quarterly-market" "-filter"),
                                    options=[
                                        {
                                            "label": "All",
                                            "value": "all",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1ee"
                                                "\U0001f1f3"
                                                " India"
                                            ),
                                            "value": "india",
                                        },
                                        {
                                            "label": (
                                                "\U0001f1fa" "\U0001f1f8" " US"
                                            ),
                                            "value": "us",
                                        },
                                    ],
                                    value="india",
                                    inline=True,
                                    className="mt-1",
                                ),
                            ],
                            xs=12,
                            md=3,
                            className="mb-3",
                        ),
                        dbc.Col(
                            [
                                html.Label(
                                    "Sector",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dcc.Dropdown(
                                    id=("quarterly-sector" "-filter"),
                                    options=sector_options,
                                    value="all",
                                    clearable=False,
                                ),
                            ],
                            xs=12,
                            md=3,
                            className="mb-3",
                        ),
                        dbc.Col(
                            [
                                html.Label(
                                    "Statement",
                                    className=(
                                        "text-muted small" " fw-semibold"
                                    ),
                                ),
                                dbc.RadioItems(
                                    id=("quarterly" "-statement" "-filter"),
                                    options=[
                                        {
                                            "label": ("Income"),
                                            "value": ("income"),
                                        },
                                        {
                                            "label": ("Balance"),
                                            "value": ("balance"),
                                        },
                                        {
                                            "label": ("Cash Flow"),
                                            "value": ("cashflow"),
                                        },
                                    ],
                                    value="income",
                                    inline=True,
                                    className="mt-1",
                                ),
                            ],
                            xs=12,
                            md=3,
                            className="mb-3",
                        ),
                    ],
                    className=("bg-light rounded" " p-3 mb-4 border"),
                ),
                dcc.Loading(
                    type="circle",
                    color="#4f46e5",
                    children=dcc.Graph(
                        id="quarterly-chart",
                        config={"displayModeBar": False},
                        style={"height": "380px"},
                    ),
                ),
                dcc.Loading(
                    type="circle",
                    color="#4f46e5",
                    children=html.Div(
                        id="quarterly-table-container",
                    ),
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            html.Small(
                                id="quarterly-count-text",
                                className="text-muted",
                            ),
                            width="auto",
                            className="my-auto",
                        ),
                        dbc.Col(
                            dbc.Pagination(
                                id="quarterly-pagination",
                                max_value=1,
                                active_page=1,
                                fully_expanded=False,
                                size="sm",
                                className=("justify-content-end" " mb-0"),
                            ),
                            className=(
                                "d-flex" " justify-content-end" " my-auto"
                            ),
                        ),
                        dbc.Col(
                            dbc.Select(
                                id="quarterly-page-size",
                                options=[
                                    {
                                        "label": "10 / page",
                                        "value": "10",
                                    },
                                    {
                                        "label": "25 / page",
                                        "value": "25",
                                    },
                                    {
                                        "label": "50 / page",
                                        "value": "50",
                                    },
                                ],
                                value="10",
                                size="sm",
                                style={"width": "120px"},
                            ),
                            width="auto",
                            className="my-auto",
                        ),
                    ],
                    className=("mt-2 align-items-center"),
                ),
            ],
        ),
    ]
