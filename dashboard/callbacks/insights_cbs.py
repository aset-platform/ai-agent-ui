"""Insights-page Dash callbacks for the AI Stock Analysis Dashboard.

Registers callbacks for the Screener, Price Targets, Dividends, Risk Metrics,
Sectors, Correlation, and Quarterly Results tabs on the ``/insights`` page.
All tabs read from Iceberg tables via the lazy-singleton
``_get_iceberg_repo()``, with flat-file fallbacks where available.

Example::

    from dashboard.callbacks.insights_cbs import register
    register(app)
"""

import logging
import math
from typing import Any, Optional

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, html, no_update

from dashboard.callbacks.auth_utils import (
    _fetch_user_tickers,
)
from dashboard.callbacks.iceberg import (
    _get_analysis_with_gaps_filled,
    _get_company_info_cached,
    _get_iceberg_repo,
    _get_ohlcv_cached,
    _get_quarterly_cached,
    _get_registry_cached,
)
from dashboard.callbacks.sort_helpers import (
    apply_sort,
    build_sortable_thead,
    register_sort_callback,
)

# Module-level logger; kept at module scope as a
# private-style name per convention.
_logger = logging.getLogger(__name__)


def _apply_sector_filter(
    df: pd.DataFrame,
    sector_filter: str | None,
    repo,
) -> pd.DataFrame:
    """Filter *df* to tickers matching *sector_filter*.

    Joins the ``ticker`` column of *df* against the cached
    ``company_info`` table to resolve each ticker's sector,
    then keeps only matching rows.

    Args:
        df: DataFrame with a ``ticker`` column.
        sector_filter: Sector name, or ``"all"``/``None``
            to skip filtering.
        repo: Iceberg repo (may be ``None``).

    Returns:
        Filtered DataFrame (or original if no filter).
    """
    if not sector_filter or sector_filter == "all":
        return df
    if repo is None or df.empty:
        return df
    if "ticker" not in df.columns:
        return df
    ci = _get_company_info_cached(repo)
    if ci.empty or "sector" not in ci.columns:
        return df
    sector_map = ci.set_index("ticker")["sector"]
    mask = df["ticker"].map(sector_map) == sector_filter
    return df[mask].reset_index(drop=True)


