"""Insights-page Dash callbacks for the AI Stock Analysis Dashboard.

Registers callbacks for the Screener, Price Targets, Dividends, Risk Metrics,
Sectors, and Correlation tabs on the ``/insights`` page.  All tabs read from
Iceberg tables via the lazy-singleton ``_get_iceberg_repo()``, with flat-file
fallbacks where available.

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
from dash import Input, Output, html, no_update

from dashboard.callbacks.iceberg import (
    _get_analysis_summary_cached,
    _get_analysis_with_gaps_filled,
    _get_company_info_cached,
    _get_iceberg_repo,
    _get_ohlcv_cached,
)

# Module-level logger; kept at module scope as a private-style name per convention.
_logger = logging.getLogger(__name__)


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
        Input("insights-tabs", "active_tab"),
        Input("screener-pagination", "active_page"),
        Input("screener-page-size", "value"),
    )
    def update_screener(
        rsi_filter: str,
        market_filter: str,
        active_tab: str,
        page: Optional[int],
        page_size_str: Optional[str],
    ) -> Any:
        """Populate the screener table from stocks.analysis_summary.

        Args:
            rsi_filter: RSI filter value (``"all"``, ``"oversold"``, etc.).
            market_filter: Market filter value (``"all"``, ``"india"``, ``"us"``).
            active_tab: Currently active Insights tab ID.
            page: Current pagination page (1-based).
            page_size_str: Number of rows per page as string.

        Returns:
            Tuple of (table component, count text, pagination max_value).
        """
        if active_tab != "screener-tab":
            return no_update, no_update, no_update
        repo = _get_iceberg_repo()
        df = pd.DataFrame()

        if repo is not None:
            df = _get_analysis_with_gaps_filled(repo)

        if df.empty:
            return (
                dbc.Alert(
                    "No analysis data available. Analyse stocks via the chat agent first.",
                    color="warning",
                    className="mt-3",
                ),
                "",
                1,
            )

        # Fix #16: vectorised market filter with .str.endswith()
        if market_filter != "all":
            if market_filter == "india":
                mask = df["ticker"].str.endswith((".NS", ".BO"))
            else:
                mask = ~df["ticker"].str.endswith((".NS", ".BO"))
            df = df[mask].reset_index(drop=True)

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

        # Fix #5: replace iterrows() with to_dict("records") for faster iteration
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

        return (
            dbc.Table(
                [
                    html.Thead(
                        html.Tr([html.Th(c) for c in display_df.columns])
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
        Input("insights-tabs", "active_tab"),
        Input("targets-pagination", "active_page"),
        Input("targets-page-size", "value"),
    )
    def update_targets(
        ticker_filter: str,
        market_filter: str,
        active_tab: str,
        page: Optional[int],
        page_size_str: Optional[str],
    ) -> Any:
        """Populate the price targets table from stocks.forecast_runs.

        Args:
            ticker_filter: Selected ticker or ``"all"``.
            market_filter: Market filter value (``"all"``, ``"india"``, ``"us"``).
            active_tab: Currently active Insights tab ID.
            page: Current pagination page (1-based).
            page_size_str: Number of rows per page as string.

        Returns:
            Tuple of (table component, count text, pagination max_value).
        """
        if active_tab != "targets-tab":
            return no_update, no_update, no_update

        # Fix #13: use _get_iceberg_repo() instead of raw load_catalog() call
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

        if ticker_filter and ticker_filter != "all":
            df = df[df["ticker"] == ticker_filter.upper()]

        # Fix #16: vectorised market filter
        if market_filter and market_filter != "all":
            if market_filter == "india":
                mask = df["ticker"].str.endswith((".NS", ".BO"))
            else:
                mask = ~df["ticker"].str.endswith((".NS", ".BO"))
            df = df[mask].reset_index(drop=True)

        if df.empty:
            return (
                dbc.Alert(
                    "No forecast data for " + str(ticker_filter) + ".",
                    color="info",
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

        # Fix #5: replace iterrows() with to_dict("records") for faster iteration
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

        return (
            dbc.Table(
                [
                    html.Thead(
                        html.Tr(
                            [
                                html.Th("Ticker"),
                                html.Th("Horizon"),
                                html.Th("Run Date"),
                                html.Th("Price at Run"),
                                html.Th("3m Target"),
                                html.Th("6m Target"),
                                html.Th("9m Target"),
                                html.Th("Sentiment"),
                            ]
                        )
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
        Input("insights-tabs", "active_tab"),
        Input("dividends-pagination", "active_page"),
        Input("dividends-page-size", "value"),
    )
    def update_dividends(
        ticker_filter: str,
        market_filter: str,
        active_tab: str,
        page: Optional[int],
        page_size_str: Optional[str],
    ) -> Any:
        """Populate the dividends table from stocks.dividends.

        Args:
            ticker_filter: Selected ticker or ``"all"``.
            market_filter: Market filter value (``"all"``, ``"india"``, ``"us"``).
            active_tab: Currently active Insights tab ID.
            page: Current pagination page (1-based).
            page_size_str: Number of rows per page as string.

        Returns:
            Tuple of (table component, count text, pagination max_value).
        """
        if active_tab != "dividends-tab":
            return no_update, no_update, no_update

        repo = _get_iceberg_repo()
        if repo is None:
            return dbc.Alert("Iceberg unavailable.", color="warning"), "", 1

        if ticker_filter and ticker_filter != "all":
            df = repo.get_dividends(ticker_filter.upper())
        else:
            df = repo._table_to_df("stocks.dividends")

        # Fix #16: vectorised market filter
        if not df.empty and market_filter and market_filter != "all":
            if market_filter == "india":
                mask = df["ticker"].str.endswith((".NS", ".BO"))
            else:
                mask = ~df["ticker"].str.endswith((".NS", ".BO"))
            df = df[mask].reset_index(drop=True)

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

        # Sort most-recent first
        df = df.sort_values("ex_date", ascending=False).reset_index(drop=True)

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

        # Fix #5: replace iterrows() with to_dict("records") for faster iteration
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

        return (
            dbc.Table(
                [
                    html.Thead(
                        html.Tr(
                            [
                                html.Th("Ticker"),
                                html.Th("Ex-Date"),
                                html.Th("Amount"),
                                html.Th("Currency"),
                            ]
                        )
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
        Input("risk-sort-by", "value"),
        Input("risk-market-filter", "value"),
        Input("insights-tabs", "active_tab"),
        Input("risk-pagination", "active_page"),
        Input("risk-page-size", "value"),
    )
    def update_risk(
        sort_col: str,
        market_filter: str,
        active_tab: str,
        page: Optional[int],
        page_size_str: Optional[str],
    ) -> Any:
        """Populate the risk metrics table from stocks.analysis_summary.

        Args:
            sort_col: Column name to sort by (descending for Sharpe/return;
                ascending for drawdown/volatility).
            market_filter: Market filter value (``"all"``, ``"india"``, ``"us"``).
            active_tab: Currently active Insights tab ID.
            page: Current pagination page (1-based).
            page_size_str: Number of rows per page as string.

        Returns:
            Tuple of (table component, count text, pagination max_value).
        """
        if active_tab != "risk-tab":
            return no_update, no_update, no_update

        repo = _get_iceberg_repo()
        df = pd.DataFrame()
        if repo is not None:
            df = _get_analysis_with_gaps_filled(repo)

        # Fix #16: vectorised market filter
        if not df.empty and market_filter and market_filter != "all":
            if market_filter == "india":
                mask = df["ticker"].str.endswith((".NS", ".BO"))
            else:
                mask = ~df["ticker"].str.endswith((".NS", ".BO"))
            df = df[mask].reset_index(drop=True)

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

        # Sort ascending for drawdown/volatility, descending for return/Sharpe
        ascending = sort_col in (
            "max_drawdown_pct",
            "annualized_volatility_pct",
            "max_drawdown_duration_days",
        )
        if sort_col in display_df.columns:
            display_df = display_df.sort_values(
                sort_col, ascending=ascending, na_position="last"
            ).reset_index(drop=True)

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

        # Fix #5: replace iterrows() with to_dict("records") for faster iteration
        rows_html = [
            html.Tr(
                [
                    html.Td(str(v) if v is not None else "—")
                    for v in row.values()
                ]
            )
            for row in display_df.to_dict("records")
        ]

        return (
            dbc.Table(
                [
                    html.Thead(
                        html.Tr([html.Th(c) for c in display_df.columns])
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
    )
    def update_sectors(active_tab: str) -> tuple:
        """Populate the sector analysis chart and summary table.

        Joins ``stocks.company_info`` (for sector names) with
        ``stocks.analysis_summary`` (for performance).

        Args:
            active_tab: Currently active Insights tab ID.

        Returns:
            Tuple of (Plotly figure, table component).
        """
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
                "No sector data available. Run backfill first.",
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

        if merged.empty:
            return empty_fig, dbc.Alert(
                "No sector metadata found.", color="info"
            )

        sector_agg = (
            merged.groupby("sector")
            .agg(
                count=("ticker", "count"),
                avg_return=("annualized_return_pct", "mean"),
                avg_sharpe=("sharpe_ratio", "mean"),
                avg_vol=("annualized_volatility_pct", "mean"),
            )
            .reset_index()
            .sort_values("avg_return", ascending=False)
        )

        # Bar chart — average annualised return by sector
        colors = [
            "#4caf50" if r >= 0 else "#ef5350"
            for r in sector_agg["avg_return"]
        ]
        fig = go.Figure(
            go.Bar(
                x=sector_agg["sector"],
                y=sector_agg["avg_return"].round(2),
                marker_color=colors,
                text=sector_agg["avg_return"].round(1).astype(str) + "%",
                textposition="outside",
            )
        )
        fig.update_layout(
            template="plotly_white",
            title="Average Annualised Return by Sector",
            xaxis_title="Sector",
            yaxis_title="Avg Ann. Return %",
            paper_bgcolor="#f9fafb",
            plot_bgcolor="#ffffff",
            height=400,
            margin=dict(l=50, r=30, t=60, b=100),
        )
        fig.update_xaxes(tickangle=-30)

        # Summary table
        sector_agg_disp = sector_agg.copy()
        sector_agg_disp.columns = [
            "Sector",
            "Stocks",
            "Avg Return %",
            "Avg Sharpe",
            "Avg Vol %",
        ]
        for col in ["Avg Return %", "Avg Sharpe", "Avg Vol %"]:
            sector_agg_disp[col] = sector_agg_disp[col].round(2)

        table = dbc.Table.from_dataframe(
            sector_agg_disp,
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
    )
    def update_correlation(period: str) -> go.Figure:
        """Build the returns correlation heatmap.

        Reads OHLCV data from the Iceberg ``stocks.ohlcv`` table (or flat
        parquet files as fallback), computes daily close-price returns for
        each ticker, and renders a heatmap.

        Args:
            period: Lookback period: ``"1y"``, ``"3y"``, or ``"all"``.

        Returns:
            Plotly heatmap figure.
        """
        empty_fig = go.Figure().update_layout(
            template="plotly_white",
            title="No OHLCV data available",
            paper_bgcolor="#f9fafb",
        )

        repo = _get_iceberg_repo()
        close_data = {}

        if repo is not None:
            df_all = repo._table_to_df("stocks.ohlcv")
            if not df_all.empty:
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

        # Fallback: read OHLCV from Iceberg per-ticker using cached helper
        if not close_data:
            try:
                fallback_repo = _get_iceberg_repo()
                if fallback_repo is not None:
                    registry = fallback_repo.get_all_registry()
                    for ticker in sorted(registry.keys()):
                        ohlcv = _get_ohlcv_cached(fallback_repo, ticker)
                        if ohlcv is not None and not ohlcv.empty:
                            close_data[ticker] = ohlcv["Close"].dropna()
            except Exception as _e:
                _logger.warning("Correlation fallback failed: %s", _e)

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
