"""Forecast accuracy evaluation and summary statistics.

Functions
---------
- :func:`_calculate_forecast_accuracy` — MAE, RMSE, MAPE backtest.
- :func:`_generate_forecast_summary` — 3/6/9m price targets.
"""

import logging
import math
from datetime import date

import pandas as pd
from prophet import Prophet

# Module-level logger; prefixed with _ to signal internal use.
_logger = logging.getLogger(__name__)


def _calculate_forecast_accuracy(
    model: Prophet,
    prophet_df: pd.DataFrame,
) -> dict:
    """Evaluate model accuracy via cross-validation.

    Only called from background refresh jobs — never
    from live chat.  Takes ~2 min for large datasets.

    Args:
        model: A fitted :class:`~prophet.Prophet` model.
        prophet_df: The training data (``ds``, ``y``).

    Returns:
        Dictionary with keys ``MAE``, ``RMSE``,
        ``MAPE_pct`` (all floats), or
        ``{"error": <message>}`` if evaluation fails.
    """
    try:
        from prophet.diagnostics import (
            cross_validation,
            performance_metrics,
        )

        data_days = (prophet_df["ds"].max() - prophet_df["ds"].min()).days
        if data_days < 730:
            return {
                "error": (
                    f"Only {data_days} days data " f"(need 730+ for CV)."
                ),
            }

        # Cap CV input to last 10 years for consistent
        # evaluation across tickers.  Prophet trains on
        # full history; CV evaluates recent accuracy only.
        from datetime import timedelta

        _ten_yr = prophet_df["ds"].max() - timedelta(
            days=3650,
        )
        _cv_df = prophet_df[prophet_df["ds"] >= _ten_yr].copy()
        if len(_cv_df) < 730:
            _cv_df = prophet_df.copy()

        # Refit model on capped data for CV.
        from prophet import Prophet as _P

        _cv_model = _P(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=False,
            interval_width=0.80,
        )
        # Copy regressor definitions from original model.
        for reg_name in model.extra_regressors:
            _cv_model.add_regressor(reg_name)
        # Merge regressor columns from original history.
        _cv_train = _cv_df.copy()
        for reg_name in model.extra_regressors:
            if reg_name in model.history.columns:
                _reg_vals = model.history[["ds", reg_name]]
                _cv_train = _cv_train.merge(
                    _reg_vals,
                    on="ds",
                    how="left",
                )
                _cv_train[reg_name] = _cv_train[reg_name].ffill().bfill()
        _cv_model.fit(_cv_train)

        # parallel=None avoids nested process spawning.
        # With 5 outer ThreadPoolExecutor workers,
        # parallel="processes" would spawn 50+ sub-
        # processes on 10 cores, causing 2x contention.
        # Sequential CV within each thread is faster
        # overall (~17 min vs ~31 min for 748 tickers).
        df_cv = cross_validation(
            _cv_model,
            initial="730 days",
            period="90 days",
            horizon="90 days",
            parallel=None,
        )
        metrics = performance_metrics(df_cv)
        mae = float(metrics["mae"].mean())
        rmse = float(metrics["rmse"].mean())
        mape = float(metrics["mape"].mean() * 100)

        # Guard against NaN/inf from edge-case
        # numerics (e.g., zero close prices → MAPE
        # division by zero).
        if any(
            math.isnan(v) or math.isinf(v)
            for v in (mae, rmse, mape)
        ):
            return {
                "error": (
                    "Accuracy metrics could not be "
                    "computed (numerical instability)."
                ),
            }

        _logger.info(
            "Cross-validation: MAE=%.2f " "RMSE=%.2f MAPE=%.1f%%",
            mae,
            rmse,
            mape,
        )
        # Deduplicate backtest: keep last prediction
        # per date (multiple folds may predict same ds).
        bt = (
            df_cv[["ds", "yhat", "y"]]
            .groupby("ds")
            .last()
            .reset_index()
            .sort_values("ds")
        )

        # Extended accuracy metrics from backtest
        err_pct = (
            ((bt["yhat"] - bt["y"]) / bt["y"]).abs()
            * 100
        )
        max_err = float(err_pct.max())
        p50_err = float(err_pct.median())
        p90_err = float(err_pct.quantile(0.90))

        # Directional accuracy: % of times model
        # predicted same direction as actual movement
        actual_dir = bt["y"].diff().apply(
            lambda x: 1 if x > 0 else -1,
        )
        pred_dir = bt["yhat"].diff().apply(
            lambda x: 1 if x > 0 else -1,
        )
        valid = actual_dir.iloc[1:].reset_index(
            drop=True,
        )
        predicted = pred_dir.iloc[1:].reset_index(
            drop=True,
        )
        dir_acc = float(
            (valid == predicted).mean() * 100,
        )

        return {
            "MAE": round(mae, 2),
            "RMSE": round(rmse, 2),
            "MAPE_pct": round(mape, 2),
            "directional_accuracy_pct": round(
                dir_acc, 1,
            ),
            "max_error_pct": round(max_err, 1),
            "p50_error_pct": round(p50_err, 1),
            "p90_error_pct": round(p90_err, 1),
            "backtest_df": bt,
        }
    except Exception as exc:
        _logger.warning(
            "Cross-validation failed: %s",
            exc,
        )
        return {"error": str(exc)}


def _generate_forecast_summary(
    forecast_df: pd.DataFrame, current_price: float, ticker: str, months: int
) -> dict:
    """Extract price targets at 3/6/9 months and sentiment.

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
