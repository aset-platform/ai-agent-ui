"""XGBoost ensemble correction on top of Prophet forecasts.

Trains an XGBoost regressor on Prophet's **residuals** (where
Prophet was wrong) using technical indicators + macro + sentiment
as features.  The correction is added to Prophet's base forecast:

    final_price = prophet_yhat + xgb_residual_correction

Gated by ``config.ensemble_enabled``.  If training fails or
insufficient data is available, returns ``None`` (graceful
fallback to pure Prophet).
"""

import logging

import pandas as pd

_logger = logging.getLogger(__name__)

# Features used by XGBoost (order matters for consistency).
# Only features actually present in the merged DataFrame
# are used — missing columns are silently skipped.
_FEATURES = [
    "prophet_yhat",
    # Market regressors (Phase 2)
    "vix",
    "index_return",
    "sentiment",
    # Macro regressors (Phase 3a)
    "treasury_10y",
    "yield_spread",
    "oil_price",
    "dollar_index",
    # Analyst signals
    "analyst_bias",
    "eps_revision",
    # Technical indicators
    "sma_50",
    "sma_200",
    "rsi_14",
    "macd",
    "bb_upper",
    "bb_lower",
    "atr_14",
]

_MIN_TRAINING_ROWS = 200


def ensemble_forecast(
    model,
    train_df: pd.DataFrame,
    prophet_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
    ticker: str,
    regressors: pd.DataFrame | None = None,
) -> pd.DataFrame | None:
    """Apply XGBoost residual correction to a Prophet forecast.

    Args:
        model: Fitted Prophet model.
        train_df: Training DataFrame (``ds``, ``y``, +
            regressor columns) returned by
            ``_train_prophet_model()``.
        prophet_df: Original Prophet data (``ds``, ``y``).
        forecast_df: Future-only forecast from
            ``_generate_forecast()`` with ``ds``, ``yhat``,
            ``yhat_lower``, ``yhat_upper``.
        ticker: Stock ticker symbol.
        regressors: Regressor DataFrame (same as passed to
            Prophet) for aligning future features.

    Returns:
        Corrected ``forecast_df`` with adjusted ``yhat``,
        ``yhat_lower``, ``yhat_upper``, or ``None`` if
        ensemble training fails.
    """
    try:
        from xgboost import XGBRegressor

        # ── 1. Compute in-sample Prophet predictions ────
        in_sample = model.predict(train_df)
        work = train_df.copy()
        work["prophet_yhat"] = in_sample["yhat"].values
        work["residual"] = work["y"] - work["prophet_yhat"]

        # ── 2. Compute technical indicators on-the-fly ──
        from tools._analysis_shared import (
            compute_indicators,
        )

        tech = compute_indicators(ticker)

        if tech is None or tech.empty:
            _logger.info(
                "No tech indicators for %s, "
                "skipping ensemble",
                ticker,
            )
            return None

        # Align on date (indicators have DatetimeIndex).
        work["_date"] = pd.to_datetime(
            work["ds"],
        ).dt.date
        tech = tech.reset_index()
        # Normalize column names to lowercase so they
        # match the lowercase _FEATURES list.
        tech.columns = [c.lower() for c in tech.columns]
        tech["_date"] = pd.to_datetime(
            tech["date"],
        ).dt.date

        tech_cols = [
            c
            for c in [
                "sma_50",
                "sma_200",
                "rsi_14",
                "macd",
                "bb_upper",
                "bb_lower",
                "atr_14",
            ]
            if c in tech.columns
        ]

        work = work.merge(
            tech[["_date"] + tech_cols],
            on="_date",
            how="left",
        )

        # Drop rows with NaN (SMA_200 needs 200 rows).
        work = work.dropna(
            subset=["residual"] + tech_cols,
        )

        if len(work) < _MIN_TRAINING_ROWS:
            _logger.info(
                "Ensemble: only %d rows for %s " "(need %d), skipping",
                len(work),
                ticker,
                _MIN_TRAINING_ROWS,
            )
            return None

        # ── 3. Build feature matrix ────────────────────
        available = [f for f in _FEATURES if f in work.columns]
        if not available:
            return None

        X_train = work[available].astype(float)
        y_train = work["residual"].astype(float)

        # ── 4. Train XGBoost ───────────────────────────
        xgb_model = XGBRegressor(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=0,
        )
        xgb_model.fit(X_train, y_train)

        _logger.info(
            "XGBoost trained for %s: %d rows, " "%d features %s",
            ticker,
            len(X_train),
            len(available),
            available,
        )

        # ── 5. Build future feature matrix ─────────────
        future = forecast_df.copy()
        future["prophet_yhat"] = future["yhat"]

        # Merge regressors into future (already done
        # by _generate_forecast, but yhat column was
        # not named prophet_yhat).
        if regressors is not None:
            for col in regressors.columns:
                if col == "ds" or col in future.columns:
                    continue
                future = future.merge(
                    regressors[["ds", col]],
                    on="ds",
                    how="left",
                )
                future[col] = future[col].ffill().bfill()

        # Forward-fill last known tech indicator values.
        if tech_cols:
            last_tech = tech.sort_values(
                "_date",
            ).iloc[-1]
            for col in tech_cols:
                if col not in future.columns:
                    future[col] = float(
                        last_tech.get(col, 0),
                    )

        # Ensure all training features exist in future.
        # Missing columns get the last known training value.
        for col in available:
            if col not in future.columns:
                last_val = float(
                    X_train[col].iloc[-1],
                )
                future[col] = last_val

        # Fill any remaining NaN with 0.
        X_future = future[available].astype(float).fillna(0)

        # ── 6. Predict residual correction ─────────────
        correction = xgb_model.predict(X_future)

        result = forecast_df.copy()
        result["yhat"] = result["yhat"] + correction
        result["yhat_lower"] = result["yhat_lower"] + correction
        result["yhat_upper"] = result["yhat_upper"] + correction

        _logger.info(
            "Ensemble correction applied for %s: " "mean adj=%.2f",
            ticker,
            float(correction.mean()),
        )
        return result

    except Exception as exc:
        _logger.warning(
            "Ensemble failed for %s: %s",
            ticker,
            exc,
            exc_info=True,
        )
        return None
