dashboard/callbacks/home_cbs.py
"""Home-page Dash callbacks for the AI Stock Analysis Dashboard.

Registers callbacks that refresh stock cards, manage the market filter,
handle pagination, and navigate to the analysis page from the home page.

Example::

    from dashboard.callbacks.home_cbs import register
    register(app)
"""

import logging
import math
from typing import Optional
from urllib.parse import parse_qs

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, ctx, html, no_update

from dashboard.callbacks.data_loaders import (
    _DATA_FORECASTS,
    _DATA_METADATA,
    _load_raw,
    _load_reg_cb,
)
from dashboard.callbacks.utils import _get_currency, _get_market

# Module-level logger — kept at module scope for callback functions defined outside a class.
_logger = logging.getLogger(__name__)


def register(app) -> None:
    """Register home-page callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """

    @app.callback(
        [
            Output("stock-raw-data-store", "data"),
            Output("home-registry-dropdown", "options"),
        ],
        [
            Input("registry-refresh", "n_intervals"),
            Input("url", "pathname"),
        ],
    )
    def refresh_stock_cards(n_intervals, pathname):
        """Load stock data from the registry and store raw dicts for rendering.

        Fires on page load or interval tick.  Stores serialisable dicts so that
        ``render_home_cards`` can filter and paginate without repeating I/O.

        Args:
            n_intervals: Auto-refresh interval counter.
            pathname: Current URL path.

        Returns:
            Tuple of (list of raw card data dicts, dropdown options list).
        """
        import json

        registry = _load_reg_cb()
        if not registry:
            return [], []

        dropdown_options = [{"label": t, "value": t} for t in sorted(registry.keys())]
        card_data = []

        for ticker, entry in sorted(registry.items()):
            last_updated = entry.get("last_fetch_date", "Unknown")

            # Current price + 10Y return from parquet
            current_price_str = "N/A"
            total_return_str  = "N/A"
            return_color_cls  = "text-muted"
            try:
                df = _load_raw(ticker)
                if df is not None and len(df) > 1:
                    cp = float(df["Close"].iloc[-1])
                    fp = float(df["Close"].iloc[0])
                    tr = (cp / fp - 1) * 100
                    current_price_str = f"{_get_currency(ticker)}{cp:,.2f}"
                    total_return_str  = f"{tr:+.1f}%"
                    return_color_cls  = "text-success" if tr >= 0 else "text-danger"
            except Exception as exc:
                _logger.warning("Card data error for %s: %s", ticker, exc)

            # Sentiment from forecast parquet
            sentiment  = "Unknown"
            sent_color = "secondary"
            sent_emoji = "⚪"
            try:
                forecast_files = list(_DATA_FORECASTS.glob(f"{ticker}_*m_forecast.parquet"))
                if forecast_files:
                    latest = max(forecast_files, key=lambda p: p.stat().st_mtime)
                    fc_df  = pd.read_parquet(latest, engine="pyarrow")
                    df_raw = _load_raw(ticker)
                    if df_raw is not None and len(fc_df) > 0:
                        cp  = float(df_raw["Close"].iloc[-1])
                        fp  = float(fc_df["yhat"].iloc[-1])
                        pct = (fp - cp) / cp * 100
                        if pct > 10:
                            sentiment, sent_color, sent_emoji = "Bullish", "success", "🟢"
                        elif pct < -10:
                            sentiment, sent_color, sent_emoji = "Bearish", "danger",  "🔴"
                        else:
                            sentiment, sent_color, sent_emoji = "Neutral", "warning", "🟡"
            except Exception as exc:
                _logger.warning("Sentiment error for %s: %s", ticker, exc)

            # Company name from metadata JSON if available
            company   = ticker
            info_path = _DATA_METADATA / f"{ticker}_info.json"
            if info_path.exists():
                try:
                    with open(info_path) as fh:
                        info    = json.load(fh)
                        company = info.get("name", ticker) or ticker
                except Exception:
                    pass

            card_data.append({
                "ticker":            ticker,
                "company":           company,
                "current_price_str": current_price_str,
                "total_return_str":  total_return_str,
                "return_color_cls":  return_color_cls,
                "last_updated":      last_updated,
                "sentiment":         sentiment,
                "sent_color":        sent_color,
                "sent_emoji":        sent_emoji,
                "market":            _get_market(ticker),
            })

        return card_data, dropdown_options

    @app.callback(
        Output("market-filter-store", "data"),
        Output("filter-india-btn",    "color"),
        Output("filter-us-btn",       "color"),
        Output("home-pagination",     "active_page"),
        Input("filter-india-btn", "n_clicks"),
        Input("filter-us-btn",    "n_clicks"),
        prevent_initial_call=True,
    )
    def update_market_filter(india_clicks, us_clicks):
        """Toggle the market filter store between India and US stocks.

        Args:
            india_clicks: Click count on the India filter button.
            us_clicks: Click count on the US filter button.

        Returns:
            Tuple of (market string, india button color, us button color, reset page).
        """
        if ctx.triggered_id == "filter-us-btn":
            return "us", "outline-secondary", "primary", 1
        return "india", "primary", "outline-secondary", 1

    @app.callback(
        Output("home-pagination", "active_page", allow_duplicate=True),
        Input("home-page-size", "value"),
        prevent_initial_call=True,
    )
    def reset_home_page_on_size_change(page_size):
        """Reset home pagination to page 1 when the page size changes.

        Args:
            page_size: New page size value from the select dropdown.

        Returns:
            Integer ``1`` to reset the active page.
        """
        return 1

    @app.callback(
        Output("stock-cards-container", "children"),
        Output("home-pagination",       "max_value"),
        Output("home-count-text",       "children"),
        Input("stock-raw-data-store", "data"),
        Input("market-filter-store",  "data"),
        Input("home-pagination",      "active_page"),
        Input("home-page-size",       "value"),
    )
    def render_home_cards(raw_data, market_filter, active_page, page_size):
        """Filter, paginate, and render stock cards from stored raw data.

        Args:
            raw_data: List of raw card data dicts from ``stock-raw-data-store``.
            market_filter: Active market string — ``'india'`` or ``'us'``.
            active_page: Current pagination page (1-indexed).
            page_size: Number of cards per page as a string (e.g. ``"10"``).

        Returns:
            Tuple of (list of card columns, pagination max_value, count text).
        """
        page_size_int = int(page_size or 10)
        if not raw_data:
            return (
                [dbc.Col(html.P(
                    "No stocks saved yet. Analyse a stock via the chat interface first.",
                    className="text-muted",
                ))],
                1,
                "",
            )

        market   = market_filter or "india"
        page     = active_page or 1
        filtered = [d for d in raw_data if d.get("market") == market]

        if not filtered:
            label = "India (.NS / .BO)" if market == "india" else "US"
            return (
                [dbc.Col(html.P(f"No {label} stocks saved yet.", className="text-muted"))],
                1,
                "",
            )

        total     = len(filtered)
        max_pages = max(1, math.ceil(total / page_size_int))
        page      = min(page, max_pages)
        start     = (page - 1) * page_size_int
        page_data = filtered[start: start + page_size_int]
        count_txt = f"Showing {start + 1}–{min(start + page_size_int, total)} of {total}"

        cols = []
        for d in page_data:
            card = html.A(
                href=f"/analysis?ticker={d['ticker']}",
                className="text-decoration-none",
                children=dbc.Card([
                    dbc.CardBody([
                        html.Div([
                            html.H5(d["ticker"], className="card-title text-info mb-0"),
                            dbc.Badge(
                                f"{d['sent_emoji']} {d['sentiment']}",
                                color=d["sent_color"],
                                className="ms-auto",
                            ),
                        ], className="d-flex justify-content-between align-items-center mb-1"),
                        html.P(d["company"], className="card-subtitle text-muted small mb-3"),
                        html.Div([
                            html.Div([
                                html.Small("Price", className="text-muted d-block"),
                                html.Strong(d["current_price_str"], className="text-dark"),
                            ], className="me-3"),
                            html.Div([
                                html.Small("10Y Return", className="text-muted d-block"),
                                html.Strong(d["total_return_str"], className=d["return_color_cls"]),
                            ], className="me-3"),
                            html.Div([
                                html.Small("Updated", className="text-muted d-block"),
                                html.Small(d["last_updated"], className="text-muted"),
                            ]),
                        ], className="d-flex align-items-start"),
                    ]),
                ], className="stock-card h-100"),
            )
            cols.append(dbc.Col(card, xs=12, sm=6, md=4, lg=3, className="mb-4"))

        return cols, max_pages, count_txt

    @app.callback(
        [Output("url", "pathname"), Output("nav-ticker-store", "data")],
        [
            Input("search-btn", "n_clicks"),
            Input("home-registry-dropdown", "value"),
        ],
        [State("ticker-search-input", "value")],
        prevent_initial_call=True,
    )
    def navigate_to_analysis(search_clicks, dropdown_val, search_input):
        """Navigate to the analysis page when the user selects or searches a ticker.

        Args:
            search_clicks: Number of times the Analyse button was clicked.
            dropdown_val: Selected value from the home-page dropdown.
            search_input: Text entered in the ticker search input.

        Returns:
            Tuple of (new URL pathname, ticker to store for pre-selection).
        """
        triggered = ctx.triggered_id
        if triggered == "search-btn":
            if not search_input:
                return no_update, no_update
            return "/analysis", search_input.upper().strip()
        if triggered == "home-registry-dropdown" and dropdown_val:
            return "/analysis", dropdown_val
        return no_update, no_update