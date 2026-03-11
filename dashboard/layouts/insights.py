"""Insights page layout for the AI Stock Analysis Dashboard.

Provides :func:`insights_layout`, which builds the unified Insights page
combining Screener, Price Targets, Dividends, Risk Metrics, Sector Analysis,
and Returns Correlation into a single tabbed view backed by the Iceberg
``stocks.*`` tables.
"""

import dash_bootstrap_components as dbc
from dash import dcc, html

from dashboard.layouts.helpers import (
    _get_available_sectors,
    _get_available_tickers,
)
from dashboard.layouts.insights_tabs import (
    _correlation_tab,
    _dividends_tab,
    _quarterly_tab,
    _risk_tab,
    _screener_tab,
    _sectors_tab,
    _targets_tab,
)


def insights_layout() -> html.Div:
    """Build the unified Insights page with seven analysis tabs.

    Combines Screener, Price Targets, Dividends, Risk Metrics, Sector
    Analysis, Returns Correlation, and Quarterly Results into a single
    tabbed page.  All data is sourced from Iceberg ``stocks.*`` tables.

    Returns:
        :class:`~dash.html.Div` representing the full Insights page.
    """
    tickers = _get_available_tickers()
    ticker_options = [
        {"label": "All tickers", "value": "all"},
    ] + [{"label": t, "value": t} for t in tickers]

    sectors = _get_available_sectors()
    sector_options = [
        {"label": "All sectors", "value": "all"},
    ] + [{"label": s, "value": s} for s in sectors]

    return html.Div(
        [
            # ── Tabs ──────────────────────────────────
            dbc.Tabs(
                id="insights-tabs",
                active_tab="screener-tab",
                children=[
                    dbc.Tab(
                        label="Screener",
                        tab_id="screener-tab",
                        children=_screener_tab(
                            ticker_options,
                            sector_options,
                        ),
                    ),
                    dbc.Tab(
                        label="Price Targets",
                        tab_id="targets-tab",
                        children=_targets_tab(
                            ticker_options,
                            sector_options,
                        ),
                    ),
                    dbc.Tab(
                        label="Dividends",
                        tab_id="dividends-tab",
                        children=_dividends_tab(
                            ticker_options,
                            sector_options,
                        ),
                    ),
                    dbc.Tab(
                        label="Risk Metrics",
                        tab_id="risk-tab",
                        children=_risk_tab(
                            ticker_options,
                            sector_options,
                        ),
                    ),
                    dbc.Tab(
                        label="Sectors",
                        tab_id="sectors-tab",
                        children=_sectors_tab(
                            ticker_options,
                        ),
                    ),
                    dbc.Tab(
                        label="Correlation",
                        tab_id="correlation-tab",
                        children=_correlation_tab(
                            ticker_options,
                            sector_options,
                        ),
                    ),
                    dbc.Tab(
                        label="Quarterly",
                        tab_id="quarterly-tab",
                        children=_quarterly_tab(
                            ticker_options,
                            sector_options,
                        ),
                    ),
                ],
            ),
            # ── Hidden sort stores ─────────────────────────────────
            dcc.Store(
                id="screener-sort-store",
                data={"col": None, "dir": "none"},
            ),
            dcc.Store(
                id="targets-sort-store",
                data={"col": None, "dir": "none"},
            ),
            dcc.Store(
                id="dividends-sort-store",
                data={"col": None, "dir": "none"},
            ),
            dcc.Store(
                id="risk-sort-store",
                data={"col": None, "dir": "none"},
            ),
            dcc.Store(
                id="sectors-sort-store",
                data={"col": None, "dir": "none"},
            ),
            dcc.Store(
                id="quarterly-sort-store",
                data={"col": None, "dir": "none"},
            ),
        ]
    )
