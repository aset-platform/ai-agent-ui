"""Forecast chart builders for the AI Stock Analysis Dashboard.

Contains chart builders for the three forecast views:

- :func:`_build_forecast_fig` — standard forecast with confidence
  bands, price targets, and today marker.
- :func:`_build_decomposition_fig` — trend + seasonality subplots
  from Prophet component data.
- :func:`_build_multi_horizon_fig` — overlay of 3m/6m/9m forecasts
  on a single chart for horizon comparison.

Example::

    from dashboard.callbacks.chart_builders2 import (
        _build_forecast_fig,
        _build_decomposition_fig,
        _build_multi_horizon_fig,
    )
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard.callbacks.utils import _get_currency

# Module-level logger; kept at module scope as a private convention.
_logger = logging.getLogger(__name__)


def _build_forecast_fig(
    prophet_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
    ticker: str,
    current_price: float,
    summary: dict,
) -> go.Figure:
    """Build the interactive forecast chart.

    Shows historical price, confidence interval, forecast line, today
    marker, current-price line, and price-target annotations.

    Args:
        prophet_df: Historical data with ``ds`` (datetime) and ``y`` columns.
        forecast_df: Future-only forecast with ``ds``, ``yhat``,
            ``yhat_lower``, ``yhat_upper``.
        ticker: Ticker symbol for title and annotations.
        current_price: Most recent closing price.
        summary: Output of :func:`_generate_forecast_summary_cb`.

    Returns:
        :class:`plotly.graph_objects.Figure` for use in a :class:`dcc.Graph`.
    """
    sym = _get_currency(ticker)
    sentiment_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Neutral": "🟡"}.get(
        summary.get("sentiment", ""), ""
    )

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=prophet_df["ds"],
            y=prophet_df["y"],
            name="Historical Price",
            line=dict(color="#1e88e5", width=2),
            mode="lines",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=pd.concat([forecast_df["ds"], forecast_df["ds"].iloc[::-1]]),
            y=pd.concat(
                [
                    forecast_df["yhat_upper"],
                    forecast_df["yhat_lower"].iloc[::-1],
                ]
            ),
            fill="toself",
            fillcolor="rgba(76,175,80,0.15)",
            line=dict(color="rgba(0,0,0,0)"),
            name="80% Confidence Interval",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=forecast_df["ds"],
            y=forecast_df["yhat"],
            name="Forecast",
            line=dict(color="#4caf50", width=2, dash="dash"),
            mode="lines",
        )
    )

    # Today vertical line (use add_shape — Plotly 6.x datetime workaround)
    today_ts = pd.Timestamp(date.today())
    fig.add_shape(
        type="line",
        x0=today_ts,
        x1=today_ts,
        y0=0,
        y1=1,
        xref="x",
        yref="paper",
        line=dict(color="rgba(0,0,0,0.35)", width=1.5, dash="dot"),
    )
    fig.add_annotation(
        x=today_ts,
        y=1.02,
        yref="paper",
        text="Today",
        showarrow=False,
        font=dict(color="rgba(0,0,0,0.6)", size=10),
        xanchor="left",
    )

    # Current-price horizontal line
    fig.add_shape(
        type="line",
        x0=prophet_df["ds"].min(),
        x1=forecast_df["ds"].max(),
        y0=current_price,
        y1=current_price,
        xref="x",
        yref="y",
        line=dict(color="rgba(0,0,0,0.2)", width=1, dash="dot"),
    )
    fig.add_annotation(
        x=forecast_df["ds"].max(),
        y=current_price,
        text=f"Current: {sym}{current_price:.2f}",
        showarrow=False,
        font=dict(color="rgba(0,0,0,0.5)", size=10),
        xanchor="right",
        yanchor="bottom",
    )

    # Price-target annotations
    colors = {"3m": "#d97706", "6m": "#ea580c", "9m": "#dc2626"}
    for key, target in summary.get("targets", {}).items():
        sign = "+" if target["pct_change"] >= 0 else ""
        fig.add_annotation(
            x=target["date"],
            y=target["price"],
            text=(
                f"{key}: {sym}{target['price']}"
                f"<br>{sign}{target['pct_change']:.1f}%"
            ),
            showarrow=True,
            arrowhead=2,
            arrowcolor=colors.get(key, "#111827"),
            font=dict(color=colors.get(key, "#111827"), size=11),
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor=colors.get(key, "#e5e7eb"),
            borderwidth=1,
        )

    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f9fafb",
        font=dict(color="#111827"),
        title=dict(
            text=(
                f"{ticker} — Price Forecast"
                f"  {sentiment_emoji}"
                f" {summary.get('sentiment', '')}"
            ),
            font=dict(size=16),
        ),
        height=550,
        showlegend=True,
        margin=dict(l=60, r=30, t=80, b=50),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#e5e7eb")
    fig.update_yaxes(gridcolor="#e5e7eb")
    return fig


def _build_decomposition_fig(
    prophet_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
    ticker: str,
) -> go.Figure:
    """Build a trend + seasonality decomposition chart.

    Shows two subplots: the trend component (long-term
    direction) and the weekly/yearly seasonality pattern
    extracted from the Prophet forecast data.

    Args:
        prophet_df: Historical ``ds``/``y`` DataFrame.
        forecast_df: Forecast with ``ds``, ``yhat``,
            ``yhat_lower``, ``yhat_upper`` columns.
        ticker: Ticker symbol for the title.

    Returns:
        :class:`plotly.graph_objects.Figure` with two
        vertically-stacked subplots.
    """
    sym = _get_currency(ticker)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=[
            f"{ticker} — Trend Component",
            f"{ticker} — Seasonality",
        ],
        row_heights=[0.6, 0.4],
    )

    # ── Subplot 1: Trend ──────────────────────────
    # Use yhat as trend proxy (Prophet's trend component
    # is not stored separately in our Iceberg table).
    fig.add_trace(
        go.Scatter(
            x=prophet_df["ds"],
            y=prophet_df["y"],
            name="Historical",
            line=dict(color="#1e88e5", width=1.5),
            mode="lines",
            opacity=0.5,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=forecast_df["ds"],
            y=forecast_df["yhat"],
            name="Trend (Forecast)",
            line=dict(
                color="#4caf50",
                width=2.5,
            ),
            mode="lines",
        ),
        row=1,
        col=1,
    )
    # Confidence band on trend subplot
    fig.add_trace(
        go.Scatter(
            x=pd.concat(
                [
                    forecast_df["ds"],
                    forecast_df["ds"].iloc[::-1],
                ]
            ),
            y=pd.concat(
                [
                    forecast_df["yhat_upper"],
                    forecast_df["yhat_lower"].iloc[::-1],
                ]
            ),
            fill="toself",
            fillcolor="rgba(76,175,80,0.12)",
            line=dict(color="rgba(0,0,0,0)"),
            name="Confidence Band",
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    # ── Subplot 2: Seasonality ─────────────────────
    # Compute seasonality as residual: y - rolling mean
    hist = prophet_df.copy()
    hist = hist.set_index("ds").sort_index()
    window = min(63, max(5, len(hist) // 10))
    trend_line = (
        hist["y"]
        .rolling(
            window=window,
            center=True,
            min_periods=1,
        )
        .mean()
    )
    seasonality = hist["y"] - trend_line

    fig.add_trace(
        go.Scatter(
            x=seasonality.index,
            y=seasonality.values,
            name="Seasonality",
            line=dict(color="#ff9800", width=1.5),
            mode="lines",
            fill="tozeroy",
            fillcolor="rgba(255,152,0,0.1)",
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f9fafb",
        font=dict(color="#111827"),
        height=700,
        showlegend=True,
        margin=dict(l=60, r=30, t=60, b=50),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#e5e7eb")
    fig.update_yaxes(gridcolor="#e5e7eb")
    return fig


def _build_multi_horizon_fig(
    prophet_df: pd.DataFrame,
    forecasts: dict,
    ticker: str,
    current_price: float,
) -> go.Figure:
    """Build a multi-horizon overlay chart.

    Overlays 3-month, 6-month, and 9-month forecasts on
    a single chart so the user can compare how different
    horizons diverge.

    Args:
        prophet_df: Historical ``ds``/``y`` DataFrame.
        forecasts: Dict mapping horizon labels
            (``"3m"``, ``"6m"``, ``"9m"``) to forecast
            DataFrames with ``ds``, ``yhat``,
            ``yhat_lower``, ``yhat_upper``.
        ticker: Ticker symbol for the title.
        current_price: Most recent closing price.

    Returns:
        :class:`plotly.graph_objects.Figure` with
        overlaid forecast lines and bands.
    """
    sym = _get_currency(ticker)
    fig = go.Figure()

    # Historical price
    fig.add_trace(
        go.Scatter(
            x=prophet_df["ds"],
            y=prophet_df["y"],
            name="Historical Price",
            line=dict(color="#1e88e5", width=2),
            mode="lines",
        )
    )

    # Horizon-specific styles
    styles = {
        "3m": {
            "color": "#d97706",
            "fill": "rgba(217,119,6,0.10)",
        },
        "6m": {
            "color": "#ea580c",
            "fill": "rgba(234,88,12,0.08)",
        },
        "9m": {
            "color": "#dc2626",
            "fill": "rgba(220,38,38,0.06)",
        },
    }

    for label in ("3m", "6m", "9m"):
        fc = forecasts.get(label)
        if fc is None or fc.empty:
            continue
        s = styles[label]

        # Confidence band
        fig.add_trace(
            go.Scatter(
                x=pd.concat(
                    [fc["ds"], fc["ds"].iloc[::-1]],
                ),
                y=pd.concat(
                    [
                        fc["yhat_upper"],
                        fc["yhat_lower"].iloc[::-1],
                    ]
                ),
                fill="toself",
                fillcolor=s["fill"],
                line=dict(color="rgba(0,0,0,0)"),
                name=f"{label} Band",
                showlegend=False,
            )
        )

        # Forecast line
        fig.add_trace(
            go.Scatter(
                x=fc["ds"],
                y=fc["yhat"],
                name=f"{label} Forecast",
                line=dict(
                    color=s["color"],
                    width=2,
                    dash="dash",
                ),
                mode="lines",
            )
        )

    # Today marker
    today_ts = pd.Timestamp(date.today())
    fig.add_shape(
        type="line",
        x0=today_ts,
        x1=today_ts,
        y0=0,
        y1=1,
        xref="x",
        yref="paper",
        line=dict(
            color="rgba(0,0,0,0.35)",
            width=1.5,
            dash="dot",
        ),
    )
    fig.add_annotation(
        x=today_ts,
        y=1.02,
        yref="paper",
        text="Today",
        showarrow=False,
        font=dict(
            color="rgba(0,0,0,0.6)",
            size=10,
        ),
        xanchor="left",
    )

    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f9fafb",
        font=dict(color="#111827"),
        title=dict(
            text=(f"{ticker} — Multi-Horizon" " Forecast Comparison"),
            font=dict(size=16),
        ),
        height=550,
        showlegend=True,
        margin=dict(l=60, r=30, t=80, b=50),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#e5e7eb")
    fig.update_yaxes(gridcolor="#e5e7eb")
    return fig
