dashboard/callbacks/chart_builders.py
"""Analysis chart builders for the AI Stock Analysis Dashboard callbacks.

Contains helpers for building the empty placeholder figure and the 3-panel
interactive analysis chart (candlestick + RSI + MACD).

Example::

    from dashboard.callbacks.chart_builders import _build_analysis_fig, _empty_fig
"""

import logging
from typing import List

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard.callbacks.data_loaders import _add_indicators  # noqa: F401 — re-exported

import pandas as pd

# Module-level logger — kept at module scope as a private constant.
_logger = logging.getLogger(__name__)


def _empty_fig(message: str, height: int = 400) -> go.Figure:
    """Return a light-themed empty figure with a centred annotation.

    Args:
        message: Text to display in the empty chart area.
        height: Chart height in pixels.

    Returns:
        :class:`plotly.graph_objects.Figure` with the annotation.
    """
    fig = go.Figure()
    fig.add_annotation(
        text=message, xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=15, color="rgba(0,0,0,0.4)"),
    )
    fig.update_layout(
        template="plotly_white", height=height,
        paper_bgcolor="#ffffff", plot_bgcolor="#f9fafb",
        xaxis={"visible": False}, yaxis={"visible": False},
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


def _build_analysis_fig(
    df: pd.DataFrame,
    ticker: str,
    overlays: List[str],
) -> go.Figure:
    """Build the 3-panel interactive analysis chart.

    Panel 1 (60 %): Candlestick + optional SMA 50 / SMA 200 / Bollinger
    Bands overlays + optional Volume bars on a secondary y-axis.
    Panel 2 (20 %): RSI (14) with overbought/oversold zones.
    Panel 3 (20 %): MACD line, signal line, and histogram.

    Args:
        df: OHLCV DataFrame with indicator columns already added.
        ticker: Ticker symbol used in the chart title.
        overlays: List of active overlay keys
            (``"sma50"``, ``"sma200"``, ``"bb"``, ``"volume"``).

    Returns:
        :class:`plotly.graph_objects.Figure` for use in a :class:`dcc.Graph`.
    """
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.60, 0.20, 0.20],
        specs=[[{"secondary_y": True}], [{"secondary_y": False}], [{"secondary_y": False}]],
        subplot_titles=(f"{ticker} — Price & Indicators", "RSI (14)", "MACD"),
    )

    # ── Panel 1: Candlestick ──────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"],
        name="OHLC",
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
    ), row=1, col=1, secondary_y=False)

    if "sma50" in overlays and "SMA_50" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["SMA_50"], name="SMA 50",
            line=dict(color="orange", width=1.5),
        ), row=1, col=1, secondary_y=False)

    if "sma200" in overlays and "SMA_200" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["SMA_200"], name="SMA 200",
            line=dict(color="tomato", width=1.5),
        ), row=1, col=1, secondary_y=False)

    if "bb" in overlays and "BB_Upper" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["BB_Upper"], name="BB Upper",
            line=dict(color="rgba(100,149,237,0.7)", width=1, dash="dot"),
        ), row=1, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["BB_Lower"], name="BB Lower",
            line=dict(color="rgba(100,149,237,0.7)", width=1, dash="dot"),
            fill="tonexty", fillcolor="rgba(100,149,237,0.07)",
        ), row=1, col=1, secondary_y=False)

    if "volume" in overlays and "Volume" in df.columns:
        vol_colors = [
            "#26a69a" if df["Close"].iloc[i] >= df["Open"].iloc[i] else "#ef5350"
            for i in range(len(df))
        ]
        fig.add_trace(go.Bar(
            x=df.index, y=df["Volume"], name="Volume",
            marker_color=vol_colors, opacity=0.35, showlegend=True,
        ), row=1, col=1, secondary_y=True)
        fig.update_yaxes(
            title_text="Volume", secondary_y=True,
            row=1, col=1, showgrid=False, tickfont=dict(size=9),
        )

    # ── Panel 2: RSI ──────────────────────────────────────────────────────
    if "RSI_14" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["RSI_14"], name="RSI (14)",
            line=dict(color="#ab47bc", width=1.5),
        ), row=2, col=1)
        # add_hline is safe here — y=70/30 are numeric, not datetime
        fig.add_hline(y=70, line_dash="dash", line_color="tomato", line_width=1, row=2, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="#26a69a", line_width=1, row=2, col=1)
        fig.add_hrect(
            y0=70, y1=100, fillcolor="tomato", opacity=0.07,
            line_width=0, row=2, col=1,
        )
        fig.add_hrect(
            y0=0, y1=30, fillcolor="#26a69a", opacity=0.07,
            line_width=0, row=2, col=1,
        )

    # ── Panel 3: MACD ─────────────────────────────────────────────────────
    if "MACD" in df.columns and "MACD_Signal" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["MACD"], name="MACD",
            line=dict(color="#1e88e5", width=1.5),
        ), row=3, col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["MACD_Signal"], name="MACD Signal",
            line=dict(color="#e53935", width=1.5),
        ), row=3, col=1)
        if "MACD_Hist" in df.columns:
            hist_colors = [
                "#26a69a" if v >= 0 else "#ef5350"
                for v in df["MACD_Hist"].fillna(0)
            ]
            fig.add_trace(go.Bar(
                x=df.index, y=df["MACD_Hist"], name="Histogram",
                marker_color=hist_colors, showlegend=False,
            ), row=3, col=1)

    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f9fafb",
        font=dict(color="#111827"),
        height=800,
        showlegend=True,
        xaxis_rangeslider_visible=False,
        margin=dict(l=60, r=30, t=60, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(gridcolor="#e5e7eb")
    fig.update_yaxes(gridcolor="#e5e7eb", title_text="Price", row=1, col=1, secondary_y=False)
    fig.update_yaxes(gridcolor="#e5e7eb", title_text="RSI",  row=2, col=1, range=[0, 100])
    fig.update_yaxes(gridcolor="#e5e7eb", title_text="MACD", row=3, col=1)
    return fig