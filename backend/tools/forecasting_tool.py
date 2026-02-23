"""Prophet-based price forecasting tools for the Stock Analysis Agent.

This module provides private helper functions for preparing data, training
a Meta Prophet time-series model, generating price forecasts, evaluating
accuracy, and producing interactive Plotly forecast charts, plus one public
LangChain ``@tool`` function that orchestrates the full forecasting pipeline.

All analysis reads from locally stored parquet files written by
:mod:`tools.stock_data_tool`. Forecast results are persisted to
``data/forecasts/{TICKER}_{N}m_forecast.parquet``. An interactive Plotly
HTML chart is saved to ``charts/forecasts/{TICKER}_forecast.html``.

**Prophet configuration:**

- Yearly and weekly seasonality enabled; daily seasonality disabled.
- US federal holidays added as special events.
- Accuracy is evaluated by in-sample backtesting over the last 12 months
  (MAE, RMSE, MAPE).

Typical usage (via LangChain tool call)::

    from tools.forecasting_tool import forecast_stock

    result = forecast_stock.invoke({"ticker": "AAPL", "months": 9})
"""

import logging
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import holidays as holidays_lib
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from prophet import Prophet
from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_RAW = _PROJECT_ROOT / "data" / "raw"
_DATA_FORECASTS = _PROJECT_ROOT / "data" / "forecasts"
_CHARTS_FORECASTS = _PROJECT_ROOT / "charts" / "forecasts"

# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _load_parquet(ticker: str) -> Optional[pd.DataFrame]:
    """Load the raw OHLCV parquet file for a ticker.

    Args:
        ticker: Stock ticker symbol (already uppercased).

    Returns:
        A :class:`pandas.DataFrame` with a DatetimeIndex, or ``None`` if
        the parquet file does not exist.
    """
    file_path = _DATA_RAW / f"{ticker}_raw.parquet"
    if not file_path.exists():
        logger.warning("Parquet file not found for %s", ticker)
        return None
    df = pd.read_parquet(file_path, engine="pyarrow")
    df.index = pd.to_datetime(df.index)
    return df


def _build_holidays_df(years: range) -> pd.DataFrame:
    """Build a Prophet-compatible holidays DataFrame for US federal holidays.

    Args:
        years: Range of calendar years to include.

    Returns:
        DataFrame with columns ``holiday`` (str) and ``ds``
        (:class:`pandas.Timestamp`), ready to pass to :class:`Prophet`.
    """
    us_hols = holidays_lib.country_holidays("US", years=list(years))
    rows = [
        {"holiday": name, "ds": pd.Timestamp(dt)}
        for dt, name in us_hols.items()
    ]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["holiday", "ds"])


def _prepare_data_for_prophet(df: pd.DataFrame) -> pd.DataFrame:
    """Convert an OHLCV DataFrame to Prophet's ``ds`` / ``y`` format.

    Uses the ``Adj Close`` column when available, falling back to ``Close``.
    Any rows with NaN prices are dropped.

    Args:
        df: OHLCV DataFrame with a DatetimeIndex.

    Returns:
        DataFrame with exactly two columns: ``ds`` (datetime) and ``y``
        (adjusted close price), sorted ascending, with no NaN values.
    """
    price_col = "Adj Close" if "Adj Close" in df.columns else "Close"
    prophet_df = pd.DataFrame({
        "ds": df.index.normalize(),
        "y": df[price_col].values,
    })
    prophet_df = prophet_df.dropna(subset=["y"])
    prophet_df = prophet_df.sort_values("ds").reset_index(drop=True)
    # Remove timezone info if present
    prophet_df["ds"] = pd.to_datetime(prophet_df["ds"]).dt.tz_localize(None)
    logger.debug("Prophet data prepared: %d rows", len(prophet_df))
    return prophet_df


def _train_prophet_model(prophet_df: pd.DataFrame) -> Prophet:
    """Fit a Prophet model on the prepared price data.

    Enables yearly and weekly seasonality, disables daily seasonality, and
    adds US federal holidays as special events.

    Args:
        prophet_df: DataFrame in Prophet format (``ds``, ``y``) as returned
            by :func:`_prepare_data_for_prophet`.

    Returns:
        A fitted :class:`~prophet.Prophet` model instance.
    """
    year_start = int(prophet_df["ds"].dt.year.min())
    year_end = int(prophet_df["ds"].dt.year.max()) + 2
    hols = _build_holidays_df(range(year_start, year_end))

    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
        holidays=hols if not hols.empty else None,
        interval_width=0.80,
    )
    model.fit(prophet_df)
    logger.info("Prophet model fitted on %d rows", len(prophet_df))
    return model


