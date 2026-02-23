"""Interactive callback definitions for the AI Stock Analysis Dashboard.

All Dash callbacks are registered inside the :func:`register_callbacks`
factory, which accepts the :class:`~dash.Dash` application instance.
This pattern avoids circular imports between ``app.py`` and this module.

Data is read directly from ``data/raw/`` and ``data/forecasts/`` parquet
files.  The *Run New Analysis* button imports backend tool functions from
``backend/tools/`` (via the ``sys.path`` insertion done in ``app.py``) and
re-runs the full fetch → analysis → Prophet forecast pipeline without any
LLM involved.

Example::

    from dashboard.callbacks import register_callbacks
    register_callbacks(app)
"""

import json
import logging
import math
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import ta
from dash import Input, Output, State, ctx, no_update
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so backend modules are importable
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Path constants (mirror backend tool constants)
# ---------------------------------------------------------------------------

_DATA_RAW = _PROJECT_ROOT / "data" / "raw"
_DATA_FORECASTS = _PROJECT_ROOT / "data" / "forecasts"
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"
_REGISTRY_PATH = _DATA_METADATA / "stock_registry.json"

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private data-loading helpers
# ---------------------------------------------------------------------------


def _load_reg_cb() -> dict:
    """Load the stock registry for use inside callbacks.

    Returns:
        Registry dict; empty dict if missing or unreadable.
    """
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        with open(_REGISTRY_PATH) as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("registry load failed: %s", exc)
        return {}


def _load_raw(ticker: str) -> Optional[pd.DataFrame]:
    """Load the raw OHLCV parquet file for a ticker.

    Args:
        ticker: Uppercase ticker symbol (e.g. ``"AAPL"``).

    Returns:
        DataFrame with DatetimeIndex, or ``None`` if the file is absent.
    """
    path = _DATA_RAW / f"{ticker}_raw.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, engine="pyarrow")
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as exc:
        logger.error("Error loading %s: %s", path, exc)
        return None


