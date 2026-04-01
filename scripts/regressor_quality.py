#!/usr/bin/env python
"""Regressor quality diagnostic — compare forecast accuracy.

Trains 3 models for a ticker and compares:
1. Prophet baseline (no regressors)
2. Prophet + regressors (VIX, macro, sentiment)
3. Prophet + regressors + XGBoost ensemble

Uses 10-year data cap for consistent CV across tickers.
Ensemble comparison uses 80/20 time-series split for
honest out-of-sample evaluation.

Usage::

    source ~/.ai-agent-ui/venv/bin/activate
    PYTHONPATH=backend python scripts/regressor_quality.py AAPL
"""

import logging
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s",
)
_log = logging.getLogger("regressor_quality")
_log.setLevel(logging.INFO)


def _run_cv(
    model,
    prophet_df: pd.DataFrame,
    label: str,
) -> dict:
    """Run Prophet CV on last 10 years of data.

    Refits the model on capped data so CV cutoffs are
    consistent across tickers regardless of history
    length (~32 cutoffs for 10-year window).
    """
    from datetime import timedelta

    from prophet import Prophet as _P
    from prophet.diagnostics import (
        cross_validation,
        performance_metrics,
    )

    # Cap to last 10 years.
    _ten_yr = prophet_df["ds"].max() - timedelta(
        days=3650,
    )
    cv_df = prophet_df[prophet_df["ds"] >= _ten_yr].copy()
    if len(cv_df) < 730:
        cv_df = prophet_df.copy()

    cv_days = (cv_df["ds"].max() - cv_df["ds"].min()).days

    # Refit on capped data with same regressors.
    cv_model = _P(
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
        interval_width=0.80,
    )
    cv_train = cv_df.copy()
    for reg_name in model.extra_regressors:
        cv_model.add_regressor(reg_name)
        if reg_name in model.history.columns:
            reg_vals = model.history[["ds", reg_name]]
            cv_train = cv_train.merge(
                reg_vals,
                on="ds",
                how="left",
            )
            cv_train[reg_name] = cv_train[reg_name].ffill().bfill()
    cv_model.fit(cv_train)

    _log.info(
        "  Running CV for %s (%d days, ~%d cutoffs)...",
        label,
        cv_days,
        max(1, (cv_days - 730 - 90) // 90),
    )
    df_cv = cross_validation(
        cv_model,
        initial="730 days",
        period="90 days",
        horizon="90 days",
        parallel="processes",
    )
    n_cutoffs = df_cv["cutoff"].nunique()
    metrics = performance_metrics(df_cv)
    mae = float(metrics["mae"].iloc[-1])
    rmse = float(metrics["rmse"].iloc[-1])
    mape = float(metrics["mape"].iloc[-1]) * 100
    return {
        "MAE": mae,
        "RMSE": rmse,
        "MAPE": mape,
        "cutoffs": n_cutoffs,
    }


def _ensemble_oos_eval(
    model_reg,
    train_df: pd.DataFrame,
    ticker: str,
) -> dict | None:
    """Out-of-sample ensemble evaluation.

    Uses 80/20 time-series split:
    - Train XGBoost on first 80% of data
    - Evaluate Prophet-only vs ensemble on last 20%

    Returns dict with prophet_mae, ensemble_mae,
    prophet_mape, ensemble_mape, or None on failure.
    """
    try:
        from tools._forecast_ensemble import _FEATURES
        from tools._stock_shared import _require_repo
        from xgboost import XGBRegressor

        repo = _require_repo()

        # Get Prophet in-sample predictions.
        in_sample = model_reg.predict(train_df)
        work = train_df.copy()
        work["prophet_yhat"] = in_sample["yhat"].values
        work["residual"] = work["y"] - work["prophet_yhat"]

        # Load tech indicators.
        tech = repo.get_technical_indicators(ticker)
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

        if tech.empty:
            return None

        work["_date"] = pd.to_datetime(
            work["ds"],
        ).dt.date
        tech["_date"] = pd.to_datetime(
            tech["date"],
        ).dt.date
        work = work.merge(
            tech[["_date"] + tech_cols],
            on="_date",
            how="left",
        )
        work = work.dropna(
            subset=["residual"] + tech_cols,
        )

        available = [f for f in _FEATURES if f in work.columns]
        if len(work) < 300:
            return None

        # 80/20 time-series split.
        split_idx = int(len(work) * 0.8)
        train_part = work.iloc[:split_idx]
        test_part = work.iloc[split_idx:]

        X_train = train_part[available].astype(float)
        y_train = train_part["residual"].astype(float)
        X_test = test_part[available].astype(float)

        xgb = XGBRegressor(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=0,
        )
        xgb.fit(X_train, y_train)

        # Evaluate on test set.
        correction = xgb.predict(X_test)
        test_y = test_part["y"].values
        test_prophet = test_part["prophet_yhat"].values
        test_ensemble = test_prophet + correction

        pro_err = np.abs(test_y - test_prophet)
        ens_err = np.abs(test_y - test_ensemble)
        pro_pct = (
            np.abs(
                (test_y - test_prophet) / test_y,
            )
            * 100
        )
        ens_pct = (
            np.abs(
                (test_y - test_ensemble) / test_y,
            )
            * 100
        )

        # Feature importance.
        importance = xgb.feature_importances_
        feat_imp = sorted(
            zip(available, importance),
            key=lambda x: x[1],
            reverse=True,
        )

        return {
            "prophet_mae": float(pro_err.mean()),
            "ensemble_mae": float(ens_err.mean()),
            "prophet_mape": float(pro_pct.mean()),
            "ensemble_mape": float(ens_pct.mean()),
            "test_rows": len(test_part),
            "train_rows": len(train_part),
            "features": len(available),
            "feature_importance": feat_imp,
            "mean_correction": float(correction.mean()),
        }
    except Exception as exc:
        _logger.info(f"  ERROR: {exc}")
        return None


def main():
    if len(sys.argv) < 2:
        _logger.info("Usage: python scripts/" "regressor_quality.py TICKER")
        sys.exit(1)

    ticker = sys.argv[1].upper().strip()
    _logger.info(f"\n{'=' * 55}")
    _logger.info(f"  REGRESSOR QUALITY REPORT: {ticker}")
    _logger.info(f"{'=' * 55}\n")

    from tools._forecast_model import (
        _prepare_data_for_prophet,
        _train_prophet_model,
    )
    from tools._forecast_shared import (
        _load_ohlcv,
        _load_regressors_from_iceberg,
    )

    # Load data.
    _log.info("Loading OHLCV for %s...", ticker)
    df = _load_ohlcv(ticker)
    if df is None:
        _logger.info(f"ERROR: No OHLCV data for {ticker}")
        sys.exit(1)

    prophet_df = _prepare_data_for_prophet(df)
    current_price = float(prophet_df["y"].iloc[-1])
    data_days = (prophet_df["ds"].max() - prophet_df["ds"].min()).days
    _logger.info(f"  Data: {len(prophet_df)} rows ({data_days} days)")
    _logger.info(f"  Current price: ${current_price:.2f}\n")

    # ── Model 1: Baseline (no regressors) ──────────
    _logger.info("MODEL 1: Prophet Baseline (no regressors)")
    model_base, _ = _train_prophet_model(
        prophet_df,
        ticker=ticker,
    )
    base_acc = _run_cv(model_base, prophet_df, "baseline")
    _logger.info(
        f"  MAE : {base_acc['MAE']:.2f}  "
        f"RMSE: {base_acc['RMSE']:.2f}  "
        f"MAPE: {base_acc['MAPE']:.1f}%  "
        f"({base_acc['cutoffs']} cutoffs)\n"
    )

    # ── Model 2: With regressors ───────────────────
    _logger.info("MODEL 2: Prophet + Regressors " "(market, macro, sentiment)")
    regressors = _load_regressors_from_iceberg(
        ticker,
        prophet_df,
    )

    reg_cols = []
    if regressors is not None:
        reg_cols = [c for c in regressors.columns if c != "ds"]
        _logger.info(f"  Regressors: {reg_cols}")

    model_reg, train_df = _train_prophet_model(
        prophet_df,
        ticker=ticker,
        regressors=regressors,
    )
    reg_acc = _run_cv(model_reg, prophet_df, "regressors")
    _logger.info(
        f"  MAE : {reg_acc['MAE']:.2f}  "
        f"RMSE: {reg_acc['RMSE']:.2f}  "
        f"MAPE: {reg_acc['MAPE']:.1f}%  "
        f"({reg_acc['cutoffs']} cutoffs)"
    )

    # Delta vs baseline.
    mae_d = reg_acc["MAE"] - base_acc["MAE"]
    mape_d = reg_acc["MAPE"] - base_acc["MAPE"]
    mae_pct = (mae_d / base_acc["MAE"]) * 100 if base_acc["MAE"] else 0
    s = "+" if mae_d >= 0 else ""
    _logger.info(
        f"  vs Baseline: MAE {s}{mae_d:.2f} "
        f"({s}{mae_pct:.1f}%), "
        f"MAPE {s}{mape_d:.1f}pp\n"
    )

    # ── Model 3: XGBoost Ensemble (out-of-sample) ──
    _logger.info("MODEL 3: Prophet + XGBoost Ensemble " "(80/20 out-of-sample)")
    ens = _ensemble_oos_eval(model_reg, train_df, ticker)

    if ens is not None:
        _logger.info(
            f"  Split: train={ens['train_rows']} "
            f"test={ens['test_rows']} "
            f"features={ens['features']}"
        )
        _logger.info(f"  Mean correction: ${ens['mean_correction']:.2f}")
        _logger.info(
            f"\n  OUT-OF-SAMPLE COMPARISON " f"(last {ens['test_rows']} days):"
        )
        _logger.info(
            f"  Prophet only : "
            f"MAE={ens['prophet_mae']:.2f}  "
            f"MAPE={ens['prophet_mape']:.1f}%"
        )
        _logger.info(
            f"  + Ensemble   : "
            f"MAE={ens['ensemble_mae']:.2f}  "
            f"MAPE={ens['ensemble_mape']:.1f}%"
        )

        ens_mae_d = ens["ensemble_mae"] - ens["prophet_mae"]
        ens_mape_d = ens["ensemble_mape"] - ens["prophet_mape"]
        es = "+" if ens_mae_d >= 0 else ""
        _logger.info(
            f"  Ensemble delta: "
            f"MAE {es}{ens_mae_d:.2f} "
            f"({es}{ens_mae_d / ens['prophet_mae'] * 100:.1f}%), "
            f"MAPE {es}{ens_mape_d:.1f}pp"
        )

        # Feature importance.
        _logger.info(
            f"\n  XGBOOST FEATURE IMPORTANCE "
            f"(top {min(len(ens['feature_importance']), 15)}):"
        )
        for i, (name, imp) in enumerate(
            ens["feature_importance"][:15],
            1,
        ):
            bar = "#" * int(imp * 50)
            _logger.info(f"  {i:2d}. {name:20s} {imp:.4f} {bar}")
    else:
        _logger.info("  SKIPPED — insufficient data")

    # ── Verdict ────────────────────────────────────
    _logger.info(f"\n{'=' * 55}")
    # Regressor verdict.
    if mape_d < -0.5:
        rv = f"Regressors IMPROVE by " f"{abs(mape_d):.1f}pp MAPE"
    elif mape_d > 0.5:
        rv = f"Regressors HURT by " f"{mape_d:.1f}pp MAPE"
    else:
        rv = f"Regressors MARGINAL ({mape_d:+.1f}pp)"

    # Ensemble verdict.
    if ens is not None:
        if ens_mape_d < -0.5:
            ev = f"Ensemble IMPROVES by " f"{abs(ens_mape_d):.1f}pp MAPE"
        elif ens_mape_d > 0.5:
            ev = (
                f"Ensemble HURTS by " f"{ens_mape_d:.1f}pp MAPE (overfitting?)"
            )
        else:
            ev = f"Ensemble MARGINAL ({ens_mape_d:+.1f}pp)"
    else:
        ev = "Ensemble: N/A"

    _logger.info(f"  REGRESSORS: {rv}")
    _logger.info(f"  ENSEMBLE  : {ev}")
    _logger.info(f"{'=' * 55}\n")


if __name__ == "__main__":
    main()
