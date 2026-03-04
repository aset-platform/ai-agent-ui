"""Forecast-page Dash callbacks for the AI Stock Analysis Dashboard.

Registers callbacks that sync the forecast ticker dropdown, update the
forecast chart, target cards, and accuracy row, and run the full stock data
refresh pipeline when the "Refresh Data & Run Analysis" button is clicked.

Example::

    from dashboard.callbacks.forecast_cbs import register
    register(app)
"""

import logging
from typing import Optional
from urllib.parse import parse_qs

import pandas as pd
from dash import Input, Output, State, html, no_update

from dashboard.callbacks.auth_utils import _unauth_notice, _validate_token
from dashboard.callbacks.card_builders import (
    _build_accuracy_row,
    _build_target_cards,
    _generate_forecast_summary_cb,
)
from dashboard.callbacks.chart_builders import _empty_fig
from dashboard.callbacks.chart_builders2 import _build_forecast_fig
from dashboard.callbacks.data_loaders import (
    _clear_indicator_cache,
    _load_forecast,
    _load_raw,
)
from dashboard.callbacks.iceberg import clear_caches
from dashboard.services.stock_refresh import run_full_refresh

# Module-level logger — intentionally module-scoped (not inside a class)
_logger = logging.getLogger(__name__)


def register(app) -> None:
    """Register forecast-page callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
    """

    @app.callback(
        Output("forecast-ticker-dropdown", "value"),
        [Input("url", "search"), Input("url", "pathname")],
        State("nav-ticker-store", "data"),
    )
    def sync_forecast_ticker(
        search: Optional[str], pathname: Optional[str], stored_ticker: Optional[str]
    ):
        """Pre-select the forecast dropdown when navigating from a stock card.

        Args:
            search: URL query string.
            pathname: Current URL path.
            stored_ticker: Ticker stored via the nav store.

        Returns:
            Ticker string or :data:`~dash.no_update`.
        """
        if pathname != "/forecast":
            return no_update
        if search:
            params = parse_qs(search.lstrip("?"))
            t = params.get("ticker", [None])[0]
            if t:
                return t.upper()
        if stored_ticker:
            return stored_ticker
        return no_update

    @app.callback(
        [
            Output("forecast-chart", "figure"),
            Output("forecast-target-cards", "children"),
            Output("forecast-accuracy-row", "children"),
        ],
        [
            Input("forecast-ticker-dropdown", "value"),
            Input("forecast-horizon-radio", "value"),
            Input("forecast-refresh-store", "data"),
        ],
        State("auth-token-store", "data"),
    )
    def update_forecast_chart(
        ticker: Optional[str],
        horizon: Optional[str],
        refresh_trigger,
        token: Optional[str],
    ):
        """Reload and render the forecast chart when inputs change.

        Args:
            ticker: Selected ticker from the dropdown.
            horizon: Forecast horizon string (``"3"``, ``"6"``, ``"9"``).
            refresh_trigger: Counter incremented by the Run New Analysis
                callback to force a chart refresh.
            token: JWT access token from the auth-token-store.

        Returns:
            Tuple of (forecast figure, target-cards component,
            accuracy-row component).
        """
        if _validate_token(token) is None:
            return _empty_fig("Authentication required."), _unauth_notice(), []

        if not ticker:
            return _empty_fig("Select a ticker to begin."), [], []

        horizon_months = int(horizon) if horizon else 9

        df_raw = _load_raw(ticker)
        if df_raw is None:
            return _empty_fig(f"No price data for '{ticker}'."), [], []

        # Build prophet-format historical series
        # yfinance >=1.2 dropped "Adj Close"; also Iceberg may store it as
        # all-NaN.  Fall back to "Close" when the column is absent or empty.
        if "Adj Close" in df_raw.columns and df_raw["Adj Close"].notna().any():
            price_col = "Adj Close"
        else:
            price_col = "Close"
        prophet_df = (
            pd.DataFrame(
                {
                    "ds": pd.to_datetime(df_raw.index).tz_localize(None),
                    "y": df_raw[price_col].values,
                }
            )
            .dropna(subset=["y"])
            .sort_values("ds")
        )
        if prophet_df.empty:
            return _empty_fig(f"No valid price data for '{ticker}'."), [], []
        current_price = float(prophet_df["y"].iloc[-1])

        forecast_df = _load_forecast(ticker, horizon_months)
        if forecast_df is None:
            msg = (
                f"No forecast found for '{ticker}'. "
                "Click 'Refresh Data & Run Analysis' to generate one."
            )
            return (
                _empty_fig(msg, height=550),
                [],
                [html.P(msg, className="text-muted small")],
            )

        # Trim to requested horizon
        cutoff = pd.Timestamp.now() + pd.DateOffset(months=horizon_months)
        forecast_df = forecast_df[forecast_df["ds"] <= cutoff].copy()

        summary = _generate_forecast_summary_cb(
            forecast_df, current_price, ticker, horizon_months
        )
        fig = _build_forecast_fig(
            prophet_df, forecast_df, ticker, current_price, summary
        )

        target_cards = _build_target_cards(summary, current_price, ticker)
        accuracy_note = [
            html.P(
                "Model accuracy metrics are computed when you click "
                "'Refresh Data & Run Analysis'.",
                className="text-muted small",
            )
        ]
        return fig, target_cards, accuracy_note

    @app.callback(
        [
            Output("forecast-refresh-status", "children"),
            Output("forecast-refresh-store", "data"),
            Output("forecast-accuracy-row", "children", allow_duplicate=True),
        ],
        Input("forecast-refresh-btn", "n_clicks"),
        [
            State("forecast-ticker-dropdown", "value"),
            State("forecast-horizon-radio", "value"),
            State("forecast-refresh-store", "data"),
            State("auth-token-store", "data"),
        ],
        prevent_initial_call=True,
    )
    def run_new_analysis(
        n_clicks: Optional[int],
        ticker: Optional[str],
        horizon: Optional[str],
        current_refresh: Optional[int],
        token: Optional[str],
    ):
        """Run the full stock data refresh pipeline for the selected ticker.

        Delegates to :func:`~dashboard.services.stock_refresh.run_full_refresh`
        which refreshes all 8 Iceberg tables (OHLCV, company info, dividends,
        technical indicators, analysis summary, forecast runs, forecasts,
        and the registry).  Clears dashboard caches on success and increments
        the ``forecast-refresh-store`` counter to trigger a chart reload.

        Args:
            n_clicks: Button click counter.
            ticker: Selected ticker symbol.
            horizon: Forecast horizon string.
            current_refresh: Current store value (incremented on success).
            token: JWT access token from the auth-token-store.

        Returns:
            Tuple of (status icon span, new refresh counter,
            accuracy-row component).
        """
        if _validate_token(token) is None:
            return _unauth_notice(), no_update, []

        if not ticker:
            return (
                html.Span(
                    "\u2717 Select a ticker",
                    className="refresh-status-icon text-warning",
                ),
                no_update,
                [],
            )

        horizon_months = int(horizon) if horizon else 9
        ticker = ticker.upper().strip()

        result = run_full_refresh(ticker, horizon_months)

        # Clear all dashboard caches so subsequent reads get fresh data
        clear_caches(ticker)
        _clear_indicator_cache(ticker)

        if result.success:
            acc_row = (
                _build_accuracy_row(result.accuracy, ticker) if result.accuracy else []
            )
            return (
                html.Span(
                    "\u2713",
                    className="refresh-status-icon text-success",
                    title=f"Refresh complete for {ticker}",
                ),
                (current_refresh or 0) + 1,
                acc_row,
            )

        error_msg = result.error or "Unknown error"
        _logger.error("run_new_analysis failed: %s", error_msg)
        return (
            html.Span(
                "\u2717",
                className="refresh-status-icon text-danger",
                title=error_msg[:200],
            ),
            no_update,
            [],
        )