def _load_forecast(ticker: str, horizon_months: int) -> Optional[pd.DataFrame]:
    """Find and load the best-matching forecast parquet for a ticker.

    Prefers an exact match for *horizon_months*; falls back to longer
    horizons (9m → 6m → 3m) so that a 9-month forecast can satisfy a
    6-month request.

    Args:
        ticker: Uppercase ticker symbol.
        horizon_months: Requested forecast horizon in months.

    Returns:
        DataFrame with ``ds``, ``yhat``, ``yhat_lower``, ``yhat_upper``
        columns, or ``None`` if no forecast file is found.
    """
    for h in [horizon_months, 9, 6, 3]:
        if h < horizon_months:
            continue
        path = _DATA_FORECASTS / f"{ticker}_{h}m_forecast.parquet"
        if path.exists():
            try:
                df = pd.read_parquet(path, engine="pyarrow")
                df["ds"] = pd.to_datetime(df["ds"])
                return df
            except Exception as exc:
                logger.error("Error loading forecast %s: %s", path, exc)
    return None


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate technical indicators and return an enriched DataFrame copy.

    Adds SMA_50, SMA_200, EMA_20, RSI_14, MACD, MACD_Signal, MACD_Hist,
    BB_Upper, BB_Middle, BB_Lower, and ATR_14 columns.

    Args:
        df: OHLCV DataFrame with ``Open``, ``High``, ``Low``, ``Close``
            columns and a DatetimeIndex.

    Returns:
        Copy of *df* with all indicator columns appended.
    """
    df = df.copy()
    close = df["Close"]
    df["SMA_50"]     = ta.trend.SMAIndicator(close=close, window=50).sma_indicator()
    df["SMA_200"]    = ta.trend.SMAIndicator(close=close, window=200).sma_indicator()
    df["EMA_20"]     = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
    df["RSI_14"]     = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    macd             = ta.trend.MACD(close=close)
    df["MACD"]       = macd.macd()
    df["MACD_Signal"]= macd.macd_signal()
    df["MACD_Hist"]  = macd.macd_diff()
    bb               = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    df["BB_Upper"]   = bb.bollinger_hband()
    df["BB_Middle"]  = bb.bollinger_mavg()
    df["BB_Lower"]   = bb.bollinger_lband()
    df["ATR_14"]     = ta.volatility.AverageTrueRange(
        high=df["High"], low=df["Low"], close=close, window=14
    ).average_true_range()
    return df


def _empty_fig(message: str, height: int = 400) -> go.Figure:
    """Return a dark-themed empty figure with a centred annotation.

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
        font=dict(size=15, color="rgba(255,255,255,0.4)"),
    )
    fig.update_layout(
        template="plotly_dark", height=height,
        xaxis={"visible": False}, yaxis={"visible": False},
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


# ---------------------------------------------------------------------------
# Chart-building helpers
# ---------------------------------------------------------------------------


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
        template="plotly_dark",
        height=800,
        showlegend=True,
        xaxis_rangeslider_visible=False,
        margin=dict(l=60, r=30, t=60, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_yaxes(title_text="Price", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="RSI",  row=2, col=1, range=[0, 100])
    fig.update_yaxes(title_text="MACD", row=3, col=1)
    return fig


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
    sentiment_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Neutral": "🟡"}.get(
        summary.get("sentiment", ""), ""
    )

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=prophet_df["ds"], y=prophet_df["y"],
        name="Historical Price",
        line=dict(color="#1e88e5", width=2),
        mode="lines",
    ))

    fig.add_trace(go.Scatter(
        x=pd.concat([forecast_df["ds"], forecast_df["ds"].iloc[::-1]]),
        y=pd.concat([forecast_df["yhat_upper"], forecast_df["yhat_lower"].iloc[::-1]]),
        fill="toself",
        fillcolor="rgba(76,175,80,0.15)",
        line=dict(color="rgba(0,0,0,0)"),
        name="80% Confidence Interval",
    ))

    fig.add_trace(go.Scatter(
        x=forecast_df["ds"], y=forecast_df["yhat"],
        name="Forecast",
        line=dict(color="#4caf50", width=2, dash="dash"),
        mode="lines",
    ))

    # Today vertical line (use add_shape — Plotly 6.x datetime workaround)
    today_ts = pd.Timestamp(date.today())
    fig.add_shape(
        type="line",
        x0=today_ts, x1=today_ts, y0=0, y1=1,
        xref="x", yref="paper",
        line=dict(color="rgba(255,255,255,0.5)", width=1.5, dash="dot"),
    )
    fig.add_annotation(
        x=today_ts, y=1.02, yref="paper",
        text="Today", showarrow=False,
        font=dict(color="rgba(255,255,255,0.7)", size=10), xanchor="left",
    )

    # Current-price horizontal line
    fig.add_shape(
        type="line",
        x0=prophet_df["ds"].min(), x1=forecast_df["ds"].max(),
        y0=current_price, y1=current_price,
        xref="x", yref="y",
        line=dict(color="rgba(255,255,255,0.35)", width=1, dash="dot"),
    )
    fig.add_annotation(
        x=forecast_df["ds"].max(), y=current_price,
        text=f"Current: ${current_price:.2f}",
        showarrow=False,
        font=dict(color="rgba(255,255,255,0.6)", size=10),
        xanchor="right", yanchor="bottom",
    )

    # Price-target annotations
    colors = {"3m": "#ffeb3b", "6m": "#ff9800", "9m": "#f44336"}
    for key, target in summary.get("targets", {}).items():
        sign = "+" if target["pct_change"] >= 0 else ""
        fig.add_annotation(
            x=target["date"], y=target["price"],
            text=f"{key}: ${target['price']}<br>{sign}{target['pct_change']:.1f}%",
            showarrow=True, arrowhead=2,
            arrowcolor=colors.get(key, "white"),
            font=dict(color=colors.get(key, "white"), size=11),
            bgcolor="rgba(0,0,0,0.5)",
            bordercolor=colors.get(key, "white"),
            borderwidth=1,
        )

    fig.update_layout(
        template="plotly_dark",
        title=dict(
            text=f"{ticker} — Price Forecast  {sentiment_emoji} {summary.get('sentiment','')}",
            font=dict(size=16),
        ),
        height=550,
        showlegend=True,
        margin=dict(l=60, r=30, t=80, b=50),
        hovermode="x unified",
    )
    return fig


