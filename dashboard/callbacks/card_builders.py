"""Stat card and accuracy row builders for the AI Stock Analysis Dashboard.

Provides helpers that build Bootstrap card components for displaying
summary statistics, price targets, model accuracy, and forecast summaries.

Example::

    from dashboard.callbacks.card_builders import (
        _build_stats_cards, _build_target_cards,
    )
"""

import logging
import math
from datetime import date
from typing import Any

import dash_bootstrap_components as dbc
import pandas as pd
from dash import html

from dashboard.callbacks.utils import _get_currency

# Module-level logger; intentionally kept at module
# scope for this utility module.
_logger = logging.getLogger(__name__)


def _build_stats_cards(df: pd.DataFrame, ticker: str) -> Any:
    """Build a row of six summary-stat Bootstrap cards for the analysis page.

    Args:
        df: Full OHLCV DataFrame with indicator columns added.
        ticker: Ticker symbol (used for logging only).

    Returns:
        :class:`dash_bootstrap_components.Row` containing six stat cards.
    """
    sym = _get_currency(ticker)
    close = df["Close"]
    daily_returns = close.pct_change().dropna()

    ath = round(float(close.max()), 2)
    atl = round(float(close.min()), 2)
    annual_ret = round(float(daily_returns.mean() * 252 * 100), 2)
    ann_vol = round(float(daily_returns.std() * math.sqrt(252) * 100), 2)

    rolling_max = close.cummax()
    drawdown = (close - rolling_max) / rolling_max
    max_dd = round(float(drawdown.min() * 100), 2)

    ann_vol_dec = daily_returns.std() * math.sqrt(252)
    sharpe = round(
        (
            (daily_returns.mean() * 252 - 0.04) / ann_vol_dec
            if ann_vol_dec > 0
            else 0.0
        ),
        2,
    )

    stats = [
        ("All-Time High", f"{sym}{ath:,}", "text-success"),
        ("All-Time Low", f"{sym}{atl:,}", "text-danger"),
        (
            "Annual Return",
            f"{annual_ret:+.1f}%",
            "text-success" if annual_ret >= 0 else "text-danger",
        ),
        ("Max Drawdown", f"{max_dd:.1f}%", "text-danger"),
        ("Volatility", f"{ann_vol:.1f}%", "text-warning"),
        ("Sharpe Ratio", str(sharpe), "text-info"),
    ]

    cols = []
    for label, value, color_cls in stats:
        cols.append(
            dbc.Col(
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.Small(label, className="text-muted d-block"),
                            html.Span(
                                value, className=f"fs-5 fw-bold {color_cls}"
                            ),
                        ]
                    ),
                    className="stat-card h-100",
                ),
                xs=6,
                md=4,
                lg=2,
                className="mb-3",
            )
        )
    return dbc.Row(cols)


def _build_target_cards(
    summary: dict, current_price: float, ticker: str = ""
) -> Any:
    """Build price-target cards for the forecast page.

    Args:
        summary: Dict produced by the forecast summary helper with a
            ``targets`` sub-dict keyed by ``"3m"``, ``"6m"``, ``"9m"``.
        current_price: Most recent closing price (for display).
        ticker: Ticker symbol used to look up the correct currency symbol.

    Returns:
        :class:`dash_bootstrap_components.Row` of price-target cards.
    """
    sym = _get_currency(ticker) if ticker else "$"
    targets = summary.get("targets", {})
    if not targets:
        return html.P("No price targets available.", className="text-muted")

    cols = []
    label_map = {"3m": "3 Month", "6m": "6 Month", "9m": "9 Month"}
    color_map = {"3m": "warning", "6m": "info", "9m": "danger"}

    for key in ["3m", "6m", "9m"]:
        t = targets.get(key)
        if not t:
            continue
        sign = "+" if t["pct_change"] >= 0 else ""
        text_color = "text-success" if t["pct_change"] >= 0 else "text-danger"
        cols.append(
            dbc.Col(
                dbc.Card(
                    [
                        dbc.CardHeader(
                            label_map[key],
                            className=(
                                "text-center bg-transparent"
                                f" border-{color_map[key]}"
                            ),
                        ),
                        dbc.CardBody(
                            [
                                html.H5(
                                    f"{sym}{t['price']:,}",
                                    className="text-center mb-1",
                                ),
                                html.P(
                                    f"{sign}{t['pct_change']:.1f}%",
                                    className=(
                                        "text-center fw-bold"
                                        f" mb-1 {text_color}"
                                    ),
                                ),
                                html.Small(
                                    (
                                        f"{sym}{t['lower']:,}"
                                        f" – {sym}{t['upper']:,}"
                                    ),
                                    className="text-muted d-block text-center",
                                ),
                            ]
                        ),
                    ],
                    className=f"target-card border border-{color_map[key]}",
                ),
                xs=12,
                sm=4,
                className="mb-3",
            )
        )

    return dbc.Row(cols)


