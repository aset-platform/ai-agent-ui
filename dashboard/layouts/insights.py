"""Insights page layout for the AI Stock Analysis Dashboard.

Provides :func:`insights_layout`, which builds the unified Insights page
combining Screener, Price Targets, Dividends, Risk Metrics, Sector Analysis,
and Returns Correlation into a single tabbed view backed by the Iceberg
``stocks.*`` tables.
"""

import dash_bootstrap_components as dbc
from dash import html

from dashboard.layouts.helpers import _get_available_tickers
from dashboard.layouts.insights_tabs import (
    _correlation_tab,
    _dividends_tab,
    _risk_tab,
    _screener_tab,
    _sectors_tab,
    _targets_tab,
)


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
    ticker_options = [{"label": "All tickers", "value": "all"}] + [
        {"label": t, "value": t} for t in tickers
    ]

    return html.Div(
        [
            # ── Tabs ─────────────────────────────────────────────────────────
            dbc.Tabs(
                id="insights-tabs",
                active_tab="screener-tab",
                children=[
                    dbc.Tab(
                        label="Screener",
                        tab_id="screener-tab",
                        children=_screener_tab(ticker_options),
                    ),
                    dbc.Tab(
                        label="Price Targets",
                        tab_id="targets-tab",
                        children=_targets_tab(ticker_options),
                    ),
                    dbc.Tab(
                        label="Dividends",
                        tab_id="dividends-tab",
                        children=_dividends_tab(ticker_options),
                    ),
                    dbc.Tab(
                        label="Risk Metrics",
                        tab_id="risk-tab",
                        children=_risk_tab(ticker_options),
                    ),
                    dbc.Tab(
                        label="Sectors",
                        tab_id="sectors-tab",
                        children=_sectors_tab(ticker_options),
                    ),
                    dbc.Tab(
                        label="Correlation",
                        tab_id="correlation-tab",
                        children=_correlation_tab(ticker_options),
                    ),
                ],
            ),
        ]
    )
