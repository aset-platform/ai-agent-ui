"""Plotly chart builder for Prophet price forecasts.

Functions
---------
- :func:`_create_forecast_chart` — build and save an interactive forecast chart.
"""

import logging
from datetime import date

import pandas as pd
import plotly.graph_objects as go
from prophet import Prophet

import tools._forecast_shared as _sh

# Module-level logger; kept module-level as this is a module utility, not a class.
_logger = logging.getLogger(__name__)


def _create_forecast_chart(
    model: Prophet,
    forecast_df: pd.DataFrame,
    prophet_df: pd.DataFrame,
    ticker: str,
    current_price: float,
    summary: dict,
) -> str:
    """Build and save an interactive Plotly forecast chart.

    Plots historical price (solid blue), forecasted price (dashed green),
    confidence interval (light green fill), a vertical today marker, a
    horizontal current-price line, and price-target annotations at 3, 6,
    and 9 months.

    Args:
        model: Fitted Prophet model (unused directly; kept for API symmetry).
        forecast_df: Future-only forecast rows.
        prophet_df: Historical training data in Prophet format.
        ticker: Stock ticker symbol (title and filename).
        current_price: Most recent closing price.
        summary: Output of :func:`~tools._forecast_accuracy._generate_forecast_summary`.

    Returns:
        Absolute path to the saved HTML chart file as a string.
    """
    _sh._CHARTS_FORECASTS.mkdir(parents=True, exist_ok=True)
    currency_code = _sh._load_currency(ticker)
    sym = _sh._currency_symbol(currency_code)

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=prophet_df["ds"], y=prophet_df["y"], name="Historical Price",
        line=dict(color="#1e88e5", width=2), mode="lines",
    ))

    fig.add_trace(go.Scatter(
        x=pd.concat([forecast_df["ds"], forecast_df["ds"].iloc[::-1]]),
        y=pd.concat([forecast_df["yhat_upper"], forecast_df["yhat_lower"].iloc[::-1]]),
        fill="toself", fillcolor="rgba(76,175,80,0.15)",
        line=dict(color="rgba(0,0,0,0)"),
        name="80% Confidence Interval", showlegend=True,
    ))

    fig.add_trace(go.Scatter(
        x=forecast_df["ds"], y=forecast_df["yhat"], name="Forecast",
        line=dict(color="#4caf50", width=2, dash="dash"), mode="lines",
    ))

    today_ts = pd.Timestamp(date.today())
    fig.add_shape(
        type="line", x0=today_ts, x1=today_ts, y0=0, y1=1,
        xref="x", yref="paper",
        line=dict(color="rgba(255,255,255,0.5)", width=1.5, dash="dot"),
    )
    fig.add_annotation(
        x=today_ts, y=1.01, yref="paper", text="Today", showarrow=False,
        font=dict(color="rgba(255,255,255,0.7)", size=10), xanchor="left",
    )

    fig.add_shape(
        type="line",
        x0=prophet_df["ds"].min(), x1=forecast_df["ds"].max(),
        y0=current_price, y1=current_price, xref="x", yref="y",
        line=dict(color="rgba(255,255,255,0.4)", width=1, dash="dot"),
    )
    fig.add_annotation(
        x=forecast_df["ds"].max(), y=current_price,
        text="Current: {}{:.2f}".format(sym, current_price), showarrow=False,
        font=dict(color="rgba(255,255,255,0.6)", size=10),
        xanchor="right", yanchor="bottom",
    )

    colors = {"3m": "#ffeb3b", "6m": "#ff9800", "9m": "#f44336"}
    for key, target in summary.get("targets", {}).items():
        sign = "+" if target["pct_change"] >= 0 else ""
        fig.add_annotation(
            x=target["date"], y=target["price"],
            text="{}: {}{}<br>{}{:.1f}%".format(
                key, sym, target["price"], sign, target["pct_change"]
            ),
            showarrow=True, arrowhead=2,
            arrowcolor=colors.get(key, "white"),
            font=dict(color=colors.get(key, "white"), size=11),
            bgcolor="rgba(0,0,0,0.5)",
            bordercolor=colors.get(key, "white"), borderwidth=1,
        )

    sentiment_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Neutral": "🟡"}.get(
        summary["sentiment"], ""
    )

    fig.update_layout(
        template="plotly_dark",
        title=dict(
            text="{} \u2014 Price Forecast | {} {}".format(
                ticker, sentiment_emoji, summary["sentiment"]
            ),
            font=dict(size=16),
        ),
        xaxis_title="Date",
        yaxis_title="Price ({})".format(currency_code),
        height=600, showlegend=True,
        margin=dict(l=60, r=30, t=80, b=50),
        hovermode="x unified",
    )

    out_path = _sh._CHARTS_FORECASTS / "{}_forecast.html".format(ticker)
    fig.write_html(str(out_path))
    _logger.info("Forecast chart saved: %s", out_path)
    return str(out_path)