def _build_accuracy_row(accuracy: dict, ticker: str = "") -> Any:
    """Build the model-accuracy metric cards for the forecast page.

    Args:
        accuracy: Dict with ``MAE``, ``RMSE``, ``MAPE_pct`` keys (or
            ``"error"`` key if accuracy could not be computed).
        ticker: Ticker symbol used to look up the correct currency symbol.

    Returns:
        :class:`dash_bootstrap_components.Row` or an error paragraph.
    """
    if "error" in accuracy:
        return html.P(
            f"Accuracy: {accuracy['error']}", className="text-muted small"
        )

    sym = _get_currency(ticker) if ticker else "$"
    metrics = [
        ("MAE", f"{sym}{accuracy['MAE']:,.2f}", "Mean Absolute Error"),
        ("RMSE", f"{sym}{accuracy['RMSE']:,.2f}", "Root Mean Square Error"),
        (
            "MAPE",
            f"{accuracy['MAPE_pct']:.1f}%",
            "Mean Abs % Error (lower = better)",
        ),
    ]
    cols = [
        dbc.Col(
            dbc.Card(
                dbc.CardBody(
                    [
                        html.Small(title, className="text-muted d-block"),
                        html.Span(value, className="fs-5 fw-bold text-info"),
                        html.Small(f" ({label})", className="text-muted"),
                    ]
                ),
                className="stat-card",
            ),
            xs=12,
            sm=4,
            className="mb-3",
        )
        for label, value, title in metrics
    ]
    return dbc.Row(cols)


def _generate_forecast_summary_cb(
    forecast_df: pd.DataFrame,
    current_price: float,
    ticker: str,
    months: int,
) -> dict:
    """Compute price targets and sentiment from a forecast DataFrame.

    Args:
        forecast_df: Future-only forecast with ``ds``, ``yhat``, etc.
        current_price: Most recent closing price.
        ticker: Ticker symbol.
        months: Forecast horizon in months.

    Returns:
        Dict with ``targets`` sub-dict and ``sentiment`` string.
    """
    today = pd.Timestamp(date.today())
    targets = {}

    for m in [3, 6, 9]:
        if m > months:
            continue
        target_date = today + pd.DateOffset(months=m)
        idx = (forecast_df["ds"] - target_date).abs().idxmin()
        row = forecast_df.iloc[idx]
        price = float(row["yhat"])
        pct = (price - current_price) / current_price * 100
        targets[f"{m}m"] = {
            "date": str(row["ds"].date()),
            "price": round(price, 2),
            "pct_change": round(pct, 2),
            "lower": round(float(row["yhat_lower"]), 2),
            "upper": round(float(row["yhat_upper"]), 2),
        }

    last_key = (
        f"{min(months, 9)}m"
        if f"{min(months, 9)}m" in targets
        else ("6m" if "6m" in targets else "3m")
    )
    final_pct = targets.get(last_key, {}).get("pct_change", 0.0)
    sentiment = (
        "Bullish"
        if final_pct > 10
        else ("Bearish" if final_pct < -10 else "Neutral")
    )

    return {
        "ticker": ticker,
        "current_price": current_price,
        "targets": targets,
        "sentiment": sentiment,
    }
