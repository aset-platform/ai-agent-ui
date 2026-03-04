"""Forecast chart builder for the AI Stock Analysis Dashboard callbacks.

Contains the :func:`_build_forecast_fig` helper that renders the Prophet
forecast chart with historical data, confidence intervals, price-target
annotations, and a today-marker.

Example::

    from dashboard.callbacks.chart_builders2 import _build_forecast_fig
"""

import logging
from datetime import date

import pandas as pd
import plotly.graph_objects as go

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
                [forecast_df["yhat_upper"], forecast_df["yhat_lower"].iloc[::-1]]
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
            text=f"{key}: {sym}{target['price']}<br>{sign}{target['pct_change']:.1f}%",
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
            text=f"{ticker} — Price Forecast  {sentiment_emoji} {summary.get('sentiment','')}",
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