def register(app) -> None:
    """Register Insights-page callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """

    @app.callback(
        Output("screener-table-container", "children"),
        Output("screener-count-text", "children"),
        Output("screener-pagination", "max_value"),
        Input("screener-rsi-filter", "value"),
        Input("screener-market-filter", "value"),
        Input("screener-sector-filter", "value"),
        Input("insights-tabs", "active_tab"),
        Input("screener-pagination", "active_page"),
        Input("screener-page-size", "value"),
        Input("screener-sort-store", "data"),
        State("auth-token-store", "data"),
    )
    def update_screener(
        rsi_filter: str,
        market_filter: str,
        sector_filter: str,
        active_tab: str,
        page: Optional[int],
        page_size_str: Optional[str],
        sort_state: dict | None = None,
        token: str | None = None,
    ) -> Any:
        """Populate the screener table.

        Args:
            rsi_filter: RSI filter value.
            market_filter: Market filter value.
            sector_filter: Sector filter value.
            active_tab: Currently active Insights tab ID.
            page: Current pagination page (1-based).
            page_size_str: Rows per page as string.
            sort_state: Column sort state dict.
            token: JWT access token.

        Returns:
            Tuple of (table, count text, max_value).
        """
        sort_state = sort_state or {
            "col": None,
            "dir": "none",
        }
        if active_tab != "screener-tab":
            return no_update, no_update, no_update
        repo = _get_iceberg_repo()
        df = pd.DataFrame()

        if repo is not None:
            df = _get_analysis_with_gaps_filled(repo)

        # Filter by user's linked tickers
        ut = _fetch_user_tickers(token)
        if ut is not None and not df.empty:
            df = df[df["ticker"].isin(ut)].reset_index(drop=True)

        if df.empty:
            return (
                dbc.Alert(
                    "No analysis data available."
                    " Analyse stocks via the"
                    " chat agent first.",
                    color="warning",
                    className="mt-3",
                ),
                "",
                1,
            )

        # Fix #16: vectorised market filter
        if market_filter != "all":
            if market_filter == "india":
                mask = df["ticker"].str.endswith((".NS", ".BO"))
            else:
                mask = ~df["ticker"].str.endswith((".NS", ".BO"))
            df = df[mask].reset_index(drop=True)

        # Sector filter
        df = _apply_sector_filter(df, sector_filter, repo)

        # RSI filter
        if rsi_filter != "all":
            if "rsi_14" in df.columns:
                rsi_num = pd.to_numeric(df["rsi_14"], errors="coerce")
                if rsi_filter == "oversold":
                    df = df[rsi_num.lt(30).values]
                elif rsi_filter == "overbought":
                    df = df[rsi_num.gt(70).values]
                elif rsi_filter == "neutral":
                    df = df[(rsi_num.ge(30) & rsi_num.le(70)).values]
            elif "rsi_signal" in df.columns:
                sig = df["rsi_signal"].str.lower().fillna("")
                if rsi_filter == "oversold":
                    df = df[sig.str.contains("oversold").values]
                elif rsi_filter == "overbought":
                    df = df[sig.str.contains("overbought").values]
                elif rsi_filter == "neutral":
                    df = df[sig.eq("neutral").values]

        if df.empty:
            return (
                dbc.Alert(
                    "No stocks match the selected filters.",
                    color="info",
                    className="mt-3",
                ),
                "",
                1,
            )

        # Pagination
        page_size = int(page_size_str or 10)
        page = page or 1
        total = len(df)
        max_pages = max(1, -(-total // page_size))
        page = min(page, max_pages)
        df = df.iloc[(page - 1) * page_size : page * page_size].reset_index(
            drop=True
        )
        count_text = f"{total} stock{'s' if total != 1 else ''}"

        # Build display table
        cols_map = {
            "ticker": "Ticker",
            "current_price": "Price",
            "rsi_14": "RSI (14)",
            "rsi_signal": "RSI Signal",
            "macd_signal_text": "MACD",
            "sma_200_signal": "vs SMA 200",
            "annualized_return_pct": "Ann. Return %",
            "annualized_volatility_pct": "Volatility %",
            "sharpe_ratio": "Sharpe",
        }
        display_cols = [c for c in cols_map if c in df.columns]
        display_df = df[display_cols].copy()

        # Sort before pagination (uses raw column names)
        display_df = apply_sort(display_df, sort_state)

        display_df.columns = [cols_map[c] for c in display_cols]

        for num_col in [
            "Price",
            "RSI (14)",
            "Ann. Return %",
            "Volatility %",
            "Sharpe",
        ]:
            if num_col in display_df.columns:
                display_df[num_col] = pd.to_numeric(
                    display_df[num_col], errors="coerce"
                ).round(2)

        # Fix #5: replace iterrows() with to_dict("records")
        # for faster iteration
        rows_html = []
        for record in display_df.to_dict("records"):
            cells = []
            for col, val in record.items():
                badge_class = ""
                if col == "RSI Signal":
                    badge_class = (
                        "badge bg-danger"
                        if val == "Overbought"
                        else (
                            "badge bg-success"
                            if val == "Oversold"
                            else "badge bg-secondary"
                        )
                    )
                if col == "MACD":
                    badge_class = (
                        "badge bg-success"
                        if val == "Bullish"
                        else (
                            "badge bg-danger"
                            if val == "Bearish"
                            else "badge bg-secondary"
                        )
                    )
                if col == "vs SMA 200":
                    badge_class = (
                        "badge bg-success"
                        if val == "Above"
                        else (
                            "badge bg-danger"
                            if val == "Below"
                            else "badge bg-secondary"
                        )
                    )
                if badge_class:
                    cells.append(
                        html.Td(html.Span(val, className=badge_class))
                    )
                else:
                    cells.append(html.Td(str(val) if val is not None else "—"))
            rows_html.append(html.Tr(cells))

        _tip_map = {
            "sharpe_ratio": "sharpe",
            "rsi_14": "rsi",
            "rsi_signal": "rsi",
            "macd_signal_text": "macd",
        }
        scr_col_defs = [
            {
                "key": k,
                "label": cols_map[k],
                **({"tooltip": _tip_map[k]} if k in _tip_map else {}),
            }
            for k in display_cols
        ]
        return (
            dbc.Table(
                [
                    build_sortable_thead(
                        scr_col_defs,
                        "screener",
                        sort_state,
                    ),
                    html.Tbody(rows_html),
                ],
                bordered=True,
                hover=True,
                responsive=True,
                size="sm",
                className="mt-2",
            ),
            count_text,
            max_pages,
        )

    @app.callback(
        Output("targets-table-container", "children"),
        Output("targets-count-text", "children"),
        Output("targets-pagination", "max_value"),
        Input("targets-ticker-dropdown", "value"),
        Input("targets-market-filter", "value"),
        Input("targets-sector-filter", "value"),
        Input("insights-tabs", "active_tab"),
        Input("targets-pagination", "active_page"),
        Input("targets-page-size", "value"),
        Input("targets-sort-store", "data"),
        State("auth-token-store", "data"),
    )
    def update_targets(
        ticker_filter: str,
        market_filter: str,
        sector_filter: str,
        active_tab: str,
        page: Optional[int],
        page_size_str: Optional[str],
        sort_state: dict | None = None,
        token: str | None = None,
    ) -> Any:
        """Populate the price targets table.

        Args:
            ticker_filter: Selected ticker or ``"all"``.
            market_filter: Market filter value.
            sector_filter: Sector filter value.
            active_tab: Currently active Insights tab ID.
            page: Current pagination page (1-based).
            page_size_str: Rows per page as string.
            sort_state: Column sort state dict.
            token: JWT access token.

        Returns:
            Tuple of (table, count text, max_value).
        """
        sort_state = sort_state or {
            "col": None,
            "dir": "none",
        }
        if active_tab != "targets-tab":
            return no_update, no_update, no_update

        repo = _get_iceberg_repo()
        if repo is None:
            return (
                dbc.Alert(
                    "Iceberg unavailable — cannot load price targets.",
                    color="warning",
                ),
                "",
                1,
            )

        try:
            df = repo._table_to_df("stocks.forecast_runs")
        except Exception as exc:
            return (
                dbc.Alert(
                    "Could not load forecast_runs: " + str(exc), color="danger"
                ),
                "",
                1,
            )

        if df.empty:
            return (
                dbc.Alert(
                    "No forecast data available. Use the forecast tool first.",
                    color="warning",
                    className="mt-3",
                ),
                "",
                1,
            )

        # Keep latest run per (ticker, horizon_months)
        df = (
            df.sort_values("run_date", ascending=False)
            .groupby(["ticker", "horizon_months"], as_index=False)
            .first()
        )

        # Filter by user's linked tickers
        ut = _fetch_user_tickers(token)
        if ut is not None and not df.empty:
            df = df[df["ticker"].isin(ut)].reset_index(drop=True)

        if ticker_filter and ticker_filter != "all":
            df = df[df["ticker"] == ticker_filter.upper()]

        # Fix #16: vectorised market filter
        if market_filter and market_filter != "all":
            if market_filter == "india":
                mask = df["ticker"].str.endswith((".NS", ".BO"))
            else:
                mask = ~df["ticker"].str.endswith((".NS", ".BO"))
            df = df[mask].reset_index(drop=True)

        # Sector filter
        df = _apply_sector_filter(df, sector_filter, repo)

        if df.empty:
            return (
                dbc.Alert(
                    "No forecast data for " + str(ticker_filter) + ".",
                    color="info",
                ),
                "",
                1,
            )

        # Sort before pagination
        df = apply_sort(df, sort_state)

        # Pagination
        page_size = int(page_size_str or 10)
        page = page or 1
        total = len(df)
        max_pages = max(1, -(-total // page_size))
        page = min(page, max_pages)
        df = df.iloc[(page - 1) * page_size : page * page_size].reset_index(
            drop=True
        )
        count_text = f"{total} forecast{'s' if total != 1 else ''}"

        def _target_cell(price, pct, _m_label):
            """Build a table cell for a price target with percentage change."""
            if price is None or (
                hasattr(price, "__float__") and math.isnan(float(price))
            ):
                return html.Td("—")
            sign = "+" if float(pct or 0) >= 0 else ""
            color = "text-success" if float(pct or 0) >= 0 else "text-danger"
            return html.Td(
                [
                    html.Span(f"{float(price):.2f}", className="fw-semibold"),
                    html.Br(),
                    html.Small(
                        f"{sign}{float(pct or 0):.1f}%", className=color
                    ),
                ]
            )

        # Fix #5: replace iterrows() with to_dict("records")
        # for faster iteration
        rows_html = []
        for row in df.to_dict("records"):
            sentiment = row.get("sentiment", "—") or "—"
            sentiment_badge = (
                "badge bg-success"
                if sentiment == "Bullish"
                else (
                    "badge bg-danger"
                    if sentiment == "Bearish"
                    else "badge bg-secondary"
                )
            )
            rows_html.append(
                html.Tr(
                    [
                        html.Td(html.Strong(row.get("ticker", ""))),
                        html.Td(str(row.get("horizon_months", "")) + "m"),
                        html.Td(str(row.get("run_date", "—"))),
                        html.Td(
                            f"{float(row['current_price_at_run']):.2f}"
                            if row.get("current_price_at_run")
                            else "—"
                        ),
                        _target_cell(
                            row.get("target_3m_price"),
                            row.get("target_3m_pct_change"),
                            "3m",
                        ),
                        _target_cell(
                            row.get("target_6m_price"),
                            row.get("target_6m_pct_change"),
                            "6m",
                        ),
                        _target_cell(
                            row.get("target_9m_price"),
                            row.get("target_9m_pct_change"),
                            "9m",
                        ),
                        html.Td(
                            html.Span(sentiment, className=sentiment_badge)
                        ),
                    ]
                )
            )

        tgt_col_defs = [
            {"key": "ticker", "label": "Ticker"},
            {"key": "horizon_months", "label": "Horizon"},
            {"key": "run_date", "label": "Run Date"},
            {
                "key": "current_price_at_run",
                "label": "Price at Run",
            },
            {"key": "target_3m_price", "label": "3m Target"},
            {"key": "target_6m_price", "label": "6m Target"},
            {"key": "target_9m_price", "label": "9m Target"},
            {"key": "sentiment", "label": "Sentiment"},
        ]
        return (
            dbc.Table(
                [
                    build_sortable_thead(
                        tgt_col_defs,
                        "targets",
                        sort_state,
                    ),
                    html.Tbody(rows_html),
                ],
                bordered=True,
                hover=True,
                responsive=True,
                size="sm",
                className="mt-2",
            ),
            count_text,
            max_pages,
        )

    @app.callback(
        Output("dividends-table-container", "children"),
        Output("dividends-count-text", "children"),
        Output("dividends-pagination", "max_value"),
        Input("dividends-ticker-dropdown", "value"),
        Input("dividends-market-filter", "value"),
        Input("dividends-sector-filter", "value"),
        Input("insights-tabs", "active_tab"),
        Input("dividends-pagination", "active_page"),
        Input("dividends-page-size", "value"),
        Input("dividends-sort-store", "data"),
        State("auth-token-store", "data"),
    )
    def update_dividends(
        ticker_filter: str,
        market_filter: str,
        sector_filter: str,
        active_tab: str,
        page: Optional[int],
        page_size_str: Optional[str],
        sort_state: dict | None = None,
        token: str | None = None,
    ) -> Any:
        """Populate the dividends table.

        Args:
            ticker_filter: Selected ticker or ``"all"``.
            market_filter: Market filter value.
            sector_filter: Sector filter value.
            active_tab: Currently active Insights tab ID.
            page: Current pagination page (1-based).
            page_size_str: Rows per page as string.
            sort_state: Column sort state dict.
            token: JWT access token.

        Returns:
            Tuple of (table, count text, max_value).
        """
        sort_state = sort_state or {
            "col": None,
            "dir": "none",
        }
        if active_tab != "dividends-tab":
            return no_update, no_update, no_update

        repo = _get_iceberg_repo()
        if repo is None:
            return (
                dbc.Alert(
                    "Iceberg unavailable.",
                    color="warning",
                ),
                "",
                1,
            )

        if ticker_filter and ticker_filter != "all":
            df = repo.get_dividends(ticker_filter.upper())
        else:
            df = repo._table_to_df("stocks.dividends")

        # Filter by user's linked tickers
        ut = _fetch_user_tickers(token)
        if ut is not None and not df.empty:
            df = df[df["ticker"].isin(ut)].reset_index(drop=True)

        # Vectorised market filter
        if not df.empty and market_filter and market_filter != "all":
            if market_filter == "india":
                mask = df["ticker"].str.endswith((".NS", ".BO"))
            else:
                mask = ~df["ticker"].str.endswith((".NS", ".BO"))
            df = df[mask].reset_index(drop=True)

        # Sector filter
        df = _apply_sector_filter(df, sector_filter, repo)

        if df.empty:
            return (
                dbc.Alert(
                    "No dividend data available. Use the dividend tool first.",
                    color="warning",
                    className="mt-3",
                ),
                "",
                1,
            )

        # Default sort: most-recent first; override by header
        if sort_state.get("col") and sort_state["dir"] != "none":
            df = apply_sort(df, sort_state)
        else:
            df = df.sort_values("ex_date", ascending=False).reset_index(
                drop=True
            )

        # Pagination
        page_size = int(page_size_str or 10)
        page = page or 1
        total = len(df)
        max_pages = max(1, -(-total // page_size))
        page = min(page, max_pages)
        page_df = df.iloc[(page - 1) * page_size : page * page_size]
        count_text = f"{total} payment{'s' if total != 1 else ''}"

        sym_map = {
            "USD": "$",
            "INR": "₹",
            "GBP": "£",
            "EUR": "€",
            "JPY": "¥",
            "CNY": "¥",
            "AUD": "A$",
            "CAD": "CA$",
        }

        # Fix #5: replace iterrows() with to_dict("records")
        # for faster iteration
        rows_html = []
        for row in page_df.to_dict("records"):
            currency = str(row.get("currency", "USD") or "USD")
            sym = sym_map.get(currency.upper(), currency)
            amount = row.get("dividend_amount")
            amount_str = f"{sym}{float(amount):.4f}" if amount else "—"
            rows_html.append(
                html.Tr(
                    [
                        html.Td(html.Strong(str(row.get("ticker", "")))),
                        html.Td(str(row.get("ex_date", "—"))),
                        html.Td(amount_str),
                        html.Td(currency),
                    ]
                )
            )

        div_col_defs = [
            {"key": "ticker", "label": "Ticker"},
            {"key": "ex_date", "label": "Ex-Date"},
            {
                "key": "dividend_amount",
                "label": "Amount",
            },
            {"key": "currency", "label": "Currency"},
        ]
        return (
            dbc.Table(
                [
                    build_sortable_thead(
                        div_col_defs,
                        "dividends",
                        sort_state,
                    ),
                    html.Tbody(rows_html),
                ],
                bordered=True,
                hover=True,
                responsive=True,
                size="sm",
                className="mt-2",
            ),
            count_text,
            max_pages,
        )

    @app.callback(
        Output("risk-table-container", "children"),
        Output("risk-count-text", "children"),
        Output("risk-pagination", "max_value"),
        Input("risk-market-filter", "value"),
        Input("risk-sector-filter", "value"),
        Input("insights-tabs", "active_tab"),
        Input("risk-pagination", "active_page"),
        Input("risk-page-size", "value"),
        Input("risk-sort-store", "data"),
        State("auth-token-store", "data"),
    )
    def update_risk(
        market_filter: str,
        sector_filter: str,
        active_tab: str,
        page: Optional[int],
        page_size_str: Optional[str],
        sort_state: dict | None = None,
        token: str | None = None,
    ) -> Any:
        """Populate the risk metrics table.

        Args:
            market_filter: Market filter value.
            sector_filter: Sector filter value.
            active_tab: Currently active Insights tab ID.
            page: Current pagination page (1-based).
            page_size_str: Rows per page as string.
            sort_state: Column sort state dict.
            token: JWT access token.

        Returns:
            Tuple of (table, count text, max_value).
        """
        sort_state = sort_state or {
            "col": None,
            "dir": "none",
        }
        if active_tab != "risk-tab":
            return no_update, no_update, no_update

        repo = _get_iceberg_repo()
        df = pd.DataFrame()
        if repo is not None:
            df = _get_analysis_with_gaps_filled(repo)

        # Filter by user's linked tickers
        ut = _fetch_user_tickers(token)
        if ut is not None and not df.empty:
            df = df[df["ticker"].isin(ut)].reset_index(drop=True)

        # Vectorised market filter
        if not df.empty and market_filter and market_filter != "all":
            if market_filter == "india":
                mask = df["ticker"].str.endswith((".NS", ".BO"))
            else:
                mask = ~df["ticker"].str.endswith((".NS", ".BO"))
            df = df[mask].reset_index(drop=True)

        # Sector filter
        df = _apply_sector_filter(df, sector_filter, repo)

        if df.empty:
            return (
                dbc.Alert(
                    "No risk data available. Analyse stocks first.",
                    color="warning",
                    className="mt-3",
                ),
                "",
                1,
            )

        display_cols = [
            "ticker",
            "annualized_return_pct",
            "annualized_volatility_pct",
            "sharpe_ratio",
            "max_drawdown_pct",
            "max_drawdown_duration_days",
            "bull_phase_pct",
            "bear_phase_pct",
        ]
        display_cols = [c for c in display_cols if c in df.columns]
        display_df = df[display_cols].copy()

        # Sort via header-click state
        display_df = apply_sort(display_df, sort_state)

        col_labels = {
            "ticker": "Ticker",
            "annualized_return_pct": "Ann. Return %",
            "annualized_volatility_pct": "Volatility %",
            "sharpe_ratio": "Sharpe",
            "max_drawdown_pct": "Max DD %",
            "max_drawdown_duration_days": "Max DD Days",
            "bull_phase_pct": "Bull %",
            "bear_phase_pct": "Bear %",
        }

        # Pagination (apply before renaming columns)
        page_size = int(page_size_str or 10)
        page = page or 1
        total = len(display_df)
        max_pages = max(1, -(-total // page_size))
        page = min(page, max_pages)
        display_df = display_df.iloc[(page - 1) * page_size : page * page_size]
        count_text = f"{total} stock{'s' if total != 1 else ''}"

        display_df.columns = [col_labels.get(c, c) for c in display_df.columns]

        for num_col in [
            "Ann. Return %",
            "Volatility %",
            "Sharpe",
            "Max DD %",
            "Bull %",
            "Bear %",
        ]:
            if num_col in display_df.columns:
                display_df[num_col] = pd.to_numeric(
                    display_df[num_col], errors="coerce"
                ).round(2)

        # Fix #5: replace iterrows() with to_dict("records")
        # for faster iteration
        rows_html = [
            html.Tr(
                [
                    html.Td(str(v) if v is not None else "—")
                    for v in row.values()
                ]
            )
            for row in display_df.to_dict("records")
        ]

        risk_col_defs = [
            {
                "key": k,
                "label": col_labels[k],
                **({"tooltip": "sharpe"} if k == "sharpe_ratio" else {}),
            }
            for k in display_cols
        ]
        return (
            dbc.Table(
                [
                    build_sortable_thead(
                        risk_col_defs,
                        "risk",
                        sort_state,
                    ),
                    html.Tbody(rows_html),
                ],
                bordered=True,
                hover=True,
                responsive=True,
                size="sm",
                className="mt-2",
            ),
            count_text,
            max_pages,
        )

    @app.callback(
        Output("sectors-bar-chart", "figure"),
        Output("sectors-table-container", "children"),
        Input("insights-tabs", "active_tab"),
        Input("sectors-market-filter", "value"),
        Input("sectors-sort-store", "data"),
        State("auth-token-store", "data"),
    )
    def update_sectors(
        active_tab: str,
        market_filter: str | None = None,
        sort_state: dict | None = None,
        token: str | None = None,
    ) -> tuple:
        """Populate the sector analysis chart and table.

        Joins ``stocks.company_info`` (for sector names) with
        ``stocks.analysis_summary`` (for performance).

        Args:
            active_tab: Currently active Insights tab ID.
            market_filter: ``"all"``, ``"india"``, or ``"us"``.
            sort_state: Column sort state dict.
            token: JWT access token.

        Returns:
            Tuple of (Plotly figure, table component).
        """
        sort_state = sort_state or {
            "col": None,
            "dir": "none",
        }
        if active_tab != "sectors-tab":
            return go.Figure(), html.Div()

        repo = _get_iceberg_repo()
        empty_fig = go.Figure().update_layout(
            template="plotly_white",
            title="No sector data available",
            paper_bgcolor="#f9fafb",
        )

        if repo is None:
            return empty_fig, dbc.Alert(
                "Iceberg unavailable.", color="warning"
            )

        company_df = _get_company_info_cached(repo)
        analysis_df = _get_analysis_with_gaps_filled(repo)

        if company_df.empty or analysis_df.empty:
            return empty_fig, dbc.Alert(
                "No sector data available. " "Run backfill first.",
                color="warning",
                className="mt-3",
            )

        # Join on ticker
        merged = company_df[["ticker", "sector"]].merge(
            analysis_df[
                [
                    "ticker",
                    "annualized_return_pct",
                    "sharpe_ratio",
                    "annualized_volatility_pct",
                ]
            ],
            on="ticker",
            how="inner",
        )
        merged = merged[merged["sector"].notna() & (merged["sector"] != "N/A")]

        # Filter by user's linked tickers
        ut = _fetch_user_tickers(token)
        if ut is not None and not merged.empty:
            merged = merged[merged["ticker"].isin(ut)].reset_index(drop=True)

        # Market filter (before groupby)
        if market_filter and market_filter != "all":
            if market_filter == "india":
                mask = merged["ticker"].str.endswith((".NS", ".BO"))
            else:
                mask = ~merged["ticker"].str.endswith((".NS", ".BO"))
            merged = merged[mask].reset_index(drop=True)

        if merged.empty:
            return empty_fig, dbc.Alert(
                "No sector metadata found.", color="info"
            )

        sector_agg = (
            merged.groupby("sector")
            .agg(
                count=("ticker", "count"),
                avg_return=(
                    "annualized_return_pct",
                    "mean",
                ),
                avg_sharpe=("sharpe_ratio", "mean"),
                avg_vol=(
                    "annualized_volatility_pct",
                    "mean",
                ),
            )
            .reset_index()
            .sort_values("avg_return", ascending=False)
        )

        # Apply header-click sorting
        sector_agg = apply_sort(sector_agg, sort_state)

        # Bar chart
        colors = [
            "#4caf50" if r >= 0 else "#ef5350"
            for r in sector_agg["avg_return"]
        ]
        fig = go.Figure(
            go.Bar(
                x=sector_agg["sector"],
                y=sector_agg["avg_return"].round(2),
                marker_color=colors,
                text=(sector_agg["avg_return"].round(1).astype(str) + "%"),
                textposition="outside",
                textfont=dict(size=11),
            )
        )
        fig.update_layout(
            template="plotly_white",
            title="Average Annualised Return by Sector",
            yaxis_title="Avg Ann. Return %",
            paper_bgcolor="#f9fafb",
            plot_bgcolor="#ffffff",
            height=320,
            margin=dict(l=40, r=20, t=50, b=80),
        )
        fig.update_xaxes(tickangle=-30)

        # Summary table — manual thead + tbody
        sectors_col_defs = [
            {"key": "sector", "label": "Sector"},
            {"key": "count", "label": "Stocks"},
            {"key": "avg_return", "label": "Avg Return %"},
            {
                "key": "avg_sharpe",
                "label": "Avg Sharpe",
                "tooltip": "sharpe",
            },
            {"key": "avg_vol", "label": "Avg Vol %"},
        ]

        for num_col in (
            "avg_return",
            "avg_sharpe",
            "avg_vol",
        ):
            sector_agg[num_col] = pd.to_numeric(
                sector_agg[num_col], errors="coerce"
            ).round(2)

        rows_html = []
        for rec in sector_agg.to_dict("records"):
            rows_html.append(
                html.Tr(
                    [
                        html.Td(
                            str(rec.get(c["key"], ""))
                            if rec.get(c["key"]) is not None
                            else "\u2014"
                        )
                        for c in sectors_col_defs
                    ]
                )
            )

        table = dbc.Table(
            [
                build_sortable_thead(
                    sectors_col_defs,
                    "sectors",
                    sort_state,
                ),
                html.Tbody(rows_html),
            ],
            bordered=True,
            hover=True,
            responsive=True,
            size="sm",
            className="mt-2",
        )

        return fig, table

    @app.callback(
        Output("correlation-heatmap", "figure"),
        Input("corr-period-filter", "value"),
        Input("corr-market-filter", "value"),
        Input("corr-sector-filter", "value"),
        State("auth-token-store", "data"),
    )
    def update_correlation(
        period: str,
        market_filter: str | None = None,
        sector_filter: str | None = None,
        token: str | None = None,
    ) -> go.Figure:
        """Build the returns correlation heatmap.

        Args:
            period: Lookback period (``"1y"``, ``"3y"``,
                or ``"all"``).
            market_filter: ``"all"``, ``"india"``, or
                ``"us"``.
            sector_filter: Sector name or ``"all"``.

        Returns:
            Plotly heatmap figure.
        """
        empty_fig = go.Figure().update_layout(
            template="plotly_white",
            title="No OHLCV data available",
            paper_bgcolor="#f9fafb",
        )

        repo = _get_iceberg_repo()

        # Resolve sector → ticker set for filtering
        _sector_tickers = None
        if sector_filter and sector_filter != "all" and repo is not None:
            ci = _get_company_info_cached(repo)
            if not ci.empty and "sector" in ci.columns:
                _sector_tickers = set(
                    ci.loc[
                        ci["sector"] == sector_filter,
                        "ticker",
                    ]
                )

        close_data = {}

        if repo is not None:
            df_all = repo._table_to_df("stocks.ohlcv")
            if not df_all.empty:
                # Market filter on OHLCV
                if market_filter and market_filter != "all":
                    if market_filter == "india":
                        df_all = df_all[
                            df_all["ticker"].str.endswith((".NS", ".BO"))
                        ]
                    else:
                        df_all = df_all[
                            ~df_all["ticker"].str.endswith((".NS", ".BO"))
                        ]
                # Sector pre-filter on OHLCV
                if _sector_tickers is not None:
                    df_all = df_all[df_all["ticker"].isin(_sector_tickers)]
                # Fix #7: push date cutoff before per-ticker Python iteration.
                # Convert "date" column to datetime64 first — the Iceberg
                # date32 type becomes Python datetime.date objects in pandas,
                # which cannot be compared directly with strings (TypeError).
                if period != "all":
                    from datetime import datetime, timedelta  # noqa: PLC0415

                    _days = 365 if period == "1y" else 3 * 365
                    _cutoff_ts = pd.Timestamp(
                        datetime.today() - timedelta(days=_days)
                    )
                    df_all["date"] = pd.to_datetime(df_all["date"])
                    df_all = df_all[df_all["date"] >= _cutoff_ts]
                for ticker in df_all["ticker"].unique():
                    sub = df_all[df_all["ticker"] == ticker].copy()
                    sub["date"] = pd.to_datetime(sub["date"])
                    sub = sub.sort_values("date").set_index("date")
                    close_data[ticker] = sub["close"].dropna()

        # Fallback: per-ticker using cached helper
        if not close_data:
            try:
                fb_repo = _get_iceberg_repo()
                if fb_repo is not None:
                    reg = fb_repo.get_all_registry()
                    tks = sorted(reg.keys())
                    # Market filter on fallback
                    if market_filter and market_filter != "all":
                        if market_filter == "india":
                            tks = [
                                t for t in tks if t.endswith((".NS", ".BO"))
                            ]
                        else:
                            tks = [
                                t
                                for t in tks
                                if not t.endswith((".NS", ".BO"))
                            ]
                    if _sector_tickers is not None:
                        tks = [t for t in tks if t in _sector_tickers]
                    for ticker in tks:
                        ohlcv = _get_ohlcv_cached(fb_repo, ticker)
                        if ohlcv is not None and not ohlcv.empty:
                            close_data[ticker] = ohlcv["Close"].dropna()
            except Exception as _e:
                _logger.warning(
                    "Correlation fallback failed: %s",
                    _e,
                )

        if not close_data:
            return empty_fig

        # Filter by user's linked tickers
        ut = _fetch_user_tickers(token)
        if ut is not None:
            close_data = {k: v for k, v in close_data.items() if k in ut}
        if not close_data:
            return empty_fig

        # Apply period filter
        cutoff = None
        if period == "1y":
            cutoff = pd.Timestamp.now() - pd.DateOffset(years=1)
        elif period == "3y":
            cutoff = pd.Timestamp.now() - pd.DateOffset(years=3)

        daily_returns = {}
        for ticker, prices in close_data.items():
            if cutoff is not None:
                prices = prices[prices.index >= cutoff]
            if len(prices) > 10:
                daily_returns[ticker] = prices.pct_change().dropna()

        if len(daily_returns) < 2:
            return empty_fig

        ret_df = pd.DataFrame(daily_returns).dropna(how="all")
        corr = ret_df.corr().round(3)
        tickers_sorted = sorted(corr.columns)
        corr = corr.loc[tickers_sorted, tickers_sorted]

        z = corr.values.tolist()
        text = [[f"{v:.2f}" for v in row] for row in z]

        fig = go.Figure(
            go.Heatmap(
                z=z,
                x=tickers_sorted,
                y=tickers_sorted,
                text=text,
                texttemplate="%{text}",
                colorscale="RdBu",
                zmid=0,
                zmin=-1,
                zmax=1,
                colorbar=dict(title="Correlation"),
            )
        )
        fig.update_layout(
            template="plotly_white",
            title=f"Daily Returns Correlation ({period.upper()} lookback)",
            paper_bgcolor="#f9fafb",
            plot_bgcolor="#ffffff",
            height=580,
            margin=dict(l=80, r=30, t=60, b=80),
            xaxis=dict(tickangle=-45),
        )

        return fig

    # ── Quarterly Results ───────────────────────────────────

    @app.callback(
        Output("quarterly-chart", "figure"),
        Output("quarterly-table-container", "children"),
        Output("quarterly-count-text", "children"),
        Output("quarterly-pagination", "max_value"),
        Input("insights-tabs", "active_tab"),
        Input("quarterly-ticker-filter", "value"),
        Input("quarterly-sector-filter", "value"),
        Input("quarterly-market-filter", "value"),
        Input("quarterly-statement-filter", "value"),
        Input("quarterly-pagination", "active_page"),
        Input("quarterly-page-size", "value"),
        Input("quarterly-sort-store", "data"),
        State("auth-token-store", "data"),
    )
    def update_quarterly(
        active_tab: str,
        ticker_filter: str | None,
        sector_filter: str | None,
        market_filter: str | None,
        stmt_filter: str | None,
        page: int | None = None,
        page_size_str: str | None = None,
        sort_state: dict | None = None,
        token: str | None = None,
    ) -> tuple:
        """Populate the quarterly results chart and table.

        Args:
            active_tab: Currently active tab ID.
            ticker_filter: Selected ticker or ``"all"``.
            sector_filter: Sector or ``"all"``.
            market_filter: ``"all"``/``"india"``/``"us"``.
            stmt_filter: Statement type filter.
            page: Current pagination page (1-based).
            page_size_str: Rows per page as string.
            sort_state: Column sort state dict.
            token: JWT access token.

        Returns:
            Tuple of (figure, table, count text,
            max pages).
        """
        sort_state = sort_state or {
            "col": None,
            "dir": "none",
        }
        _empty_fig = go.Figure()
        _empty_fig.update_layout(
            template="plotly_white",
            paper_bgcolor="#f9fafb",
            plot_bgcolor="#ffffff",
            height=360,
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            annotations=[
                dict(
                    text="No data to display",
                    xref="paper",
                    yref="paper",
                    x=0.5,
                    y=0.5,
                    showarrow=False,
                    font=dict(size=16, color="#9ca3af"),
                )
            ],
        )
        if active_tab != "quarterly-tab":
            return (
                _empty_fig,
                html.Div(),
                "",
                1,
            )

        repo = _get_iceberg_repo()
        if repo is None:
            return (
                _empty_fig,
                dbc.Alert(
                    "Iceberg unavailable.",
                    color="warning",
                    className="text-center mt-3",
                ),
                "",
                1,
            )

        df = _get_quarterly_cached(repo)
        if df.empty:
            return (
                _empty_fig,
                dbc.Alert(
                    "No quarterly data available. "
                    "Use fetch_quarterly_results"
                    " first.",
                    color="warning",
                    className="text-center mt-3",
                ),
                "",
                1,
            )

        # Filter by user's linked tickers
        ut = _fetch_user_tickers(token)
        if ut is not None and not df.empty:
            df = df[df["ticker"].isin(ut)].reset_index(drop=True)

        # Market filter
        if market_filter and market_filter != "all":
            if market_filter == "india":
                mask = df["ticker"].str.endswith((".NS", ".BO"))
            else:
                mask = ~df["ticker"].str.endswith((".NS", ".BO"))
            df = df[mask].reset_index(drop=True)

        # Sector filter
        df = _apply_sector_filter(df, sector_filter, repo)

        # Ticker filter
        if ticker_filter and ticker_filter != "all":
            df = df[df["ticker"] == ticker_filter.upper()].reset_index(
                drop=True
            )

        # Statement type filter
        if (
            stmt_filter
            and stmt_filter != "all"
            and "statement_type" in df.columns
        ):
            df = df[df["statement_type"] == stmt_filter].reset_index(drop=True)

        if df.empty:
            _stmt_labels = {
                "income": "income statement",
                "balance": "balance sheet",
                "cashflow": "cash flow",
            }
            _lbl = _stmt_labels.get(stmt_filter, "")
            _ticker_part = (
                f" for {ticker_filter.upper()}"
                if ticker_filter and ticker_filter != "all"
                else ""
            )
            return (
                _empty_fig,
                dbc.Alert(
                    f"No {_lbl} data"
                    f" available{_ticker_part}. "
                    "Try refreshing the stock or"
                    " selecting a different filter.",
                    color="info",
                    className="text-center mt-3",
                ),
                "",
                1,
            )

        # Build a display column: "Q4 FY25" or "FY25"
        if "fiscal_quarter" in df.columns and "fiscal_year" in df.columns:
            fy_suffix = "FY" + df["fiscal_year"].astype(str).str[-2:]
            df["quarter_label"] = df.apply(
                lambda r: (
                    fy_suffix[r.name]
                    if r["fiscal_quarter"] == "FY"
                    else f"{r['fiscal_quarter']} " f"{fy_suffix[r.name]}"
                ),
                axis=1,
            )
        else:
            df["quarter_label"] = df.get("fiscal_quarter", "")

        # Sort
        df = apply_sort(df, sort_state)

        # ── QoQ Bar Chart ──────────────────────────
        # Pick chart rows by statement type
        _chart_stmt = stmt_filter or "all"
        if _chart_stmt in ("income", "all") and "statement_type" in df.columns:
            chart_df = df[df["statement_type"] == "income"].copy()
        elif _chart_stmt == "balance" and "statement_type" in df.columns:
            chart_df = df[df["statement_type"] == "balance"].copy()
        elif _chart_stmt == "cashflow" and "statement_type" in df.columns:
            chart_df = df[df["statement_type"] == "cashflow"].copy()
        else:
            chart_df = df.copy()

        if chart_df.empty:
            chart_df = df.copy()
        chart_df = chart_df.sort_values("quarter_end")
        # Keep latest per (ticker, quarter_end)
        chart_df = chart_df.drop_duplicates(
            subset=["ticker", "quarter_end"],
            keep="last",
        )
        # Label: TICKER Q1 FY25
        chart_df["q_label"] = (
            chart_df["ticker"] + " " + chart_df["quarter_label"]
        )

        # Determine scale
        _is_indian = chart_df["ticker"].str.endswith((".NS", ".BO")).any()
        if _is_indian:
            _div = 1e7  # 1 Cr
            _suffix = " Cr"
        else:
            _div = 1e6
            _suffix = " M"

        # Chart metric pairs per statement type
        if _chart_stmt == "balance":
            _chart_pairs = [
                ("total_assets", "Total Assets"),
                ("total_equity", "Equity"),
            ]
        elif _chart_stmt == "cashflow":
            _chart_pairs = [
                ("operating_cashflow", "Op. Cashflow"),
                ("free_cashflow", "Free CF"),
            ]
        else:
            _chart_pairs = [
                ("revenue", "Revenue"),
                ("net_income", "Net Income"),
            ]

        traces = []
        for col_key, col_label in _chart_pairs:
            if col_key in chart_df.columns:
                vals = pd.to_numeric(chart_df[col_key], errors="coerce")
                if vals.notna().any():
                    traces.append(
                        go.Bar(
                            x=chart_df["q_label"],
                            y=(vals / _div).round(1),
                            name=col_label + _suffix,
                        )
                    )

        if traces:
            fig = go.Figure(data=traces)
            fig.update_layout(
                template="plotly_white",
                title="Quarter-over-Quarter Results",
                barmode="group",
                paper_bgcolor="#f9fafb",
                plot_bgcolor="#ffffff",
                height=360,
                margin=dict(l=40, r=20, t=50, b=80),
            )
            fig.update_xaxes(tickangle=-30)
        else:
            fig = _empty_fig

        # ── Table ──────────────────────────────────
        # Column definitions per statement type
        _base_cols = [
            {"key": "ticker", "label": "Ticker"},
            {
                "key": "quarter_label",
                "label": "Quarter",
            },
        ]
        _income_cols = [
            {"key": "revenue", "label": "Revenue"},
            {
                "key": "net_income",
                "label": "Net Income",
            },
            {
                "key": "gross_profit",
                "label": "Gross Profit",
            },
            {
                "key": "operating_income",
                "label": "Op. Income",
            },
            {"key": "ebitda", "label": "EBITDA"},
            {"key": "eps_diluted", "label": "EPS"},
        ]
        _balance_cols = [
            {
                "key": "total_assets",
                "label": "Total Assets",
            },
            {
                "key": "total_liabilities",
                "label": "Total Liab.",
            },
            {
                "key": "total_equity",
                "label": "Equity",
            },
            {
                "key": "total_debt",
                "label": "Total Debt",
            },
            {
                "key": "cash_and_equivalents",
                "label": "Cash & Equiv.",
            },
        ]
        _cashflow_cols = [
            {
                "key": "operating_cashflow",
                "label": "Op. Cashflow",
            },
            {"key": "capex", "label": "Capex"},
            {
                "key": "free_cashflow",
                "label": "Free CF",
            },
        ]

        # Pick columns based on active filter
        if stmt_filter == "income":
            metric_defs = _income_cols
        elif stmt_filter == "balance":
            metric_defs = _balance_cols
        elif stmt_filter == "cashflow":
            metric_defs = _cashflow_cols
        else:
            # Fallback — show all statement columns
            metric_defs = (
                [
                    {
                        "key": "statement_type",
                        "label": "Statement",
                    },
                ]
                + _income_cols
                + _balance_cols
                + _cashflow_cols
            )

        q_col_defs = _base_cols + metric_defs

        avail_cols = [
            c for c in [d["key"] for d in q_col_defs] if c in df.columns
        ]
        show_df = df[avail_cols].copy()

        # Format large numbers with scale
        _scale_cols = [
            "revenue",
            "net_income",
            "gross_profit",
            "operating_income",
            "ebitda",
            "total_assets",
            "total_liabilities",
            "total_equity",
            "total_debt",
            "cash_and_equivalents",
            "operating_cashflow",
            "capex",
            "free_cashflow",
        ]
        for nc in _scale_cols:
            if nc in show_df.columns:
                vals = pd.to_numeric(show_df[nc], errors="coerce")
                show_df[nc] = (vals / _div).round(1)

        if "eps_diluted" in show_df.columns:
            show_df["eps_diluted"] = pd.to_numeric(
                show_df["eps_diluted"],
                errors="coerce",
            ).round(2)

        # Drop rows missing the primary metric for the type
        _primary = {
            "income": "revenue",
            "balance": "total_assets",
            "cashflow": "operating_cashflow",
        }
        _prim_col = _primary.get(stmt_filter)
        if _prim_col and _prim_col in show_df.columns:
            show_df = show_df.dropna(subset=[_prim_col])

        _num_keys = set(_scale_cols) | {"eps_diluted"}

        # Pagination
        page_size = int(page_size_str or 10)
        page = page or 1
        total = len(show_df)
        max_pages = max(1, -(-total // page_size))
        page = min(page, max_pages)
        show_df = show_df.iloc[
            (page - 1) * page_size : page * page_size
        ].reset_index(drop=True)
        count_text = f"{total} row{'s' if total != 1 else ''}"

        rows_html = []
        for rec in show_df.to_dict("records"):
            cells = []
            for c in q_col_defs:
                key = c["key"]
                if key not in avail_cols:
                    continue
                val = rec.get(key)
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    cells.append(html.Td("\u2014"))
                elif key in _num_keys and isinstance(val, (int, float)):
                    cells.append(html.Td(f"{val:,.2f}"))
                else:
                    cells.append(html.Td(str(val)))
            rows_html.append(html.Tr(cells))

        filtered_defs = [d for d in q_col_defs if d["key"] in avail_cols]
        table = dbc.Table(
            [
                build_sortable_thead(
                    filtered_defs,
                    "quarterly",
                    sort_state,
                ),
                html.Tbody(rows_html),
            ],
            bordered=True,
            hover=True,
            responsive=True,
            size="sm",
            className="mt-2",
        )

        return fig, table, count_text, max_pages

    # ── Sort-header callbacks + pagination resets ──────────
    for _tid in (
        "screener",
        "targets",
        "dividends",
        "risk",
        "sectors",
        "quarterly",
    ):
        register_sort_callback(app, _tid)

    @app.callback(
        Output("screener-pagination", "active_page"),
        Input("screener-sort-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_screener_page_on_sort(_s):
        """Reset screener to page 1 on sort change."""
        return 1

    @app.callback(
        Output("targets-pagination", "active_page"),
        Input("targets-sort-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_targets_page_on_sort(_s):
        """Reset targets to page 1 on sort change."""
        return 1

    @app.callback(
        Output("dividends-pagination", "active_page"),
        Input("dividends-sort-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_dividends_page_on_sort(_s):
        """Reset dividends to page 1 on sort change."""
        return 1

    @app.callback(
        Output("risk-pagination", "active_page"),
        Input("risk-sort-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_risk_page_on_sort(_s):
        """Reset risk to page 1 on sort change."""
        return 1

    @app.callback(
        Output("quarterly-pagination", "active_page"),
        Input("quarterly-sort-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_quarterly_page_on_sort(_s):
        """Reset quarterly to page 1 on sort change."""
        return 1

    # ── Filter insight dropdowns by user tickers ──────
    @app.callback(
        Output("targets-ticker-dropdown", "options"),
        Output("dividends-ticker-dropdown", "options"),
        Output("quarterly-ticker-filter", "options"),
        Input("insights-tabs", "active_tab"),
        State("auth-token-store", "data"),
    )
    def filter_insights_dropdowns(active_tab, token):
        """Update ticker dropdowns on insights page.

        Adds an ``"all"`` option plus the user's linked
        tickers so that only watchlist tickers appear.

        Args:
            active_tab: Currently active Insights tab.
            token: JWT access token.

        Returns:
            Tuple of (targets options, dividends options,
            quarterly options).
        """
        repo = _get_iceberg_repo()
        if repo is not None:
            reg = _get_registry_cached(repo)
            all_tickers = sorted(reg.keys())
        else:
            all_tickers = []

        ut = _fetch_user_tickers(token)
        if ut is not None:
            tickers = [t for t in all_tickers if t in ut]
        else:
            tickers = all_tickers

        opts = [{"label": "All", "value": "all"}] + [
            {"label": t, "value": t} for t in tickers
        ]
        return opts, opts, opts
