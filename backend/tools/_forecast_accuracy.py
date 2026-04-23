"""Forecast accuracy evaluation and summary statistics.

Functions
---------
- :func:`_calculate_forecast_accuracy` — MAE, RMSE, MAPE backtest.
- :func:`_generate_forecast_summary` — 3/6/9m price targets.
- :func:`compute_confidence_score` — weighted composite score (0-1).
- :func:`confidence_badge` — human-readable badge + reason string.
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


def compute_confidence_score(
    metrics: dict,
    data_completeness: float,
) -> tuple[float, dict]:
    """Compute a weighted composite confidence score (0-1).

    Combines five components with fixed weights:

    +-----------------------+--------+
    | Component             | Weight |
    +=======================+========+
    | direction             |  0.25  |
    | mase (via MAPE)       |  0.25  |
    | coverage              |  0.20  |
    | interval_width        |  0.15  |
    | data_completeness     |  0.15  |
    +-----------------------+--------+

    Args:
        metrics: Dict with optional keys
            ``directional_accuracy_pct``, ``MAPE_pct``,
            ``coverage``, ``interval_width_ratio``.
            Missing keys fall back to moderate defaults.
        data_completeness: Fraction of expected data
            points present, in [0, 1].

    Returns:
        Tuple of (score rounded to 4 d.p., components
        dict with each component rounded to 3 d.p.).
    """
    import math

    def _safe_get(d, key, default):
        """Get value from dict, treating None/NaN as default."""
        v = d.get(key)
        if v is None:
            return default
        try:
            if math.isnan(float(v)):
                return default
        except (TypeError, ValueError):
            return default
        return float(v)

    # If no accuracy metrics at all (no CV run), penalise
    # heavily — these forecasts are unvalidated.
    has_accuracy = (
        _safe_get(metrics, "MAPE_pct", None) is not None
        or _safe_get(metrics, "directional_accuracy_pct",
                     None) is not None
    )

    # --- direction component (weight 0.25) ---
    dir_acc = _safe_get(metrics, "directional_accuracy_pct",
                        50.0 if has_accuracy else 30.0)
    direction = max(0.0, min(1.0, (dir_acc - 30.0) / 50.0))

    # --- mase component (weight 0.25) ---
    mape = _safe_get(metrics, "MAPE_pct",
                     20.0 if has_accuracy else 50.0)
    mase_approx = min(mape / 20.0, 2.0)
    mase = max(0.0, min(1.0, 1.0 - mase_approx / 2.0))

    # --- coverage component (weight 0.20) ---
    cov = _safe_get(metrics, "coverage", 0.80)
    coverage = max(
        0.0, min(1.0, 1.0 - abs(cov - 0.80) * 5.0)
    )

    # --- interval width component (weight 0.15) ---
    iwr = _safe_get(metrics, "interval_width_ratio", 0.50)
    interval = max(0.0, min(1.0, 1.0 - min(iwr, 1.0)))

    # --- data completeness component (weight 0.15) ---
    dc = max(0.0, min(1.0, data_completeness))

    score = (
        0.25 * direction
        + 0.25 * mase
        + 0.20 * coverage
        + 0.15 * interval
        + 0.15 * dc
    )
    score = round(score, 4)

    components = {
        "direction": round(direction, 3),
        "mase": round(mase, 3),
        "coverage": round(coverage, 3),
        "interval": round(interval, 3),
        "data_completeness": round(dc, 3),
    }
    _logger.debug(
        "Confidence score=%.4f components=%s",
        score,
        components,
    )
    return score, components


def confidence_badge(
    score: float,
    components: dict,
) -> tuple[str, str]:
    """Map a confidence score to a human-readable badge.

    Args:
        score: Composite score from
            :func:`compute_confidence_score`.
        components: Component dict from the same call.

    Returns:
        Tuple of (badge_label, reason_string).
        ``reason_string`` is empty for High/Medium.
        For Low/Rejected it lists the weakest signals,
        e.g. ``"Low confidence: low directional
        accuracy, high forecast error"``.
    """
    # Collect weak-signal descriptions.
    issues: list[str] = []
    if components.get("direction", 1.0) < 0.40:
        issues.append("low directional accuracy")
    if components.get("mase", 1.0) < 0.40:
        issues.append("high forecast error")
    if components.get("coverage", 1.0) < 0.40:
        issues.append(
            "poor prediction interval coverage"
        )
    if components.get("data_completeness", 1.0) < 0.40:
        issues.append("limited data signals")

    if score >= 0.65:
        return "High", ""
    if score >= 0.40:
        return "Medium", ""

    reason = (
        ", ".join(issues) if issues else "overall low model fit"
    )

    if score >= 0.25:
        label = "Low"
    else:
        label = "Rejected"

    return label, f"{label} confidence: {reason}"
