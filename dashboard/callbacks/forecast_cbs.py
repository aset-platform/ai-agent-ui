"""Forecast-page Dash callbacks for the AI Stock Analysis Dashboard.

Registers callbacks that sync the forecast ticker dropdown, update the
forecast chart, target cards, and accuracy row, and run the full
fetch → Prophet pipeline when the "Run New Analysis" button is clicked.

Example::

    from dashboard.callbacks.forecast_cbs import register
    register(app)
"""

import logging
from typing import Optional
from urllib.parse import parse_qs

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, html, no_update

from dashboard.callbacks.auth_utils import _validate_token, _unauth_notice
from dashboard.callbacks.card_builders import (
    _build_accuracy_row,
    _build_target_cards,
    _generate_forecast_summary_cb,
)
from dashboard.callbacks.chart_builders import _empty_fig
from dashboard.callbacks.chart_builders2 import _build_forecast_fig
from dashboard.callbacks.data_loaders import _load_forecast, _load_raw

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
    def sync_forecast_ticker(search: Optional[str], pathname: Optional[str], stored_ticker: Optional[str]):
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
        price_col = "Adj Close" if "Adj Close" in df_raw.columns else "Close"
        prophet_df = pd.DataFrame({
            "ds": pd.to_datetime(df_raw.index).tz_localize(None),
            "y": df_raw[price_col].values,
        }).dropna(subset=["y"]).sort_values("ds")
        current_price = float(prophet_df["y"].iloc[-1])

        forecast_df = _load_forecast(ticker, horizon_months)
        if forecast_df is None:
            msg = (
                f"No forecast found for '{ticker}'. "
                "Click 'Run New Analysis' to generate one."
            )
            return _empty_fig(msg, height=550), [], [
                html.P(msg, className="text-muted small")
            ]

        # Trim to requested horizon
        cutoff = pd.Timestamp.now() + pd.DateOffset(months=horizon_months)
        forecast_df = forecast_df[forecast_df["ds"] <= cutoff].copy()

        summary = _generate_forecast_summary_cb(
            forecast_df, current_price, ticker, horizon_months
        )
        fig = _build_forecast_fig(prophet_df, forecast_df, ticker, current_price, summary)

        target_cards = _build_target_cards(summary, current_price, ticker)
        accuracy_note = [html.P(
            "Model accuracy metrics are computed when you click 'Run New Analysis'.",
            className="text-muted small",
        )]
        return fig, target_cards, accuracy_note

    @app.callback(
        [
            Output("run-analysis-status", "children"),
            Output("forecast-refresh-store", "data"),
            Output("forecast-accuracy-row", "children", allow_duplicate=True),
        ],
        Input("run-analysis-btn", "n_clicks"),
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
        """Run the full fetch → Prophet forecast pipeline for the selected ticker.

        Imports backend tool functions directly (no HTTP call to the backend
        API).  Increments the ``forecast-refresh-store`` counter on success
        to trigger a chart reload.

        Args:
            n_clicks: Button click counter.
            ticker: Selected ticker symbol.
            horizon: Forecast horizon string.
            current_refresh: Current store value (incremented on success).
            token: JWT access token from the auth-token-store.

        Returns:
            Tuple of (status message, new refresh counter,
            accuracy-row component).
        """
        if _validate_token(token) is None:
            return _unauth_notice(), no_update, []

        if not ticker:
            return (
                dbc.Alert("Please select a ticker first.", color="warning"),
                no_update,
                [],
            )

        horizon_months = int(horizon) if horizon else 9
        ticker = ticker.upper().strip()

        try:
            # backend tools use `import tools.*` internally, so
            # backend/ must be on sys.path for those imports to resolve.
            import sys as _sys
            _backend_dir = str(Path(__file__).parent.parent.parent / "backend")
            if _backend_dir not in _sys.path:
                _sys.path.insert(0, _backend_dir)

            # ── Step 1: Fetch / delta-update price data ────────────────────
            from tools.stock_data_tool import fetch_stock_data
            fetch_result = fetch_stock_data.invoke({"ticker": ticker})
            _logger.info("fetch_stock_data result: %s", fetch_result[:80])

            # ── Step 2: Run Prophet forecast pipeline ──────────────────────
            from tools.forecasting_tool import (
                _load_parquet as _ft_load,
                _prepare_data_for_prophet,
                _train_prophet_model,
                _generate_forecast,
                _calculate_forecast_accuracy,
                _save_forecast,
            )

            df = _ft_load(ticker)
            if df is None:
                raise ValueError(f"No data loaded for {ticker} after fetch.")

            prophet_df = _prepare_data_for_prophet(df)
            current_price = float(prophet_df["y"].iloc[-1])

            _logger.info(
                "Training Prophet model for %s (%dm)…", ticker, horizon_months
            )
            model = _train_prophet_model(prophet_df)
            forecast_df = _generate_forecast(model, prophet_df, horizon_months)
            accuracy = _calculate_forecast_accuracy(model, prophet_df)
            _save_forecast(forecast_df, ticker, horizon_months)

            _logger.info("New analysis complete for %s.", ticker)

            acc_row = _build_accuracy_row(accuracy, ticker)
            status = dbc.Alert(
                f"Analysis complete for {ticker}. Forecast updated.",
                color="success",
                duration=5000,
            )
            return status, (current_refresh or 0) + 1, acc_row

        except Exception as exc:
            _logger.error("run_new_analysis error: %s", exc, exc_info=True)
            return (
                dbc.Alert(f"Error: {exc}", color="danger"),
                no_update,
                [],
            )