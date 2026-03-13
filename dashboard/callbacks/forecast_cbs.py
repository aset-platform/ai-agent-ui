"""Forecast-page Dash callbacks for the AI Stock Analysis Dashboard.

Registers callbacks that sync the forecast ticker dropdown, update the
forecast chart, target cards, and accuracy row, and run the full stock data
refresh pipeline when the "Refresh Data & Run Analysis" button is clicked.

Example::

    from dashboard.callbacks.forecast_cbs import register
    register(app)
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import parse_qs

import pandas as pd
from dash import Input, Output, State, html, no_update

from dashboard.callbacks.auth_utils import (
    _fetch_user_tickers,
    _resolve_token,
    _unauth_notice,
    _validate_token,
)
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
    _load_reg_cb,
)
from dashboard.callbacks.iceberg import clear_caches
from dashboard.callbacks.refresh_state import RefreshManager
from dashboard.components.error_overlay import make_error_banner
from dashboard.services.stock_refresh import run_full_refresh

# Module-level logger (immutable singleton — not mutable state).
_logger = logging.getLogger(__name__)


def register(app, mgr: RefreshManager) -> None:
    """Register forecast-page callbacks with *app*.

    Args:
        app: The :class:`~dash.Dash` application instance.
        mgr: Thread-safe refresh manager for background jobs.
    """

    @app.callback(
        Output("forecast-ticker-dropdown", "value"),
        [Input("url", "search"), Input("url", "pathname")],
        State("nav-ticker-store", "data"),
    )
    def sync_forecast_ticker(
        search: Optional[str],
        pathname: Optional[str],
        stored_ticker: Optional[str],
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
        Output("forecast-ticker-dropdown", "options"),
        Input("url", "pathname"),
        State("auth-token-store", "data"),
        State("url", "search"),
    )
    def filter_forecast_dropdown(pathname, token, search):
        """Update forecast dropdown to user tickers.

        Args:
            pathname: Current URL path.
            token: JWT access token.
            search: URL query string for token fallback.

        Returns:
            List of dropdown option dicts.
        """
        token = _resolve_token(token, search)
        registry = _load_reg_cb()
        all_tickers = sorted(registry.keys())
        ut = _fetch_user_tickers(token)
        if ut is not None:
            tickers = [t for t in all_tickers if t in ut]
        else:
            tickers = all_tickers
        return [{"label": t, "value": t} for t in tickers]

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
        [
            State("auth-token-store", "data"),
            State("url", "search"),
        ],
    )
    def update_forecast_chart(
        ticker: Optional[str],
        horizon: Optional[str],
        refresh_trigger,
        token: Optional[str],
        search: Optional[str] = None,
    ):
        """Reload and render the forecast chart when inputs change.

        Args:
            ticker: Selected ticker from the dropdown.
            horizon: Forecast horizon string (``"3"``, ``"6"``, ``"9"``).
            refresh_trigger: Counter incremented by the Run New Analysis
                callback to force a chart refresh.
            token: JWT access token from the auth-token-store.
            search: URL query string for token fallback.

        Returns:
            Tuple of (forecast figure, target-cards component,
            accuracy-row component).
        """
        token = _resolve_token(token, search)
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
        Output("forecast-refresh-status", "children"),
        Input("forecast-refresh-btn", "n_clicks"),
        [
            State("forecast-ticker-dropdown", "value"),
            State("forecast-horizon-radio", "value"),
            State("auth-token-store", "data"),
        ],
        prevent_initial_call=True,
    )
    def start_forecast_refresh(
        n_clicks: int | None,
        ticker: str | None,
        horizon: str | None,
        token: str | None,
    ):
        """Submit forecast refresh to background thread.

        Returns a spinner immediately; the poll callback
        harvests the result when the future completes.

        Args:
            n_clicks: Button click counter.
            ticker: Selected ticker symbol.
            horizon: Forecast horizon string.
            token: JWT access token.

        Returns:
            Status span (spinner or validation error).
        """
        if not n_clicks:
            return no_update

        if _validate_token(token) is None:
            return _unauth_notice()

        if not ticker:
            return html.Span(
                "\u2717 Select a ticker",
                className=("refresh-status-icon text-warning"),
            )

        horizon_months = int(horizon) if horizon else 9
        ticker = ticker.upper().strip()

        if not mgr.submit_if_idle(
            ticker,
            run_full_refresh,
            ticker,
            horizon_months,
        ):
            return no_update

        _logger.info(
            "Forecast refresh submitted for %s (%dm)",
            ticker,
            horizon_months,
        )

        return html.Span(
            className="card-refresh-spinner",
            title=f"Refreshing {ticker}\u2026",
        )

    @app.callback(
        [
            Output(
                "forecast-refresh-status",
                "children",
                allow_duplicate=True,
            ),
            Output("forecast-refresh-store", "data"),
            Output(
                "forecast-accuracy-row",
                "children",
                allow_duplicate=True,
            ),
            Output(
                "error-overlay-container",
                "children",
                allow_duplicate=True,
            ),
        ],
        Input("forecast-refresh-poll", "n_intervals"),
        State("forecast-refresh-store", "data"),
        prevent_initial_call=True,
    )
    def poll_forecast_refresh(
        n_intervals: int,
        current_refresh: int | None,
    ):
        """Poll the background forecast refresh future.

        Checks every 2 s whether the submitted future has
        completed.  When done, clears caches, updates
        the status icon, accuracy row, and refresh store.

        Args:
            n_intervals: Interval tick counter.
            current_refresh: Current refresh store value.

        Returns:
            Tuple of (status, counter, accuracy, overlay).
        """
        for ticker, fut in mgr.harvest_done():
            clear_caches(ticker)
            _clear_indicator_cache(ticker)

            try:
                result = fut.result()
            except Exception as exc:
                _logger.error(
                    "Forecast refresh exception: %s",
                    exc,
                )
                return (
                    html.Span(
                        "\u2717",
                        className=("refresh-status-icon" " text-danger"),
                        title=str(exc)[:200],
                    ),
                    no_update,
                    [],
                    make_error_banner(
                        "Refresh failed \u2014 " + str(exc)[:150]
                    ),
                )

            if result.success:
                acc_row = (
                    _build_accuracy_row(result.accuracy, ticker)
                    if result.accuracy
                    else []
                )
                return (
                    html.Span(
                        "\u2713",
                        className=("refresh-status-icon" " text-success"),
                        title=("Refresh complete" f" for {ticker}"),
                    ),
                    (current_refresh or 0) + 1,
                    acc_row,
                    no_update,
                )

            error_msg = result.error or "Unknown error"
            _logger.error(
                "run_new_analysis failed: %s",
                error_msg,
            )
            return (
                html.Span(
                    "\u2717",
                    className=("refresh-status-icon text-danger"),
                    title=error_msg[:200],
                ),
                no_update,
                [],
                make_error_banner(
                    f"Refresh failed for {ticker}" f" \u2014 {error_msg[:150]}"
                ),
            )

        return (
            no_update,
            no_update,
            no_update,
            no_update,
        )
