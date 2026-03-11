"""Ticker Marketplace callbacks for the AI Stock Analysis Dashboard.

Registers callbacks for loading the marketplace table of all
available tickers and handling add/remove actions for the
user's personal watchlist.  Follows the admin-page pattern:
data-load callback populates a store, render callback handles
filtering, sorting, and pagination.

Example::

    from dashboard.callbacks.marketplace_cbs import register
    register(app)
"""

import logging
import math

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, html, no_update

from dashboard.callbacks.auth_utils import (
    _api_call,
    _resolve_token,
)
from dashboard.callbacks.iceberg import (
    _get_company_info_cached,
    _get_iceberg_repo,
    _get_registry_cached,
)
from dashboard.callbacks.sort_helpers import (
    apply_sort_list,
    build_sortable_thead,
    register_sort_callback,
)

# Module-level logger; mutable but required at module
# scope for callback closures.
_logger = logging.getLogger(__name__)

_MARKETPLACE_COL_DEFS = [
    {"key": "ticker", "label": "Ticker"},
    {"key": "company", "label": "Company"},
    {"key": "market", "label": "Market"},
    {"key": "last_updated", "label": "Last Updated"},
    {"key": "_action", "label": "Action"},
]


def _build_marketplace_row(
    ticker: str,
    company: str,
    market: str,
    last_updated: str,
    is_linked: bool,
) -> html.Tr:
    """Build a single marketplace table row.

    Args:
        ticker: Uppercase ticker symbol.
        company: Company display name.
        market: Market / exchange string.
        last_updated: Human-readable date string.
        is_linked: Whether the ticker is already in
            the user's watchlist.

    Returns:
        A :class:`~dash.html.Tr` table row element.
    """
    if is_linked:
        btn = dbc.Button(
            "Remove",
            id={
                "type": "marketplace-remove-btn",
                "index": ticker,
            },
            color="danger",
            outline=True,
            size="sm",
        )
    else:
        btn = dbc.Button(
            "Add",
            id={
                "type": "marketplace-add-btn",
                "index": ticker,
            },
            color="success",
            outline=True,
            size="sm",
        )

    return html.Tr(
        [
            html.Td(
                html.Strong(ticker),
                className="align-middle",
            ),
            html.Td(company, className="align-middle"),
            html.Td(market, className="align-middle"),
            html.Td(
                last_updated,
                className="align-middle",
            ),
            html.Td(btn, className="align-middle"),
        ]
    )


def _build_marketplace_table(
    rows_data: list,
    user_set: set,
    sort_state: dict,
) -> dbc.Table:
    """Build the full marketplace table with sortable headers.

    Args:
        rows_data: List of row dicts (ticker, company,
            market, last_updated).
        user_set: Set of user's linked ticker symbols.
        sort_state: Current sort state dict.

    Returns:
        A :class:`~dash_bootstrap_components.Table`.
    """
    sort_state = sort_state or {
        "col": None,
        "dir": "none",
    }

    # Sort (skip _action pseudo-column)
    rows_data = apply_sort_list(rows_data, sort_state)

    header = build_sortable_thead(
        _MARKETPLACE_COL_DEFS,
        "marketplace",
        sort_state,
    )

    rows = []
    for item in rows_data:
        ticker = item["ticker"]
        rows.append(
            _build_marketplace_row(
                ticker=ticker,
                company=item.get("company", ""),
                market=item.get("market", ""),
                last_updated=item.get("last_updated", ""),
                is_linked=ticker in user_set,
            )
        )

    if not rows:
        rows = [
            html.Tr(
                html.Td(
                    "No matching tickers found.",
                    colSpan=5,
                    className="text-muted",
                )
            )
        ]

    return dbc.Table(
        [header, html.Tbody(rows)],
        bordered=True,
        hover=True,
        responsive=True,
        striped=True,
        size="sm",
        className="table table-sm align-middle",
    )


