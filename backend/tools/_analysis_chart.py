"""Plotly chart builder for price analysis.

Functions
---------
- :func:`_create_analysis_chart` — 3-panel candlestick/RSI chart.
"""

import logging

import pandas as pd
import plotly.graph_objects as go
import tools._analysis_shared as _sh
from plotly.subplots import make_subplots

# Module-level logger; kept at module scope as per logging conventions.
_logger = logging.getLogger(__name__)


def _create_analysis_chart(df: pd.DataFrame, ticker: str) -> str:
    """Build and save a 3-panel interactive Plotly analysis chart.

    Panel 1 (60 %): Candlestick with SMA 50, SMA 200, Bollinger Bands.
    Panel 2 (20 %): Volume bars coloured green/red by price direction.
    Panel 3 (20 %): RSI with overbought/oversold zones.

    Args:
        df: DataFrame with indicator columns added.
        ticker: Stock ticker symbol (used in chart title and filename).

    Returns:
        Absolute path to the saved HTML chart file as a string.
    """
    _sh._CHARTS_ANALYSIS.mkdir(parents=True, exist_ok=True)

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=(
            f"{ticker} — Price & Indicators",
            "Volume",
            "RSI (14)",
        ),
    )

    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="OHLC",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1,
        col=1,
    )

    if "SMA_50" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["SMA_50"],
                name="SMA 50",
                line=dict(color="orange", width=1.5),
            ),
            row=1,
            col=1,
        )
    if "SMA_200" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["SMA_200"],
                name="SMA 200",
                line=dict(color="tomato", width=1.5),
            ),
            row=1,
            col=1,
        )
    if "BB_Upper" in df.columns and "BB_Lower" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["BB_Upper"],
                name="BB Upper",
                line=dict(color="rgba(100,149,237,0.7)", width=1, dash="dot"),
                showlegend=True,
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["BB_Lower"],
                name="BB Lower",
                line=dict(color="rgba(100,149,237,0.7)", width=1, dash="dot"),
                fill="tonexty",
                fillcolor="rgba(100,149,237,0.07)",
            ),
            row=1,
            col=1,
        )

    vol_colors = [
        "#26a69a" if df["Close"].iloc[i] >= df["Open"].iloc[i] else "#ef5350"
        for i in range(len(df))
    ]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"],
            name="Volume",
            marker_color=vol_colors,
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    if "RSI_14" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["RSI_14"],
                name="RSI (14)",
                line=dict(color="#ab47bc", width=1.5),
            ),
            row=3,
            col=1,
        )
        fig.add_hline(
            y=70,
            line_dash="dash",
            line_color="tomato",
            line_width=1,
            row=3,
            col=1,
        )
        fig.add_hline(
            y=30,
            line_dash="dash",
            line_color="#26a69a",
            line_width=1,
            row=3,
            col=1,
        )
        fig.add_hrect(
            y0=70,
            y1=100,
            fillcolor="tomato",
            opacity=0.07,
            line_width=0,
            row=3,
            col=1,
        )
        fig.add_hrect(
            y0=0,
            y1=30,
            fillcolor="#26a69a",
            opacity=0.07,
            line_width=0,
            row=3,
            col=1,
        )

    fig.update_layout(
        template="plotly_dark",
        title=dict(text=f"{ticker} — Technical Analysis", font=dict(size=16)),
        height=900,
        showlegend=True,
        xaxis_rangeslider_visible=False,
        margin=dict(l=60, r=30, t=80, b=30),
    )
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_yaxes(title_text="RSI", row=3, col=1, range=[0, 100])

    out_path = _sh._CHARTS_ANALYSIS / f"{ticker}_analysis.html"
    fig.write_html(str(out_path))
    _logger.info("Analysis chart saved: %s", out_path)
    return str(out_path)
