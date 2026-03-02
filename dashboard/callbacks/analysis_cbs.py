"""Analysis-page Dash callbacks for the AI Stock Analysis Dashboard.

Registers callbacks that sync the ticker dropdown from the URL, update
the 3-panel analysis chart and summary-stat cards, and handle the compare
page (normalised performance, metrics table, correlation heatmap).

Example::

    from dashboard.callbacks.analysis_cbs import register
    register(app)
"""

import logging
import math
from typing import Optional
from urllib.parse import parse_qs

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, html, no_update

from dashboard.callbacks.auth_utils import _unauth_notice, _validate_token
from dashboard.callbacks.card_builders import _build_stats_cards
from dashboard.callbacks.chart_builders import _build_analysis_fig, _empty_fig
from dashboard.callbacks.data_loaders import (
    _add_indicators,
    _add_indicators_cached,
    _load_forecast,
    _load_raw,
    _load_reg_cb,
)

# Module-level logger; mutable but intentionally module-scoped for callback registration.
_logger = logging.getLogger(__name__)


def register(app) -> None:
    """Register analysis-page callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """

    @app.callback(
        Output("analysis-ticker-dropdown", "value"),
        [Input("url", "search"), Input("url", "pathname")],
        State("nav-ticker-store", "data"),
    )
    def sync_analysis_ticker(search, pathname, stored_ticker):
        """Pre-select the analysis dropdown when navigating from a stock card.

        Args:
            search: URL query string (e.g. ``"?ticker=AAPL"``).
            pathname: Current URL path.
            stored_ticker: Ticker stored via the home-page search or dropdown.

        Returns:
            Ticker string to set as dropdown value, or :data:`~dash.no_update`.
        """
        if pathname != "/analysis":
            return no_update
        if search:
            params = parse_qs(search.lstrip("?"))
            t = params.get("ticker", [None])[0]
            if t:
                return t.upper()
        if stored_ticker:
            return stored_ticker
        tickers = sorted(_load_reg_cb().keys())
        return tickers[0] if tickers else no_update

    @app.callback(
        [Output("analysis-chart", "figure"), Output("analysis-stats-row", "children")],
        [
            Input("analysis-ticker-dropdown", "value"),
            Input("date-range-slider", "value"),
            Input("overlay-toggles", "value"),
        ],
        State("auth-token-store", "data"),
    )
    def update_analysis_chart(ticker, date_range_idx, overlays, token):
        """Rebuild the 3-panel analysis chart and summary-stat cards.

        Args:
            ticker: Selected ticker from the dropdown.
            date_range_idx: Integer index into the date-range map
                (0=1M, 1=3M, 2=6M, 3=1Y, 4=3Y, 5=Max).
            overlays: List of active overlay keys.
            token: JWT access token from the auth-token-store.

        Returns:
            Tuple of (analysis figure, stats row component).
        """
        if _validate_token(token) is None:
            return _empty_fig("Authentication required."), _unauth_notice()

        if not ticker:
            return _empty_fig("Select a ticker to begin."), []

        df = _load_raw(ticker)
        if df is None:
            return (
                _empty_fig(
                    f"No data found for '{ticker}'. Fetch data via the chat interface."
                ),
                [],
            )

        # Fix #1/#2/#14: use cached indicator computation (5-min TTL)
        df_full = _add_indicators_cached(ticker, df)

        # Apply date-range filter
        n_map = {0: 21, 1: 63, 2: 126, 3: 252, 4: 756, 5: len(df_full)}
        n_days = n_map.get(
            date_range_idx if date_range_idx is not None else 5, len(df_full)
        )
        df_plot = df_full.tail(n_days).copy()

        overlays = overlays or []
        fig = _build_analysis_fig(df_plot, ticker, overlays)
        stats = _build_stats_cards(df_full, ticker)
        return fig, stats

    @app.callback(
        [
            Output("compare-perf-chart", "figure"),
            Output("compare-metrics-container", "children"),
            Output("compare-heatmap", "figure"),
        ],
        Input("compare-ticker-dropdown", "value"),
        State("auth-token-store", "data"),
    )
    def update_compare(tickers, token):
        """Build the normalised performance chart, metrics table, and heatmap.

        Args:
            tickers: List of selected ticker symbols (2–5).
            token: JWT access token from the auth-token-store.

        Returns:
            Tuple of (performance figure, metrics table component,
            heatmap figure).
        """
        if _validate_token(token) is None:
            empty = _empty_fig("Authentication required.", height=450)
            return empty, _unauth_notice(), _empty_fig("", height=380)

        empty_perf = _empty_fig("Select 2–5 stocks to compare.", height=450)
        empty_heat = _empty_fig("", height=380)

        if not tickers or len(tickers) < 2:
            return (
                empty_perf,
                html.P("Select at least 2 stocks.", className="text-muted"),
                empty_heat,
            )

        # ── Load data ─────────────────────────────────────────────────────
        dfs = {}
        for t in tickers[:5]:
            df = _load_raw(t)
            if df is not None and len(df) > 1:
                dfs[t] = df

        if len(dfs) < 2:
            return (
                _empty_fig("Could not load data for 2 or more selected stocks.", 450),
                html.P("Data unavailable.", className="text-muted"),
                empty_heat,
            )

        # ── Common start date ─────────────────────────────────────────────
        common_start = max(df.index.min() for df in dfs.values())
        aligned = {t: df[df.index >= common_start]["Close"] for t, df in dfs.items()}

        # ── Normalised performance chart ──────────────────────────────────
        perf_fig = go.Figure()
        final_values = {}
        for t, series in aligned.items():
            norm = (series / series.iloc[0]) * 100
            perf_fig.add_trace(
                go.Scatter(
                    x=norm.index,
                    y=norm,
                    name=t,
                    mode="lines",
                    line=dict(width=2),
                )
            )
            final_values[t] = float(norm.iloc[-1])

        best_ticker = max(final_values, key=final_values.get)
        perf_fig.update_layout(
            template="plotly_white",
            height=450,
            paper_bgcolor="#ffffff",
            plot_bgcolor="#f9fafb",
            font=dict(color="#111827"),
            title=dict(text="Normalised Performance (Base = 100)", font=dict(size=15)),
            yaxis_title="Value (Base 100)",
            margin=dict(l=60, r=30, t=60, b=40),
            hovermode="x unified",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, x=1, xanchor="right"
            ),
        )
        perf_fig.update_xaxes(gridcolor="#e5e7eb")
        perf_fig.update_yaxes(gridcolor="#e5e7eb")

        # ── Metrics table ─────────────────────────────────────────────────
        rows = []
        for t in sorted(dfs.keys()):
            df = dfs[t]
            # Fix #1/#2/#14: use cached indicator computation
            df_ind = _add_indicators_cached(t, df)
            close = df["Close"]
            daily = close.pct_change().dropna()
            ann_vol = daily.std() * math.sqrt(252)
            ann_ret = daily.mean() * 252
            sharpe = round((ann_ret - 0.04) / ann_vol if ann_vol > 0 else 0.0, 2)
            rm = close.cummax()
            dd = (close - rm) / rm
            max_dd = round(float(dd.min() * 100), 2)
            rsi_val = (
                round(float(df_ind["RSI_14"].iloc[-1]), 1)
                if "RSI_14" in df_ind.columns
                else "N/A"
            )
            macd_val = df_ind["MACD"].iloc[-1] if "MACD" in df_ind.columns else None
            msig_val = (
                df_ind["MACD_Signal"].iloc[-1]
                if "MACD_Signal" in df_ind.columns
                else None
            )
            macd_sig = (
                "Bullish"
                if (
                    macd_val is not None
                    and msig_val is not None
                    and macd_val > msig_val
                )
                else "Bearish"
            )

            # 6-month forecast upside
            fc_df = _load_forecast(t, 6)
            if fc_df is not None and len(fc_df) > 0:
                cp = float(close.iloc[-1])
                fp = float(fc_df["yhat"].iloc[-1])
                fc_upside = f"{(fp - cp)/cp*100:+.1f}%"
                fc_sent = (
                    "Bullish"
                    if (fp - cp) / cp * 100 > 10
                    else ("Bearish" if (fp - cp) / cp * 100 < -10 else "Neutral")
                )
            else:
                fc_upside = "N/A"
                fc_sent = "N/A"

            badge = "🏆 " if t == best_ticker else ""
            rows.append(
                {
                    "Ticker": f"{badge}{t}",
                    "Annual Ret": f"{ann_ret*100:+.1f}%",
                    "Volatility": f"{ann_vol*100:.1f}%",
                    "Sharpe": str(sharpe),
                    "Max Drawdown": f"{max_dd:.1f}%",
                    "RSI": str(rsi_val),
                    "MACD": macd_sig,
                    "6M Upside": fc_upside,
                    "Sentiment": fc_sent,
                }
            )

        metrics_df = pd.DataFrame(rows)
        header_cells = [
            html.Th(col, className="text-muted small") for col in metrics_df.columns
        ]
        body_rows = []
        for _, row in metrics_df.iterrows():
            cells = [html.Td(str(v), className="small") for v in row]
            body_rows.append(html.Tr(cells))

        table = dbc.Table(
            [html.Thead(html.Tr(header_cells)), html.Tbody(body_rows)],
            bordered=True,
            hover=True,
            responsive=True,
            className="table table-sm mt-2",
        )

        # ── Correlation heatmap ────────────────────────────────────────────
        returns_dict = {t: aligned[t].pct_change().dropna() for t in aligned}
        corr = pd.DataFrame(returns_dict).corr()

        heat_fig = go.Figure(
            go.Heatmap(
                z=corr.values,
                x=list(corr.columns),
                y=list(corr.index),
                colorscale="RdBu",
                zmid=0,
                zmin=-1,
                zmax=1,
                text=[[f"{v:.2f}" for v in row] for row in corr.values],
                texttemplate="%{text}",
                showscale=True,
            )
        )
        heat_fig.update_layout(
            template="plotly_white",
            height=380,
            paper_bgcolor="#ffffff",
            plot_bgcolor="#ffffff",
            font=dict(color="#111827"),
            margin=dict(l=60, r=10, t=40, b=40),
            title=dict(text="Daily Returns Correlation", font=dict(size=13)),
        )

        return perf_fig, table, heat_fig