def register(app) -> None:
    """Register marketplace callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """

    # -- 1. Data-load callback: full registry → store --
    @app.callback(
        Output("marketplace-store", "data"),
        Output("marketplace-user-tickers", "data"),
        Input("registry-refresh", "n_intervals"),
        Input("url", "pathname"),
        State("auth-token-store", "data"),
        State("url", "search"),
        prevent_initial_call=False,
    )
    def load_marketplace(
        _n_intervals,
        pathname,
        stored_token,
        url_search,
    ):
        """Load all tickers into the marketplace store.

        Fetches the full registry from Iceberg and the
        user's linked tickers from the backend API.
        Only runs on page navigation or periodic refresh
        — NOT on add/remove actions.

        Args:
            _n_intervals: Registry refresh interval
                counter.
            pathname: Current URL path.
            stored_token: JWT from ``auth-token-store``.
            url_search: URL query string for token
                fallback.

        Returns:
            Tuple of (list of row dicts, user tickers
            list).
        """
        if pathname != "/marketplace":
            return no_update, no_update

        token = _resolve_token(stored_token, url_search)

        # Load registry from Iceberg
        repo = _get_iceberg_repo()
        if repo is None:
            return [], []

        registry = _get_registry_cached(repo)
        if not registry:
            return [], []

        # Load company info for display names
        try:
            company_df = _get_company_info_cached(repo)
            company_map = {}
            if company_df is not None and not company_df.empty:
                for _, row in company_df.iterrows():
                    t = row.get("ticker", "")
                    name = row.get("company_name", "") or row.get(
                        "short_name", ""
                    )
                    company_map[t] = name or t
        except Exception:
            company_map = {}

        # Get user's current tickers
        user_tickers = []
        if token:
            resp = _api_call("get", "/users/me/tickers", token)
            if resp is not None and resp.ok:
                data = resp.json()
                if isinstance(data, list):
                    user_tickers = data
                elif isinstance(data, dict):
                    user_tickers = data.get("tickers", [])

        # Build full data list (no filtering here)
        all_rows = []
        for ticker in sorted(registry.keys()):
            meta = registry[ticker]
            company = company_map.get(ticker, "")
            market = meta.get("market", "")
            last_updated = meta.get("last_updated", "") or meta.get(
                "added_date", ""
            )

            all_rows.append(
                {
                    "ticker": ticker,
                    "company": company,
                    "market": market,
                    "last_updated": str(last_updated)[:10],
                }
            )

        return all_rows, user_tickers

    # -- 2. Reset pagination on search/sort/page-size --
    @app.callback(
        Output("marketplace-pagination", "active_page"),
        Input("marketplace-search", "value"),
        Input("marketplace-page-size", "value"),
        Input("marketplace-sort-store", "data"),
        prevent_initial_call=True,
    )
    def reset_marketplace_page(_search, _size, _sort):
        """Reset pagination to page 1 on filter change.

        Args:
            _search: Search input value.
            _size: Page size value.
            _sort: Sort state dict.

        Returns:
            Integer ``1`` to reset to first page.
        """
        return 1

    # -- 3. Render callback: filter → sort → paginate --
    @app.callback(
        Output("marketplace-table-container", "children"),
        Output("marketplace-pagination", "max_value"),
        Output("marketplace-count-text", "children"),
        Input("marketplace-store", "data"),
        Input("marketplace-pagination", "active_page"),
        Input("marketplace-search", "value"),
        Input("marketplace-page-size", "value"),
        Input("marketplace-sort-store", "data"),
        Input("marketplace-user-tickers", "data"),
    )
    def render_marketplace_page(
        all_rows,
        active_page,
        search_term,
        page_size_str,
        sort_state,
        user_tickers,
    ):
        """Filter, sort, paginate and render the table.

        Args:
            all_rows: Full list of row dicts from store.
            active_page: Current page (1-based).
            search_term: Search filter text.
            page_size_str: Rows per page as string.
            sort_state: Column sort state dict.
            user_tickers: User's linked tickers list.

        Returns:
            Tuple of (table component, max_value,
            count text).
        """
        sort_state = sort_state or {
            "col": None,
            "dir": "none",
        }
        if not all_rows:
            return (
                html.P(
                    "No tickers in registry.",
                    className="text-muted mt-3",
                ),
                1,
                "",
            )

        # Search filter
        q = (search_term or "").strip().lower()
        if q:
            all_rows = [
                r
                for r in all_rows
                if q in r.get("ticker", "").lower()
                or q in r.get("company", "").lower()
                or q in r.get("market", "").lower()
            ]

        total = len(all_rows)
        if total == 0:
            return (
                html.P(
                    "No matching tickers found.",
                    className="text-muted mt-3",
                ),
                1,
                "",
            )

        # Pagination math
        page_size = int(page_size_str or 10)
        max_pages = max(1, math.ceil(total / page_size))
        page = min(active_page or 1, max_pages)
        start = (page - 1) * page_size
        end = min(start + page_size, total)

        count_txt = f"Showing {start + 1}" f"\u2013{end} of {total} tickers"

        # Sort + slice for this page
        user_set = set(user_tickers or [])
        table = _build_marketplace_table(
            all_rows[start:end],
            user_set,
            sort_state,
        )

        return table, max_pages, count_txt

    # -- 4. Add/Remove action callback --
    # Outputs to marketplace-user-tickers (client-side
    # list update) instead of marketplace-refresh-trigger
    # so the table re-renders in-place without a full
    # Iceberg reload.
    @app.callback(
        Output("marketplace-alert", "children"),
        Output(
            "marketplace-user-tickers",
            "data",
            allow_duplicate=True,
        ),
        Input(
            {
                "type": "marketplace-add-btn",
                "index": ALL,
            },
            "n_clicks",
        ),
        Input(
            {
                "type": "marketplace-remove-btn",
                "index": ALL,
            },
            "n_clicks",
        ),
        State("auth-token-store", "data"),
        State("url", "search"),
        State("marketplace-user-tickers", "data"),
        prevent_initial_call=True,
    )
    def marketplace_action(
        add_clicks,
        remove_clicks,
        stored_token,
        url_search,
        current_tickers,
    ):
        """Handle add/remove ticker actions in-place.

        Calls the backend API, then updates the
        ``marketplace-user-tickers`` store directly
        so the render callback swaps the button without
        a full data reload.

        Args:
            add_clicks: List of n_clicks for all add
                buttons.
            remove_clicks: List of n_clicks for all
                remove buttons.
            stored_token: JWT from ``auth-token-store``.
            url_search: URL query string for token
                fallback.
            current_tickers: Current user tickers list
                from the store.

        Returns:
            Tuple of (alert component, updated user
            tickers list).
        """
        # Guard: ALL pattern-matching fires on re-render
        # with n_clicks=None.  Only proceed when a button
        # was genuinely clicked.
        all_clicks = (add_clicks or []) + (remove_clicks or [])
        if not any(c for c in all_clicks):
            return no_update, no_update

        triggered = ctx.triggered_id
        if triggered is None:
            return no_update, no_update

        action_type = triggered.get("type", "")
        ticker = triggered.get("index", "")

        if not ticker:
            return no_update, no_update

        token = _resolve_token(stored_token, url_search)
        if not token:
            return (
                dbc.Alert(
                    "Authentication required.",
                    color="warning",
                    dismissable=True,
                    duration=4000,
                ),
                no_update,
            )

        tickers = list(current_tickers or [])

        if action_type == "marketplace-add-btn":
            resp = _api_call(
                "post",
                "/users/me/tickers",
                token,
                {"ticker": ticker},
            )
            if resp is not None and resp.ok:
                if ticker not in tickers:
                    tickers.append(ticker)
                return (
                    dbc.Alert(
                        f"{ticker} added to your" f" watchlist.",
                        color="success",
                        dismissable=True,
                        duration=4000,
                    ),
                    tickers,
                )
            detail = ""
            if resp is not None:
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    detail = resp.text[:120]
            return (
                dbc.Alert(
                    f"Failed to add {ticker}." f" {detail}",
                    color="danger",
                    dismissable=True,
                    duration=4000,
                ),
                no_update,
            )

        if action_type == "marketplace-remove-btn":
            resp = _api_call(
                "delete",
                f"/users/me/tickers/{ticker}",
                token,
            )
            if resp is not None and resp.ok:
                tickers = [t for t in tickers if t != ticker]
                return (
                    dbc.Alert(
                        f"{ticker} removed from" f" your watchlist.",
                        color="info",
                        dismissable=True,
                        duration=4000,
                    ),
                    tickers,
                )
            detail = ""
            if resp is not None:
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    detail = resp.text[:120]
            return (
                dbc.Alert(
                    f"Failed to remove {ticker}." f" {detail}",
                    color="danger",
                    dismissable=True,
                    duration=4000,
                ),
                no_update,
            )

        return no_update, no_update

    # -- 5. Register sort callback --
    register_sort_callback(app, "marketplace")
