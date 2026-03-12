"""Home-page Dash callbacks for the AI Stock Analysis Dashboard.

Registers callbacks that refresh stock cards, manage the market
filter, handle pagination, navigate to analysis, and run
per-card background data refreshes.

Example::

    from dashboard.callbacks.home_cbs import register
    register(app)
"""

from __future__ import annotations

import logging
import math
import time as _time

import dash_bootstrap_components as dbc
from dash import ALL, MATCH, Input, Output, State, ctx, html, no_update

from dashboard.callbacks.auth_utils import (
    _api_call,
    _resolve_token,
    _validate_token,
)
from dashboard.callbacks.data_loaders import (
    _clear_indicator_cache,
    _load_raw,
    _load_reg_cb,
)
from dashboard.callbacks.iceberg import (
    _get_company_info_cached,
    _get_forecast_runs_cached,
    _get_iceberg_repo,
    clear_caches,
)
from dashboard.callbacks.refresh_state import RefreshManager
from dashboard.callbacks.utils import _currency_symbol, _get_market
from dashboard.components.error_overlay import make_error_banner
from dashboard.services.stock_refresh import run_full_refresh

# Module-level logger (immutable singleton — not mutable state).
_logger = logging.getLogger(__name__)


def register(app, mgr: RefreshManager) -> None:
    """Register home-page callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
        mgr: Thread-safe refresh manager for background jobs.
    """

    @app.callback(
        [
            Output("stock-raw-data-store", "data"),
            Output("home-registry-dropdown", "options"),
        ],
        [
            Input("registry-refresh", "n_intervals"),
            Input("url", "pathname"),
            Input("home-card-refresh-trigger", "data"),
        ],
        [
            State("auth-token-store", "data"),
            State("url", "search"),
        ],
    )
    def refresh_stock_cards(
        n_intervals, pathname, refresh_trigger, token, search
    ):
        """Load stock data and store raw dicts for rendering.

        Fires on page load, interval tick, or after a
        per-card background refresh completes.  Cards are
        filtered to the current user's linked tickers when
        a valid token is available; the dropdown always
        shows all registry tickers.

        Args:
            n_intervals: Auto-refresh interval counter.
            pathname: Current URL path.
            refresh_trigger: Incremented when a card
                refresh completes.
            token: JWT access token from localStorage.
            search: URL query string for token fallback.

        Returns:
            Tuple of (list of raw card data dicts,
            dropdown options list).
        """
        token = _resolve_token(token, search)
        t0 = _time.monotonic()
        registry = _load_reg_cb()
        if not registry:
            return [], []

        # Filter cards + dropdown by user's linked tickers
        user_tickers = None
        if token:
            resp = _api_call("get", "/users/me/tickers", token)
            if resp is not None and resp.status_code == 200:
                data = resp.json()
                user_tickers = set(data.get("tickers", []))

        # Dropdown shows user's tickers (or all if no auth)
        if user_tickers is not None:
            dropdown_tickers = sorted(t for t in registry if t in user_tickers)
        else:
            dropdown_tickers = sorted(registry.keys())
        dropdown_options = [{"label": t, "value": t} for t in dropdown_tickers]

        if user_tickers is not None:
            filtered_registry = {
                k: v for k, v in registry.items() if k in user_tickers
            }
        else:
            filtered_registry = registry

        # -- Batch pre-fetch (2 Iceberg scans total) --
        company_map: dict = {}
        currency_map: dict = {}
        sentiment_map: dict = {}
        repo = _get_iceberg_repo()
        if repo is not None:
            try:
                tp = _time.monotonic()
                ci = _get_company_info_cached(repo)
                if ci is not None and not ci.empty:
                    for _, row in ci.iterrows():
                        t = row.get("ticker")
                        if t:
                            company_map[t] = row.get("company_name") or t
                            cur = row.get("currency") or "USD"
                            currency_map[t] = _currency_symbol(cur)
                fr = _get_forecast_runs_cached(repo, 9)
                if fr is not None and not fr.empty:
                    for _, row in fr.iterrows():
                        t = row.get("ticker")
                        s = row.get("sentiment")
                        if t and s:
                            sentiment_map[t] = s
                elapsed = (_time.monotonic() - tp) * 1000
                _logger.info(
                    "Home batch pre-fetch: %.0fms" " (%d tickers)",
                    elapsed,
                    len(filtered_registry),
                )
            except Exception as exc:
                _logger.warning("Batch pre-fetch error: %s", exc)

        card_data = []
        for ticker, entry in sorted(filtered_registry.items()):
            last_updated = entry.get("last_fetch_date", "Unknown")
            raw_df = _load_raw(ticker)

            # Current price + 10Y return
            current_price_str = "N/A"
            total_return_str = "N/A"
            return_color_cls = "text-muted"
            try:
                if raw_df is not None and len(raw_df) > 1:
                    cp = float(raw_df["Close"].iloc[-1])
                    fp = float(raw_df["Close"].iloc[0])
                    tr = (cp / fp - 1) * 100
                    sym = currency_map.get(ticker, "$")
                    current_price_str = f"{sym}{cp:,.2f}"
                    total_return_str = f"{tr:+.1f}%"
                    return_color_cls = (
                        "text-success" if tr >= 0 else "text-danger"
                    )
            except Exception as exc:
                _logger.warning(
                    "Card data error for %s: %s",
                    ticker,
                    exc,
                )

            # Sentiment from batch forecast runs
            sentiment = "Unknown"
            sent_color = "secondary"
            sent_emoji = "\u26aa"
            _sent = sentiment_map.get(ticker)
            if _sent == "Bullish":
                sentiment = "Bullish"
                sent_color = "success"
                sent_emoji = "\U0001f7e2"
            elif _sent == "Bearish":
                sentiment = "Bearish"
                sent_color = "danger"
                sent_emoji = "\U0001f534"
            elif _sent:
                sentiment = "Neutral"
                sent_color = "warning"
                sent_emoji = "\U0001f7e1"

            # Company name from batch company info
            company = company_map.get(ticker, ticker)

            card_data.append(
                {
                    "ticker": ticker,
                    "company": company,
                    "current_price_str": current_price_str,
                    "total_return_str": total_return_str,
                    "return_color_cls": return_color_cls,
                    "last_updated": last_updated,
                    "sentiment": sentiment,
                    "sent_color": sent_color,
                    "sent_emoji": sent_emoji,
                    "market": _get_market(ticker),
                }
            )

        total_ms = (_time.monotonic() - t0) * 1000
        _logger.info("Home cards built: %.0fms total", total_ms)
        return card_data, dropdown_options

    @app.callback(
        Output("market-filter-store", "data"),
        Output("filter-india-btn", "color"),
        Output("filter-us-btn", "color"),
        Output("home-pagination", "active_page"),
        Input("filter-india-btn", "n_clicks"),
        Input("filter-us-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def update_market_filter(india_clicks, us_clicks):
        """Toggle market filter between India and US.

        Args:
            india_clicks: Click count on India filter.
            us_clicks: Click count on US filter.

        Returns:
            Tuple of (market, india color, us color,
            reset page).
        """
        if ctx.triggered_id == "filter-us-btn":
            return "us", "outline-secondary", "primary", 1
        return "india", "primary", "outline-secondary", 1

    @app.callback(
        Output(
            "home-pagination",
            "active_page",
            allow_duplicate=True,
        ),
        Input("home-page-size", "value"),
        prevent_initial_call=True,
    )
    def reset_home_page_on_size_change(page_size):
        """Reset pagination to page 1 on size change.

        Args:
            page_size: New page size value.

        Returns:
            Integer ``1`` to reset the active page.
        """
        return 1

    @app.callback(
        Output("stock-cards-container", "children"),
        Output("home-pagination", "max_value"),
        Output("home-count-text", "children"),
        Input("stock-raw-data-store", "data"),
        Input("market-filter-store", "data"),
        Input("home-pagination", "active_page"),
        Input("home-page-size", "value"),
    )
    def render_home_cards(raw_data, market_filter, active_page, page_size):
        """Filter, paginate, and render stock cards.

        Each card is wrapped in a ``position-relative``
        container with the clickable link and a refresh
        button overlay as siblings (so clicking the button
        does not navigate).

        Args:
            raw_data: List of raw card data dicts.
            market_filter: ``'india'`` or ``'us'``.
            active_page: Current page (1-indexed).
            page_size: Cards per page as string.

        Returns:
            Tuple of (card columns, max_value, count
            text).
        """
        page_size_int = int(page_size or 12)
        if not raw_data:
            return (
                [
                    dbc.Col(
                        html.P(
                            "No stocks saved yet."
                            " Analyse a stock via the"
                            " chat interface first.",
                            className="text-muted",
                        )
                    )
                ],
                1,
                "",
            )

        market = market_filter or "india"
        page = active_page or 1
        filtered = [d for d in raw_data if d.get("market") == market]

        if not filtered:
            label = "India (.NS / .BO)" if market == "india" else "US"
            return (
                [
                    dbc.Col(
                        html.P(
                            f"No {label} stocks saved" " yet.",
                            className="text-muted",
                        )
                    )
                ],
                1,
                "",
            )

        total = len(filtered)
        max_pages = max(1, math.ceil(total / page_size_int))
        page = min(page, max_pages)
        start = (page - 1) * page_size_int
        page_data = filtered[start : start + page_size_int]
        count_txt = (
            f"Showing {start + 1}"
            f"\u2013{min(start + page_size_int, total)}"
            f" of {total}"
        )

        cols = []
        for d in page_data:
            ticker = d["ticker"]
            link = html.A(
                href=f"/analysis?ticker={ticker}",
                className="text-decoration-none",
                children=dbc.Card(
                    [
                        dbc.CardBody(
                            [
                                html.Div(
                                    [
                                        html.H6(
                                            ticker,
                                            className=(
                                                "card-title"
                                                " text-info"
                                                " mb-0"
                                                " fw-bold"
                                            ),
                                        ),
                                        dbc.Badge(
                                            (
                                                f"{d['sent_emoji']}"
                                                f" {d['sentiment']}"
                                            ),
                                            color=d["sent_color"],
                                            className=("ms-auto"),
                                        ),
                                    ],
                                    className=(
                                        "d-flex"
                                        " justify-content-between"
                                        " align-items-center"
                                        " mb-1"
                                    ),
                                ),
                                html.P(
                                    d["company"],
                                    className=(
                                        "card-subtitle"
                                        " text-muted"
                                        " small mb-2"
                                        " text-truncate"
                                    ),
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.Small(
                                                    "Price",
                                                    className=(
                                                        "text-muted" " d-block"
                                                    ),
                                                ),
                                                html.Strong(
                                                    d["current_price_str"],
                                                    className=(
                                                        "text-dark" " small"
                                                    ),
                                                ),
                                            ],
                                            className=("me-3"),
                                        ),
                                        html.Div(
                                            [
                                                html.Small(
                                                    "10Y Ret",
                                                    className=(
                                                        "text-muted" " d-block"
                                                    ),
                                                ),
                                                html.Strong(
                                                    d["total_return_str"],
                                                    className=(
                                                        d["return_color_cls"]
                                                        + " small"
                                                    ),
                                                ),
                                            ],
                                            className=("me-3"),
                                        ),
                                        html.Div(
                                            [
                                                html.Small(
                                                    "Updated",
                                                    className=(
                                                        "text-muted" " d-block"
                                                    ),
                                                ),
                                                html.Small(
                                                    d["last_updated"],
                                                    className=("text-muted"),
                                                ),
                                            ]
                                        ),
                                    ],
                                    className=("d-flex" " align-items-start"),
                                ),
                            ],
                            className="p-3",
                        ),
                    ],
                    className="stock-card h-100",
                ),
            )
            refresh_overlay = html.Div(
                [
                    dbc.Button(
                        "\u21bb",
                        id={
                            "type": "card-refresh-btn",
                            "index": ticker,
                        },
                        className=(
                            "card-refresh-icon-btn"
                            " btn btn-outline-success"
                            " btn-sm"
                        ),
                        title=(f"Refresh {ticker} data"),
                        n_clicks=0,
                    ),
                    html.Span(
                        id={
                            "type": "card-refresh-status",
                            "index": ticker,
                        },
                    ),
                ],
                className="card-refresh-wrapper",
            )
            wrapper = html.Div(
                [link, refresh_overlay],
                style={"position": "relative"},
            )
            cols.append(dbc.Col(wrapper, xs=12, sm=6, md=4, lg=3))

        return cols, max_pages, count_txt

    @app.callback(
        Output(
            {"type": "card-refresh-status", "index": MATCH},
            "children",
        ),
        Output(
            {"type": "card-refresh-btn", "index": MATCH},
            "disabled",
        ),
        Input(
            {"type": "card-refresh-btn", "index": MATCH},
            "n_clicks",
        ),
        State("auth-token-store", "data"),
        prevent_initial_call=True,
    )
    def start_card_refresh(n_clicks, token):
        """Submit a background refresh for a single ticker.

        Validates the JWT, checks for a duplicate in-flight
        job, then submits ``run_full_refresh`` to the
        thread pool.

        Args:
            n_clicks: Button click counter.
            token: JWT access token from localStorage.

        Returns:
            Tuple of (spinner span, disabled flag).
        """
        if not n_clicks:
            return no_update, no_update

        if _validate_token(token) is None:
            return (
                html.Span(
                    "\u2717",
                    className=("card-refresh-status-icon" " text-warning"),
                    title="Sign in required",
                ),
                False,
            )

        ticker = ctx.triggered_id["index"]

        # Prevent duplicate in-flight jobs
        if not mgr.submit_if_idle(ticker, run_full_refresh, ticker, 9):
            return no_update, no_update

        _logger.info("Card refresh submitted for %s", ticker)

        spinner = html.Span(
            className="card-refresh-spinner",
        )
        return spinner, True

    @app.callback(
        Output(
            {"type": "card-refresh-status", "index": ALL},
            "children",
            allow_duplicate=True,
        ),
        Output(
            {"type": "card-refresh-btn", "index": ALL},
            "disabled",
            allow_duplicate=True,
        ),
        Output(
            "home-card-refresh-trigger",
            "data",
            allow_duplicate=True,
        ),
        Output(
            "error-overlay-container",
            "children",
            allow_duplicate=True,
        ),
        Input("card-refresh-poll", "n_intervals"),
        State(
            {"type": "card-refresh-status", "index": ALL},
            "id",
        ),
        State("home-card-refresh-trigger", "data"),
        prevent_initial_call=True,
    )
    def poll_card_refreshes(n_intervals, status_ids, current_trigger):
        """Poll background refresh futures every 2 s.

        For each visible card, checks if a future exists
        in the refresh manager.  Completed futures are
        harvested: success shows a green check, failure
        shows a red cross.  Caches are cleared and the
        trigger store is incremented to reload card data.

        Args:
            n_intervals: Interval tick counter.
            status_ids: List of pattern-match id dicts.
            current_trigger: Current trigger store value.

        Returns:
            Tuple of (status children list, disabled
            flags list, new trigger value).
        """
        if not status_ids:
            return [], [], no_update, no_update

        statuses = []
        disabled_flags = []
        any_completed = False
        error_msgs: list[str] = []

        for sid in status_ids:
            ticker = sid["index"]
            fut = mgr.get(ticker)

            if fut is None or not fut.done():
                statuses.append(no_update)
                disabled_flags.append(no_update)
                continue

            any_completed = True
            try:
                result = fut.result()
                if result.success:
                    _logger.info(
                        "Card refresh OK for %s",
                        ticker,
                    )
                    statuses.append(
                        html.Span(
                            "\u2713",
                            className=(
                                "card-refresh-status-icon" " text-success"
                            ),
                            title=(f"Refresh complete" f" for {ticker}"),
                        )
                    )
                else:
                    err = result.error or "Unknown"
                    _logger.warning(
                        "Card refresh failed for" " %s: %s",
                        ticker,
                        err,
                    )
                    statuses.append(
                        html.Span(
                            "\u2717",
                            className=(
                                "card-refresh-status-icon" " text-danger"
                            ),
                            title=err[:200],
                        )
                    )
                    error_msgs.append(f"{ticker}: {err[:120]}")
            except Exception as exc:
                _logger.error(
                    "Card refresh exception for" " %s: %s",
                    ticker,
                    exc,
                )
                statuses.append(
                    html.Span(
                        "\u2717",
                        className=("card-refresh-status-icon" " text-danger"),
                        title=str(exc)[:200],
                    )
                )
                error_msgs.append(f"{ticker}: {str(exc)[:120]}")

            disabled_flags.append(False)
            clear_caches(ticker)
            _clear_indicator_cache(ticker)
            mgr.pop(ticker)

        trigger = (current_trigger or 0) + 1 if any_completed else no_update
        overlay = (
            make_error_banner("Refresh failed \u2014 " + "; ".join(error_msgs))
            if error_msgs
            else no_update
        )
        return statuses, disabled_flags, trigger, overlay

    @app.callback(
        [
            Output("url", "pathname"),
            Output("nav-ticker-store", "data"),
        ],
        [
            Input("search-btn", "n_clicks"),
            Input("home-registry-dropdown", "value"),
        ],
        [State("ticker-search-input", "value")],
        prevent_initial_call=True,
    )
    def navigate_to_analysis(search_clicks, dropdown_val, search_input):
        """Navigate to analysis when a ticker is selected.

        Args:
            search_clicks: Analyse button click count.
            dropdown_val: Dropdown selected value.
            search_input: Text from the search input.

        Returns:
            Tuple of (new pathname, ticker to store).
        """
        triggered = ctx.triggered_id
        if triggered == "search-btn":
            if not search_input:
                return no_update, no_update
            return "/analysis", search_input.upper().strip()
        if triggered == "home-registry-dropdown" and dropdown_val:
            return "/analysis", dropdown_val
        return no_update, no_update