def _generate_forecast(
    model: Prophet, prophet_df: pd.DataFrame, months: int
) -> pd.DataFrame:
    """Generate a price forecast for a given number of months ahead.

    Args:
        model: A fitted :class:`~prophet.Prophet` model.
        prophet_df: The training data in Prophet format.
        months: Number of months to forecast into the future.

    Returns:
        DataFrame of **future-only** rows with columns ``ds``,
        ``yhat``, ``yhat_lower``, ``yhat_upper``.
    """
    periods = int(months * 30)
    future = model.make_future_dataframe(periods=periods, freq="D")
    forecast = model.predict(future)

    # Keep only future dates (after the last training date)
    last_date = prophet_df["ds"].max()
    future_mask = forecast["ds"] > last_date
    result = forecast.loc[future_mask, ["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    result = result.reset_index(drop=True)
    logger.debug("Forecast generated: %d future rows", len(result))
    return result


def _calculate_forecast_accuracy(
    model: Prophet, prophet_df: pd.DataFrame
) -> dict:
    """Evaluate model accuracy via in-sample backtesting on the last 12 months.

    Generates in-sample predictions for the entire training period, then
    computes MAE, RMSE, and MAPE over the most recent 12 months.

    Args:
        model: A fitted :class:`~prophet.Prophet` model.
        prophet_df: The training data in Prophet format.

    Returns:
        Dictionary with keys ``MAE``, ``RMSE``, ``MAPE_pct`` (all floats),
        or ``{"error": <message>}`` if there is insufficient data.
    """
    cutoff = prophet_df["ds"].max() - pd.DateOffset(months=12)
    recent_actual = prophet_df[prophet_df["ds"] > cutoff].copy()

    if len(recent_actual) < 10:
        return {"error": "Insufficient data for 12-month backtest."}

    in_sample = model.predict(prophet_df[["ds"]])
    recent_pred = in_sample[in_sample["ds"] > cutoff][["ds", "yhat"]]
    merged = recent_actual.merge(recent_pred, on="ds", how="inner")

    if merged.empty:
        return {"error": "Could not align predictions with actuals."}

    errors = (merged["y"] - merged["yhat"]).abs()
    mae = float(errors.mean())
    rmse = float(math.sqrt(((merged["y"] - merged["yhat"]) ** 2).mean()))
    mape = float((errors / merged["y"]).mean() * 100)

    return {
        "MAE": round(mae, 2),
        "RMSE": round(rmse, 2),
        "MAPE_pct": round(mape, 2),
    }


def _save_forecast(
    forecast_df: pd.DataFrame, ticker: str, months: int
) -> str:
    """Save forecast results to a parquet file.

    Args:
        forecast_df: Future-only forecast DataFrame with columns ``ds``,
            ``yhat``, ``yhat_lower``, ``yhat_upper``.
        ticker: Stock ticker symbol (already uppercased).
        months: Forecast horizon in months (used in the filename).

    Returns:
        Absolute path to the saved parquet file as a string.
    """
    _DATA_FORECASTS.mkdir(parents=True, exist_ok=True)
    out_path = _DATA_FORECASTS / f"{ticker}_{months}m_forecast.parquet"
    forecast_df.to_parquet(str(out_path), engine="pyarrow", index=False)
    logger.info("Forecast saved: %s", out_path)
    return str(out_path)


def _generate_forecast_summary(
    forecast_df: pd.DataFrame, current_price: float, ticker: str, months: int
) -> dict:
    """Extract price targets at 3, 6, and 9 month marks and determine sentiment.

    Args:
        forecast_df: Future-only forecast DataFrame (``ds``, ``yhat``,
            ``yhat_lower``, ``yhat_upper``).
        current_price: The most recent closing price.
        ticker: Stock ticker symbol.
        months: Total forecast horizon (determines which targets are shown).

    Returns:
        Dictionary with price targets, percentage changes, confidence
        bounds, and an overall sentiment string (``"Bullish"``,
        ``"Bearish"``, or ``"Neutral"``).
    """
    today = pd.Timestamp(date.today())
    targets = {}

    for m in [3, 6, 9]:
        if m > months:
            continue
        target_date = today + pd.DateOffset(months=m)
        # Find the closest available forecast date
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

    # Sentiment based on the longest available horizon
    last_key = f"{min(months, 9)}m" if f"{min(months, 9)}m" in targets else (
        "6m" if "6m" in targets else "3m"
    )
    final_pct = targets.get(last_key, {}).get("pct_change", 0.0)
    if final_pct > 10:
        sentiment = "Bullish"
    elif final_pct < -10:
        sentiment = "Bearish"
    else:
        sentiment = "Neutral"

    return {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "targets": targets,
        "sentiment": sentiment,
    }


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
        summary: Output of :func:`_generate_forecast_summary`.

    Returns:
        Absolute path to the saved HTML chart file as a string.
    """
    _CHARTS_FORECASTS.mkdir(parents=True, exist_ok=True)

    fig = go.Figure()

    # Historical price
    fig.add_trace(go.Scatter(
        x=prophet_df["ds"],
        y=prophet_df["y"],
        name="Historical Price",
        line=dict(color="#1e88e5", width=2),
        mode="lines",
    ))

    # Confidence interval (shaded)
    fig.add_trace(go.Scatter(
        x=pd.concat([forecast_df["ds"], forecast_df["ds"].iloc[::-1]]),
        y=pd.concat([forecast_df["yhat_upper"], forecast_df["yhat_lower"].iloc[::-1]]),
        fill="toself",
        fillcolor="rgba(76,175,80,0.15)",
        line=dict(color="rgba(0,0,0,0)"),
        name="80% Confidence Interval",
        showlegend=True,
    ))

    # Forecast line
    fig.add_trace(go.Scatter(
        x=forecast_df["ds"],
        y=forecast_df["yhat"],
        name="Forecast",
        line=dict(color="#4caf50", width=2, dash="dash"),
        mode="lines",
    ))

    # Today vertical line — use add_shape for Plotly 6.x datetime compat
    today_ts = pd.Timestamp(date.today())
    fig.add_shape(
        type="line",
        x0=today_ts, x1=today_ts,
        y0=0, y1=1,
        xref="x", yref="paper",
        line=dict(color="rgba(255,255,255,0.5)", width=1.5, dash="dot"),
    )
    fig.add_annotation(
        x=today_ts,
        y=1.01,
        yref="paper",
        text="Today",
        showarrow=False,
        font=dict(color="rgba(255,255,255,0.7)", size=10),
        xanchor="left",
    )

    # Current price horizontal line
    y_min = float(prophet_df["y"].min())
    y_max = float(forecast_df["yhat_upper"].max())
    fig.add_shape(
        type="line",
        x0=prophet_df["ds"].min(), x1=forecast_df["ds"].max(),
        y0=current_price, y1=current_price,
        xref="x", yref="y",
        line=dict(color="rgba(255,255,255,0.4)", width=1, dash="dot"),
    )
    fig.add_annotation(
        x=forecast_df["ds"].max(),
        y=current_price,
        text=f"Current: ${current_price:.2f}",
        showarrow=False,
        font=dict(color="rgba(255,255,255,0.6)", size=10),
        xanchor="right",
        yanchor="bottom",
    )

    # Price target annotations
    colors = {"3m": "#ffeb3b", "6m": "#ff9800", "9m": "#f44336"}
    for key, target in summary.get("targets", {}).items():
        sign = "+" if target["pct_change"] >= 0 else ""
        fig.add_annotation(
            x=target["date"],
            y=target["price"],
            text=f"{key}: ${target['price']}<br>{sign}{target['pct_change']:.1f}%",
            showarrow=True,
            arrowhead=2,
            arrowcolor=colors.get(key, "white"),
            font=dict(color=colors.get(key, "white"), size=11),
            bgcolor="rgba(0,0,0,0.5)",
            bordercolor=colors.get(key, "white"),
            borderwidth=1,
        )

    sentiment_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Neutral": "🟡"}.get(
        summary["sentiment"], ""
    )

    fig.update_layout(
        template="plotly_dark",
        title=dict(
            text=(
                f"{ticker} — Price Forecast | "
                f"{sentiment_emoji} {summary['sentiment']}"
            ),
            font=dict(size=16),
        ),
        xaxis_title="Date",
        yaxis_title="Price (USD)",
        height=600,
        showlegend=True,
        margin=dict(l=60, r=30, t=80, b=50),
        hovermode="x unified",
    )

    out_path = _CHARTS_FORECASTS / f"{ticker}_forecast.html"
    fig.write_html(str(out_path))
    logger.info("Forecast chart saved: %s", out_path)
    return str(out_path)


# ---------------------------------------------------------------------------
# Public @tool function
# ---------------------------------------------------------------------------


@tool
def forecast_stock(ticker: str, months: int = 9) -> str:
    """Forecast the stock price using Meta Prophet and generate a chart.

    Loads locally stored OHLCV data, trains a Prophet model with yearly
    and weekly seasonality and US market holidays, generates a price
    forecast for the requested horizon, evaluates accuracy via 12-month
    in-sample backtesting, and saves both the forecast (parquet) and an
    interactive Plotly chart.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``. Data must already be
            fetched via :func:`fetch_stock_data` before calling this tool.
        months: Forecast horizon in months. Targets are shown at 3, 6, and
            9 months (whichever fall within the horizon). Defaults to ``9``.

    Returns:
        A formatted string report with price targets, confidence bounds,
        sentiment, model accuracy, and the chart file path. Returns an
        error string if data is unavailable or the model fails.

    Example:
        >>> result = forecast_stock.invoke({"ticker": "AAPL", "months": 9})
        >>> "AAPL" in result
        True
    """
    ticker = ticker.upper().strip()
    months = max(1, int(months))
    logger.info("forecast_stock | ticker=%s | months=%d", ticker, months)

    try:
        df = _load_parquet(ticker)
        if df is None:
            return (
                f"No local data found for '{ticker}'. "
                "Please run fetch_stock_data first."
            )

        prophet_df = _prepare_data_for_prophet(df)
        current_price = float(prophet_df["y"].iloc[-1])

        logger.info("Training Prophet model for %s...", ticker)
        model = _train_prophet_model(prophet_df)

        forecast_df = _generate_forecast(model, prophet_df, months)
        accuracy = _calculate_forecast_accuracy(model, prophet_df)
        summary = _generate_forecast_summary(forecast_df, current_price, ticker, months)

        forecast_path = _save_forecast(forecast_df, ticker, months)
        chart_path = _create_forecast_chart(
            model, forecast_df, prophet_df, ticker, current_price, summary
        )

        # ── Format report ─────────────────────────────────────────────────
        sentiment_emoji = {
            "Bullish": "🟢 BULLISH",
            "Bearish": "🔴 BEARISH",
            "Neutral": "🟡 NEUTRAL",
        }.get(summary["sentiment"], summary["sentiment"])

        target_lines = []
        for key in ["3m", "6m", "9m"]:
            t = summary["targets"].get(key)
            if t:
                sign = "+" if t["pct_change"] >= 0 else ""
                target_lines.append(
                    f"  {key.upper()} Target  : ${t['price']} "
                    f"({sign}{t['pct_change']:.1f}%) "
                    f"[${t['lower']} – ${t['upper']}]"
                )

        if "error" in accuracy:
            acc_line = f"  Accuracy        : {accuracy['error']}"
        else:
            acc_line = (
                f"  MAE             : ${accuracy['MAE']}\n"
                f"  RMSE            : ${accuracy['RMSE']}\n"
                f"  MAPE            : {accuracy['MAPE_pct']:.1f}%"
            )

        report = (
            f"=== PRICE FORECAST: {ticker} ({months}-month horizon) ===\n\n"
            f"CURRENT PRICE     : ${current_price:.2f}\n\n"
            f"PRICE TARGETS\n"
            + "\n".join(target_lines)
            + f"\n\nSENTIMENT         : {sentiment_emoji}\n\n"
            f"MODEL ACCURACY (last 12 months in-sample)\n"
            f"{acc_line}\n\n"
            f"FILES\n"
            f"  Forecast data   : {forecast_path}\n"
            f"  Chart           : {chart_path}\n"
        )

        logger.info("forecast_stock complete for %s", ticker)
        return report

    except Exception as e:
        logger.error(
            "forecast_stock failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error forecasting '{ticker}': {e}"