# ---------------------------------------------------------------------------
# Stats / card helpers
# ---------------------------------------------------------------------------


def _build_stats_cards(df: pd.DataFrame, ticker: str):
    """Build a row of six summary-stat Bootstrap cards for the analysis page.

    Args:
        df: Full OHLCV DataFrame with indicator columns added.
        ticker: Ticker symbol (used for logging only).

    Returns:
        :class:`dash_bootstrap_components.Row` containing six stat cards.
    """
    import dash_bootstrap_components as dbc
    from dash import html

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
        (daily_returns.mean() * 252 - 0.04) / ann_vol_dec
        if ann_vol_dec > 0 else 0.0,
        2,
    )

    stats = [
        ("All-Time High",  f"${ath:,}",     "text-success"),
        ("All-Time Low",   f"${atl:,}",     "text-danger"),
        ("Annual Return",  f"{annual_ret:+.1f}%",
         "text-success" if annual_ret >= 0 else "text-danger"),
        ("Max Drawdown",   f"{max_dd:.1f}%",  "text-danger"),
        ("Volatility",     f"{ann_vol:.1f}%", "text-warning"),
        ("Sharpe Ratio",   str(sharpe),       "text-info"),
    ]

    cols = []
    for label, value, color_cls in stats:
        cols.append(dbc.Col(
            dbc.Card(dbc.CardBody([
                html.Small(label, className="text-muted d-block"),
                html.Span(value, className=f"fs-5 fw-bold {color_cls}"),
            ]), className="stat-card h-100"),
            xs=6, md=4, lg=2, className="mb-3",
        ))
    return dbc.Row(cols)


def _build_target_cards(summary: dict, current_price: float):
    """Build price-target cards for the forecast page.

    Args:
        summary: Dict produced by the forecast summary helper with a
            ``targets`` sub-dict keyed by ``"3m"``, ``"6m"``, ``"9m"``.
        current_price: Most recent closing price (for display).

    Returns:
        :class:`dash_bootstrap_components.Row` of price-target cards.
    """
    import dash_bootstrap_components as dbc
    from dash import html

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
        cols.append(dbc.Col(
            dbc.Card([
                dbc.CardHeader(
                    label_map[key],
                    className=f"text-center bg-transparent border-{color_map[key]}",
                ),
                dbc.CardBody([
                    html.H5(f"${t['price']:,}", className="text-center mb-1"),
                    html.P(
                        f"{sign}{t['pct_change']:.1f}%",
                        className=f"text-center fw-bold mb-1 {text_color}",
                    ),
                    html.Small(
                        f"${t['lower']:,} – ${t['upper']:,}",
                        className="text-muted d-block text-center",
                    ),
                ]),
            ], className=f"target-card border border-{color_map[key]}"),
            xs=12, sm=4, className="mb-3",
        ))

    return dbc.Row(cols)


