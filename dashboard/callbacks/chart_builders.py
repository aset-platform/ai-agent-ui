"""Analysis chart builders for the AI Stock Analysis Dashboard callbacks.

Contains helpers for building the empty placeholder figure and the 3-panel
interactive analysis chart (candlestick + RSI + MACD).

Example::

    from dashboard.callbacks.chart_builders import (
        _build_analysis_fig, _empty_fig,
    )
"""

from __future__ import annotations

import logging
from typing import List

import holidays as holidays_lib
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard.callbacks.data_loaders import (  # noqa: F401 — re-exported
    _add_indicators,
)

# Module-level logger — kept at module scope as a private constant.
_logger = logging.getLogger(__name__)


def _get_market_holidays(market: str, start_year: int, end_year: int) -> dict:
    """Return exchange holidays as ``{date: name}`` dict.

    Uses :func:`holidays.financial_holidays` with exchange
    codes ``XNSE`` (India) or ``XNYS`` (US).

    Args:
        market: ``"india"`` or ``"us"``.
        start_year: First calendar year to include.
        end_year: Last calendar year to include.

    Returns:
        Dict mapping :class:`datetime.date` to holiday name.
    """
    exchange = "XNSE" if market == "india" else "XNYS"
    try:
        return dict(
            holidays_lib.financial_holidays(
                exchange,
                years=range(start_year, end_year + 1),
            )
        )
    except Exception:
        _logger.debug("Could not load holidays for %s", exchange)
        return {}


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
        text=message,
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=15, color="rgba(0,0,0,0.4)"),
    )
    fig.update_layout(
        template="plotly_white",
        height=height,
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f9fafb",
        xaxis={"visible": False},
        yaxis={"visible": False},
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


def _build_analysis_fig(
    df: pd.DataFrame,
    ticker: str,
    overlays: List[str],
    div_df: pd.DataFrame | None = None,
    market: str = "us",
) -> go.Figure:
    """Build the 3-panel interactive analysis chart.

    Panel 1 (60 %): Candlestick + optional SMA 50 / SMA 200 /
    Bollinger Bands overlays + optional Volume bars on a
    secondary y-axis.  Optional holiday annotations and
    dividend markers.
    Panel 2 (20 %): RSI (14) with overbought/oversold zones.
    Panel 3 (20 %): MACD line, signal line, and histogram.

    Args:
        df: OHLCV DataFrame with indicator columns.
        ticker: Ticker symbol used in the chart title.
        overlays: Active overlay keys (``"sma50"``,
            ``"sma200"``, ``"bb"``, ``"volume"``,
            ``"holidays"``, ``"dividends"``).
        div_df: Dividend DataFrame with ``ex_date`` and
            ``dividend_amount`` columns, or ``None``.
        market: ``"india"`` or ``"us"`` — selects the
            exchange holiday calendar.

    Returns:
        :class:`plotly.graph_objects.Figure`.
    """
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.60, 0.20, 0.20],
        specs=[
            [{"secondary_y": True}],
            [{"secondary_y": False}],
            [{"secondary_y": False}],
        ],
        subplot_titles=(f"{ticker} — Price & Indicators", "RSI (14)", "MACD"),
    )

    # ── Panel 1: Candlestick ──────────────────────────────────────────────
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
        secondary_y=False,
    )

    if "sma50" in overlays and "SMA_50" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["SMA_50"],
                name="SMA 50",
                line=dict(color="orange", width=1.5),
            ),
            row=1,
            col=1,
            secondary_y=False,
        )

    if "sma200" in overlays and "SMA_200" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["SMA_200"],
                name="SMA 200",
                line=dict(color="tomato", width=1.5),
            ),
            row=1,
            col=1,
            secondary_y=False,
        )

    if "bb" in overlays and "BB_Upper" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["BB_Upper"],
                name="BB Upper",
                line=dict(color="rgba(100,149,237,0.7)", width=1, dash="dot"),
            ),
            row=1,
            col=1,
            secondary_y=False,
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
            secondary_y=False,
        )

    if "volume" in overlays and "Volume" in df.columns:
        # Fix #22: vectorised colour array — avoids Python loop over rows
        vol_colors = np.where(
            df["Close"] >= df["Open"], "#26a69a", "#ef5350"
        ).tolist()
        fig.add_trace(
            go.Bar(
                x=df.index,
                y=df["Volume"],
                name="Volume",
                marker_color=vol_colors,
                opacity=0.35,
                showlegend=True,
            ),
            row=1,
            col=1,
            secondary_y=True,
        )
        fig.update_yaxes(
            title_text="Volume",
            secondary_y=True,
            row=1,
            col=1,
            showgrid=False,
            tickfont=dict(size=9),
        )

    # ── Holiday annotations ──────────────────────────────────────────
    if "holidays" in overlays and len(df) > 0:
        start_yr = df.index.min().year
        end_yr = df.index.max().year
        hols = _get_market_holidays(market, start_yr, end_yr)
        chart_start = df.index.min()
        chart_end = df.index.max()
        for hol_date, hol_name in sorted(hols.items()):
            hol_dt = pd.Timestamp(hol_date)
            if hol_dt < chart_start or hol_dt > chart_end:
                continue
            # Skip weekends (exchange holidays on
            # weekdays only are relevant for charts)
            if hol_dt.weekday() >= 5:
                continue
            fig.add_vline(
                x=hol_dt,
                line_dash="dot",
                line_color="gray",
                opacity=0.35,
                row=1,
                col=1,
            )
            fig.add_annotation(
                x=hol_dt,
                y=1,
                yref="y domain",
                text=hol_name,
                textangle=-90,
                font=dict(size=8, color="gray"),
                showarrow=False,
                xanchor="left",
                yanchor="top",
                row=1,
                col=1,
            )

    # ── Dividend markers ─────────────────────────────────────
    if "dividends" in overlays and div_df is not None and not div_df.empty:
        chart_start = df.index.min()
        chart_end = df.index.max()
        div_x, div_y, div_text = [], [], []
        for _, row in div_df.iterrows():
            ex_dt = pd.Timestamp(row["ex_date"])
            if ex_dt < chart_start or ex_dt > chart_end:
                continue
            # Snap to nearest trading date via abs-diff
            # (avoids get_indexer Timestamp arithmetic bug
            # in pandas 2.x with freq-less DatetimeIndex)
            diffs = np.abs(df.index - ex_dt)
            nearest_idx = diffs.argmin()
            nearest_dt = df.index[nearest_idx]
            high_val = float(df.loc[nearest_dt, "High"])
            div_x.append(nearest_dt)
            div_y.append(high_val * 1.02)
            amt = row.get("dividend_amount", 0)
            div_text.append(
                f"Div: {amt:.2f}<br>" f"{ex_dt.strftime('%Y-%m-%d')}"
            )
        if div_x:
            fig.add_trace(
                go.Scatter(
                    x=div_x,
                    y=div_y,
                    mode="markers",
                    name="Dividends",
                    marker=dict(
                        symbol="diamond",
                        size=9,
                        color="#f59e0b",
                        line=dict(width=1, color="#d97706"),
                    ),
                    text=div_text,
                    hoverinfo="text",
                    showlegend=True,
                ),
                row=1,
                col=1,
                secondary_y=False,
            )

    # ── Panel 2: RSI ──────────────────────────────────────────────────────
    if "RSI_14" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["RSI_14"],
                name="RSI (14)",
                line=dict(color="#ab47bc", width=1.5),
            ),
            row=2,
            col=1,
        )
        # add_hline is safe here — y=70/30 are numeric, not datetime
        fig.add_hline(
            y=70,
            line_dash="dash",
            line_color="tomato",
            line_width=1,
            row=2,
            col=1,
        )
        fig.add_hline(
            y=30,
            line_dash="dash",
            line_color="#26a69a",
            line_width=1,
            row=2,
            col=1,
        )
        fig.add_hrect(
            y0=70,
            y1=100,
            fillcolor="tomato",
            opacity=0.07,
            line_width=0,
            row=2,
            col=1,
        )
        fig.add_hrect(
            y0=0,
            y1=30,
            fillcolor="#26a69a",
            opacity=0.07,
            line_width=0,
            row=2,
            col=1,
        )

    # ── Panel 3: MACD ─────────────────────────────────────────────────────
    if "MACD" in df.columns and "MACD_Signal" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["MACD"],
                name="MACD",
                line=dict(color="#1e88e5", width=1.5),
            ),
            row=3,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["MACD_Signal"],
                name="MACD Signal",
                line=dict(color="#e53935", width=1.5),
            ),
            row=3,
            col=1,
        )
        if "MACD_Hist" in df.columns:
            # Fix #22: vectorised colour array
            macd_colors = np.where(
                df["MACD_Hist"].fillna(0) >= 0, "#26a69a", "#ef5350"
            ).tolist()
            fig.add_trace(
                go.Bar(
                    x=df.index,
                    y=df["MACD_Hist"],
                    name="Histogram",
                    marker_color=macd_colors,
                    showlegend=False,
                ),
                row=3,
                col=1,
            )

    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f9fafb",
        font=dict(color="#111827"),
        height=800,
        showlegend=True,
        xaxis_rangeslider_visible=False,
        margin=dict(l=60, r=30, t=60, b=30),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
        ),
    )
    # Add hover-tooltips to RSI / MACD subplot titles.
    # make_subplots stores subplot_titles as annotations.
    _panel_tips = {
        "RSI (14)": (
            "Momentum oscillator (0\u2013100). "
            "\u226570 overbought, \u226430 oversold."
        ),
        "MACD": (
            "Trend momentum indicator. Bullish"
            " when MACD \u2265 signal line."
        ),
    }
    for ann in fig.layout.annotations:
        tip = _panel_tips.get(ann.text)
        if tip:
            ann.hovertext = tip
            ann.captureevents = True

    fig.update_xaxes(gridcolor="#e5e7eb")
    fig.update_yaxes(
        gridcolor="#e5e7eb",
        title_text="Price",
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.update_yaxes(
        gridcolor="#e5e7eb",
        title_text="RSI",
        row=2,
        col=1,
        range=[0, 100],
    )
    fig.update_yaxes(
        gridcolor="#e5e7eb",
        title_text="MACD",
        row=3,
        col=1,
    )
    return fig
