"""Forecast accuracy evaluation and summary statistics.

Functions
---------
- :func:`_calculate_forecast_accuracy` — MAE, RMSE, MAPE via 12-month backtest.
- :func:`_generate_forecast_summary` — price targets at 3/6/9 months + sentiment.
"""

import logging
import math
from datetime import date

import pandas as pd
from prophet import Prophet

# Module-level logger; prefixed with _ to signal internal use.
_logger = logging.getLogger(__name__)


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