def _build_accuracy_row(accuracy: dict):
    """Build the model-accuracy metric cards for the forecast page.

    Args:
        accuracy: Dict with ``MAE``, ``RMSE``, ``MAPE_pct`` keys (or
            ``"error"`` key if accuracy could not be computed).

    Returns:
        :class:`dash_bootstrap_components.Row` or an error paragraph.
    """
    import dash_bootstrap_components as dbc
    from dash import html

    if "error" in accuracy:
        return html.P(f"Accuracy: {accuracy['error']}", className="text-muted small")

    metrics = [
        ("MAE",  f"${accuracy['MAE']:,.2f}", "Mean Absolute Error"),
        ("RMSE", f"${accuracy['RMSE']:,.2f}", "Root Mean Square Error"),
        ("MAPE", f"{accuracy['MAPE_pct']:.1f}%", "Mean Abs % Error (lower = better)"),
    ]
    cols = [
        dbc.Col(
            dbc.Card(dbc.CardBody([
                html.Small(title, className="text-muted d-block"),
                html.Span(value, className="fs-5 fw-bold text-info"),
                html.Small(f" ({label})", className="text-muted"),
            ]), className="stat-card"),
            xs=12, sm=4, className="mb-3",
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
        f"{min(months, 9)}m" if f"{min(months, 9)}m" in targets
        else ("6m" if "6m" in targets else "3m")
    )
    final_pct = targets.get(last_key, {}).get("pct_change", 0.0)
    sentiment = "Bullish" if final_pct > 10 else ("Bearish" if final_pct < -10 else "Neutral")

    return {"ticker": ticker, "current_price": current_price, "targets": targets, "sentiment": sentiment}


# ---------------------------------------------------------------------------
# Public callback registration function
# ---------------------------------------------------------------------------


def register_callbacks(app) -> None:
    """Register all Dash callbacks with the application instance.

    Args:
        app: The :class:`~dash.Dash` application instance created in
            ``dashboard/app.py``.
    """
    import dash_bootstrap_components as dbc
    from dash import html

    # ======================================================================
    # Home page: refresh stock cards + populate registry dropdown
    # ======================================================================

    @app.callback(
        [
            Output("stock-cards-container", "children"),
            Output("home-registry-dropdown", "options"),
        ],
        [
            Input("registry-refresh", "n_intervals"),
            Input("url", "pathname"),
        ],
    )
    def refresh_stock_cards(n_intervals, pathname):
        """Rebuild stock cards from the registry on page load or interval tick.

        Args:
            n_intervals: Auto-refresh interval counter.
            pathname: Current URL path (cards only shown on home).

        Returns:
            Tuple of (list of card columns, dropdown options list).
        """
        registry = _load_reg_cb()
        if not registry:
            empty = [dbc.Col(
                html.P("No stocks saved yet. Analyse a stock via the chat interface first.",
                       className="text-muted"),
            )]
            return empty, []

        dropdown_options = [{"label": t, "value": t} for t in sorted(registry.keys())]
        cols = []

        for ticker, entry in sorted(registry.items()):
            last_updated = entry.get("last_fetch_date", "Unknown")

            # Current price + 10Y return from parquet
            current_price_str = "N/A"
            total_return_str  = "N/A"
            return_color_cls  = "text-muted"
            try:
                df = _load_raw(ticker)
                if df is not None and len(df) > 1:
                    cp = float(df["Close"].iloc[-1])
                    fp = float(df["Close"].iloc[0])
                    tr = (cp / fp - 1) * 100
                    current_price_str = f"${cp:,.2f}"
                    total_return_str  = f"{tr:+.1f}%"
                    return_color_cls  = "text-success" if tr >= 0 else "text-danger"
            except Exception as exc:
                logger.warning("Card data error for %s: %s", ticker, exc)

            # Sentiment from forecast parquet
            sentiment     = "Unknown"
            sent_color    = "secondary"
            sent_emoji    = "⚪"
            try:
                forecast_files = list(_DATA_FORECASTS.glob(f"{ticker}_*m_forecast.parquet"))
                if forecast_files:
                    latest = max(forecast_files, key=lambda p: p.stat().st_mtime)
                    fc_df  = pd.read_parquet(latest, engine="pyarrow")
                    df_raw = _load_raw(ticker)
                    if df_raw is not None and len(fc_df) > 0:
                        cp   = float(df_raw["Close"].iloc[-1])
                        fp   = float(fc_df["yhat"].iloc[-1])
                        pct  = (fp - cp) / cp * 100
                        if pct > 10:
                            sentiment, sent_color, sent_emoji = "Bullish",  "success", "🟢"
                        elif pct < -10:
                            sentiment, sent_color, sent_emoji = "Bearish",  "danger",  "🔴"
                        else:
                            sentiment, sent_color, sent_emoji = "Neutral",  "warning", "🟡"
            except Exception as exc:
                logger.warning("Sentiment error for %s: %s", ticker, exc)

            # Company name from metadata JSON if available
            company = ticker
            info_path = _DATA_METADATA / f"{ticker}_info.json"
            if info_path.exists():
                try:
                    with open(info_path) as fh:
                        info   = json.load(fh)
                        company = info.get("name", ticker) or ticker
                except Exception:
                    pass

            card = html.A(
                href=f"/analysis?ticker={ticker}",
                className="text-decoration-none",
                children=dbc.Card([
                    dbc.CardBody([
                        html.Div([
                            html.H5(ticker, className="card-title text-info mb-0"),
                            dbc.Badge(
                                f"{sent_emoji} {sentiment}",
                                color=sent_color,
                                className="ms-auto",
                            ),
                        ], className="d-flex justify-content-between align-items-center mb-1"),
                        html.P(company, className="card-subtitle text-muted small mb-3"),
                        html.Div([
                            html.Div([
                                html.Small("Price", className="text-muted d-block"),
                                html.Strong(current_price_str, className="text-white"),
                            ], className="me-3"),
                            html.Div([
                                html.Small("10Y Return", className="text-muted d-block"),
                                html.Strong(total_return_str, className=return_color_cls),
                            ], className="me-3"),
                            html.Div([
                                html.Small("Updated", className="text-muted d-block"),
                                html.Small(last_updated, className="text-muted"),
                            ]),
                        ], className="d-flex align-items-start"),
                    ]),
                ], className="stock-card h-100"),
            )
            cols.append(dbc.Col(card, xs=12, sm=6, md=4, lg=3, className="mb-4"))

        return cols, dropdown_options

    # ======================================================================
    # Home page: navigate to analysis page on search / dropdown select
    # ======================================================================

    @app.callback(
        [Output("url", "pathname"), Output("nav-ticker-store", "data")],
        [
            Input("search-btn", "n_clicks"),
            Input("home-registry-dropdown", "value"),
        ],
        [State("ticker-search-input", "value")],
        prevent_initial_call=True,
    )
    def navigate_to_analysis(search_clicks, dropdown_val, search_input):
        """Navigate to the analysis page when the user selects or searches a ticker.

        Args:
            search_clicks: Number of times the Analyse button was clicked.
            dropdown_val: Selected value from the home-page dropdown.
            search_input: Text entered in the ticker search input.

        Returns:
            Tuple of (new URL pathname, ticker to store for pre-selection).
        """
        triggered = ctx.triggered_id
        if triggered == "search-btn":
            if not search_input:
                return no_update, no_update
            return "/analysis", search_input.upper().strip()
        if triggered == "home-registry-dropdown" and dropdown_val:
            return "/analysis", dropdown_val
        return no_update, no_update

    # ======================================================================
    # Analysis page: sync dropdown from URL query param or nav store
    # ======================================================================

    @app.callback(
        Output("analysis-ticker-dropdown", "value"),
        [Input("url", "search"), Input("url", "pathname")],
        State("nav-ticker-store", "data"),
    )
    def sync_analysis_ticker(search, pathname, stored_ticker):
        """Pre-select the analysis dropdown when navigating from a stock card.

        Args:
            search: URL query string (e.g. ``"?ticker=AAPL"``).
            pathname: Current URL path.
            stored_ticker: Ticker stored via the home-page search or dropdown.

        Returns:
            Ticker string to set as dropdown value, or :data:`~dash.no_update`.
        """
        if pathname != "/analysis":
            return no_update
        if search:
            params = parse_qs(search.lstrip("?"))
            t = params.get("ticker", [None])[0]
            if t:
                return t.upper()
        if stored_ticker:
            return stored_ticker
        tickers = sorted(_load_reg_cb().keys())
        return tickers[0] if tickers else no_update

    # ======================================================================
    # Analysis page: update chart and stats when inputs change
    # ======================================================================

    @app.callback(
        [Output("analysis-chart", "figure"), Output("analysis-stats-row", "children")],
        [
            Input("analysis-ticker-dropdown", "value"),
            Input("date-range-slider", "value"),
            Input("overlay-toggles", "value"),
        ],
    )
    def update_analysis_chart(ticker, date_range_idx, overlays):
        """Rebuild the 3-panel analysis chart and summary-stat cards.

        Args:
            ticker: Selected ticker from the dropdown.
            date_range_idx: Integer index into the date-range map
                (0=1M, 1=3M, 2=6M, 3=1Y, 4=3Y, 5=Max).
            overlays: List of active overlay keys.

        Returns:
            Tuple of (analysis figure, stats row component).
        """
        if not ticker:
            return _empty_fig("Select a ticker to begin."), []

        df = _load_raw(ticker)
        if df is None:
            return _empty_fig(f"No data found for '{ticker}'. Fetch data via the chat interface."), []

        # Calculate indicators on full df (needs 200+ rows for SMA 200)
        df_full = _add_indicators(df)

        # Apply date-range filter
        n_map = {0: 21, 1: 63, 2: 126, 3: 252, 4: 756, 5: len(df_full)}
        n_days = n_map.get(date_range_idx if date_range_idx is not None else 5, len(df_full))
        df_plot = df_full.tail(n_days).copy()

        overlays = overlays or []
        fig = _build_analysis_fig(df_plot, ticker, overlays)
        stats = _build_stats_cards(df_full, ticker)
        return fig, stats

    # ======================================================================
    # Forecast page: sync dropdown from nav store
    # ======================================================================

    @app.callback(
        Output("forecast-ticker-dropdown", "value"),
        [Input("url", "search"), Input("url", "pathname")],
        State("nav-ticker-store", "data"),
    )
    def sync_forecast_ticker(search, pathname, stored_ticker):
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

    # ======================================================================
    # Forecast page: update chart when ticker / horizon / refresh changes
    # ======================================================================

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
    )
    def update_forecast_chart(ticker, horizon, refresh_trigger):
        """Reload and render the forecast chart when inputs change.

        Args:
            ticker: Selected ticker from the dropdown.
            horizon: Forecast horizon string (``"3"``, ``"6"``, ``"9"``).
            refresh_trigger: Counter incremented by the Run New Analysis
                callback to force a chart refresh.

        Returns:
            Tuple of (forecast figure, target-cards component,
            accuracy-row component).
        """
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
            "y":  df_raw[price_col].values,
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

        summary = _generate_forecast_summary_cb(forecast_df, current_price, ticker, horizon_months)
        fig = _build_forecast_fig(prophet_df, forecast_df, ticker, current_price, summary)

        target_cards  = _build_target_cards(summary, current_price)
        accuracy_note = [html.P(
            "Model accuracy metrics are computed when you click 'Run New Analysis'.",
            className="text-muted small",
        )]
        return fig, target_cards, accuracy_note

    # ======================================================================
    # Forecast page: Run New Analysis button
    # ======================================================================

    @app.callback(
        [
            Output("run-analysis-status", "children"),
            Output("forecast-refresh-store", "data"),
            Output("forecast-accuracy-row", "children"),
        ],
        Input("run-analysis-btn", "n_clicks"),
        [
            State("forecast-ticker-dropdown", "value"),
            State("forecast-horizon-radio", "value"),
            State("forecast-refresh-store", "data"),
        ],
        prevent_initial_call=True,
    )
    def run_new_analysis(n_clicks, ticker, horizon, current_refresh):
        """Run the full fetch → Prophet forecast pipeline for the selected ticker.

        Imports backend tool functions directly (no HTTP call to the backend
        API).  Increments the ``forecast-refresh-store`` counter on success
        to trigger a chart reload.

        Args:
            n_clicks: Button click counter.
            ticker: Selected ticker symbol.
            horizon: Forecast horizon string.
            current_refresh: Current store value (incremented on success).

        Returns:
            Tuple of (status message, new refresh counter,
            accuracy-row component).
        """
        if not ticker:
            return (
                dbc.Alert("Please select a ticker first.", color="warning"),
                no_update, [],
            )

        horizon_months = int(horizon) if horizon else 9
        ticker = ticker.upper().strip()

        try:
            # ── Step 1: Fetch / delta-update price data ────────────────────
            from backend.tools.stock_data_tool import fetch_stock_data
            fetch_result = fetch_stock_data.invoke({"ticker": ticker})
            logger.info("fetch_stock_data result: %s", fetch_result[:80])

            # ── Step 2: Run Prophet forecast pipeline ──────────────────────
            from backend.tools.forecasting_tool import (
                _load_parquet as _ft_load,
                _prepare_data_for_prophet,
                _train_prophet_model,
                _generate_forecast,
                _calculate_forecast_accuracy,
                _generate_forecast_summary,
                _save_forecast,
            )

            df = _ft_load(ticker)
            if df is None:
                raise ValueError(f"No data loaded for {ticker} after fetch.")

            prophet_df = _prepare_data_for_prophet(df)
            current_price = float(prophet_df["y"].iloc[-1])

            logger.info("Training Prophet model for %s (%dm)…", ticker, horizon_months)
            model       = _train_prophet_model(prophet_df)
            forecast_df = _generate_forecast(model, prophet_df, horizon_months)
            accuracy    = _calculate_forecast_accuracy(model, prophet_df)
            _save_forecast(forecast_df, ticker, horizon_months)

            logger.info("New analysis complete for %s.", ticker)

            acc_row = _build_accuracy_row(accuracy)
            status  = dbc.Alert(
                f"Analysis complete for {ticker}. Forecast updated.",
                color="success", duration=5000,
            )
            return status, (current_refresh or 0) + 1, acc_row

        except Exception as exc:
            logger.error("run_new_analysis error: %s", exc, exc_info=True)
            return (
                dbc.Alert(f"Error: {exc}", color="danger"),
                no_update, [],
            )

    # ======================================================================
    # Compare page: update all three charts / table when selection changes
    # ======================================================================

    @app.callback(
        [
            Output("compare-perf-chart",       "figure"),
            Output("compare-metrics-container", "children"),
            Output("compare-heatmap",           "figure"),
        ],
        Input("compare-ticker-dropdown", "value"),
    )
    def update_compare(tickers):
        """Build the normalised performance chart, metrics table, and heatmap.

        Args:
            tickers: List of selected ticker symbols (2–5).

        Returns:
            Tuple of (performance figure, metrics table component,
            heatmap figure).
        """
        empty_perf = _empty_fig("Select 2–5 stocks to compare.", height=450)
        empty_heat = _empty_fig("", height=380)

        if not tickers or len(tickers) < 2:
            return empty_perf, html.P("Select at least 2 stocks.", className="text-muted"), empty_heat

        # ── Load data ─────────────────────────────────────────────────────
        dfs = {}
        for t in tickers[:5]:
            df = _load_raw(t)
            if df is not None and len(df) > 1:
                dfs[t] = df

        if len(dfs) < 2:
            return (
                _empty_fig("Could not load data for 2 or more selected stocks.", 450),
                html.P("Data unavailable.", className="text-muted"),
                empty_heat,
            )

        # ── Common start date ─────────────────────────────────────────────
        common_start = max(df.index.min() for df in dfs.values())
        aligned = {t: df[df.index >= common_start]["Close"] for t, df in dfs.items()}

        # ── Normalised performance chart ──────────────────────────────────
        perf_fig = go.Figure()
        final_values = {}
        for t, series in aligned.items():
            norm = (series / series.iloc[0]) * 100
            perf_fig.add_trace(go.Scatter(
                x=norm.index, y=norm, name=t, mode="lines", line=dict(width=2),
            ))
            final_values[t] = float(norm.iloc[-1])

        best_ticker = max(final_values, key=final_values.get)
        perf_fig.update_layout(
            template="plotly_dark", height=450,
            title=dict(text="Normalised Performance (Base = 100)", font=dict(size=15)),
            yaxis_title="Value (Base 100)",
            margin=dict(l=60, r=30, t=60, b=40),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=1, xanchor="right"),
        )

        # ── Metrics table ─────────────────────────────────────────────────
        rows = []
        for t in sorted(dfs.keys()):
            df = dfs[t]
            df_ind = _add_indicators(df)
            close = df["Close"]
            daily = close.pct_change().dropna()
            ann_vol   = daily.std() * math.sqrt(252)
            ann_ret   = daily.mean() * 252
            sharpe    = round((ann_ret - 0.04) / ann_vol if ann_vol > 0 else 0.0, 2)
            rm        = close.cummax()
            dd        = (close - rm) / rm
            max_dd    = round(float(dd.min() * 100), 2)
            rsi_val   = (
                round(float(df_ind["RSI_14"].iloc[-1]), 1)
                if "RSI_14" in df_ind.columns else "N/A"
            )
            macd_val = df_ind["MACD"].iloc[-1] if "MACD" in df_ind.columns else None
            msig_val = df_ind["MACD_Signal"].iloc[-1] if "MACD_Signal" in df_ind.columns else None
            macd_sig = (
                "Bullish" if (macd_val is not None and msig_val is not None and macd_val > msig_val)
                else "Bearish"
            )

            # 6-month forecast upside
            fc_df = _load_forecast(t, 6)
            if fc_df is not None and len(fc_df) > 0:
                cp         = float(close.iloc[-1])
                fp         = float(fc_df["yhat"].iloc[-1])
                fc_upside  = f"{(fp - cp)/cp*100:+.1f}%"
                fc_sent    = "Bullish" if (fp - cp)/cp*100 > 10 else ("Bearish" if (fp - cp)/cp*100 < -10 else "Neutral")
            else:
                fc_upside = "N/A"
                fc_sent   = "N/A"

            badge = "🏆 " if t == best_ticker else ""
            rows.append({
                "Ticker":      f"{badge}{t}",
                "Annual Ret":  f"{ann_ret*100:+.1f}%",
                "Volatility":  f"{ann_vol*100:.1f}%",
                "Sharpe":      str(sharpe),
                "Max Drawdown":f"{max_dd:.1f}%",
                "RSI":         str(rsi_val),
                "MACD":        macd_sig,
                "6M Upside":   fc_upside,
                "Sentiment":   fc_sent,
            })

        metrics_df   = pd.DataFrame(rows)
        header_cells = [html.Th(col, className="text-muted small") for col in metrics_df.columns]
        body_rows    = []
        for _, row in metrics_df.iterrows():
            cells = [html.Td(str(v), className="small") for v in row]
            body_rows.append(html.Tr(cells))

        table = dbc.Table(
            [html.Thead(html.Tr(header_cells)), html.Tbody(body_rows)],
            bordered=True, hover=True, responsive=True,
            className="table-dark table-sm mt-2",
        )

        # ── Correlation heatmap ────────────────────────────────────────────
        returns_dict = {t: aligned[t].pct_change().dropna() for t in aligned}
        corr = pd.DataFrame(returns_dict).corr()

        heat_fig = go.Figure(go.Heatmap(
            z=corr.values,
            x=list(corr.columns),
            y=list(corr.index),
            colorscale="RdBu",
            zmid=0,
            zmin=-1, zmax=1,
            text=[[f"{v:.2f}" for v in row] for row in corr.values],
            texttemplate="%{text}",
            showscale=True,
        ))
        heat_fig.update_layout(
            template="plotly_dark", height=380,
            margin=dict(l=60, r=10, t=40, b=40),
            title=dict(text="Daily Returns Correlation", font=dict(size=13)),
        )

        return perf_fig, table, heat_fig
