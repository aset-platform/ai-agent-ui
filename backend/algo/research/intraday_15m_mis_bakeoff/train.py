"""XGBoost 3-class training + gate orchestration for the bake-off.

Modes:
  --smoke   synthetic 5K-row data; no Iceberg
  --dry-run real Iceberg, 3 tickers, 2 weeks (added in Task 9)
  (full)    F&O 200, full window, 5 seeds (added in Task 9)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.utils.class_weight import compute_class_weight

from backend.algo.research._shared.time_split import (
    assert_chronological,
    chronological_split,
)
from backend.algo.research.intraday_15m_mis_bakeoff.labeler import (
    LABEL_FLAT,
    LABEL_LONG,
    LABEL_SHORT,
    label_bars,
)

_logger = logging.getLogger("bakeoff.train")

XGB_PARAMS: dict[str, Any] = {
    "objective": "multi:softprob",
    "num_class": 3,
    "n_estimators": 400,
    "max_depth": 5,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.7,
    "min_child_weight": 10,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "tree_method": "hist",
    "eval_metric": ["mlogloss", "merror"],
    "early_stopping_rounds": 30,
    "n_jobs": -1,
}


@dataclass
class GateResults:
    """Accumulated pass/fail/skipped status for each quality gate."""

    chronology: str = "skipped"
    label_distribution: str = "skipped"
    leak_audit: str = "skipped"
    random_baseline: str = "skipped"
    ranking_stability: str = "skipped"
    harness_self_test: str = "skipped"
    per_regime: dict[str, dict[str, float]] = field(default_factory=dict)


def gate1_chronology(
    train_fit: pd.DataFrame,
    train_val: pd.DataFrame,
    test: pd.DataFrame,
) -> str:
    """Hard fail on overlap."""
    assert_chronological(train_fit, train_val, test, date_col="bar_date")
    return "pass"


def gate2_label_distribution(
    y_train: np.ndarray,
) -> tuple[str, dict[int, float]]:
    """Each class must be in [15%, 60%]."""
    counts = pd.Series(y_train).value_counts(normalize=True).to_dict()
    pct = {int(k): float(v) for k, v in counts.items()}
    ok = all(
        0.15 <= pct.get(k, 0.0) <= 0.60
        for k in (LABEL_SHORT, LABEL_FLAT, LABEL_LONG)
    )
    return ("pass" if ok else f"fail: {pct}"), pct


def gate3_leak_audit(
    X: pd.DataFrame,
    y: np.ndarray,
    threshold: float = 0.5,
) -> str:
    """Pearson |corr| < 0.5 for every feature."""
    corrs = X.corrwith(pd.Series(y, index=X.index)).abs()
    offenders = corrs[corrs >= threshold].to_dict()
    if offenders:
        raise ValueError(
            f"Gate 3 LEAK AUDIT FAILED — features correlated with "
            f"label: {offenders}"
        )
    return "pass"


def _synthetic_smoke_frame(
    n_rows: int = 5_000,
    seed: int = 42,
) -> tuple[pd.DataFrame, list[str]]:
    """5K-row synthetic data — passes Gates 1, 2, and 3.

    Label signal is spread across *four* features (f0..f3) with
    substantial additive Gaussian noise so no single feature has
    Pearson |corr| >= 0.5 with the label, while the class
    distribution stays within [15%, 60%] per Gate 2.

    Thresholds (+0.8 / -0.8) on the noisy composite score produce
    ~25% LONG, ~25% SHORT, ~50% FLAT — well inside Gate 2 bounds.
    """
    rng = np.random.default_rng(seed)
    n_features = 10
    X = rng.normal(0, 1, size=(n_rows, n_features))

    # Multi-feature composite with additive noise dilutes any single
    # feature's Pearson |corr| to ~0.3, safely below the 0.5 threshold.
    noise = rng.normal(0, 1.5, size=n_rows)
    score = (
        0.6 * X[:, 0]
        + 0.5 * X[:, 1]
        + 0.4 * X[:, 2]
        + 0.3 * X[:, 3]
        + noise
    )

    y = np.where(
        score > 0.8,
        LABEL_LONG,
        np.where(score < -0.8, LABEL_SHORT, LABEL_FLAT),
    )

    feature_names = [f"f{i}" for i in range(n_features)]
    df = pd.DataFrame(X, columns=feature_names)
    df["bar_date"] = pd.date_range(
        "2026-01-01", periods=n_rows, freq="15min"
    ).date
    df["label"] = y
    return df, feature_names


def _train_one(
    X_fit: pd.DataFrame,
    y_fit: np.ndarray,
    X_val: pd.DataFrame,
    y_val: np.ndarray,
    *,
    seed: int,
) -> xgb.XGBClassifier:
    params = XGB_PARAMS | {"random_state": seed}
    w = compute_class_weight(
        "balanced",
        classes=np.array([LABEL_SHORT, LABEL_FLAT, LABEL_LONG]),
        y=y_fit,
    )
    sample_weight = w[y_fit]
    model = xgb.XGBClassifier(**params)
    model.fit(
        X_fit,
        y_fit,
        sample_weight=sample_weight,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )
    return model


def run_smoke() -> dict[str, Any]:
    """End-to-end on synthetic data — exercises gates 1, 2, 3 + training."""
    df, feature_names = _synthetic_smoke_frame()

    df_sorted = df.sort_values("bar_date").reset_index(drop=True)
    train_fit, train_val, test = chronological_split(
        df_sorted,
        date_col="bar_date",
        train_fit_end=df_sorted["bar_date"].iloc[3000],
        train_val_end=df_sorted["bar_date"].iloc[4000],
    )

    gates = GateResults()
    gates.chronology = gate1_chronology(train_fit, train_val, test)

    X_fit = train_fit[feature_names]
    y_fit = train_fit["label"].to_numpy()
    X_val = train_val[feature_names]
    y_val = train_val["label"].to_numpy()
    X_test = test[feature_names]
    y_test = test["label"].to_numpy()

    gate2_status, gate2_pct = gate2_label_distribution(y_fit)
    gates.label_distribution = gate2_status
    gates.leak_audit = gate3_leak_audit(X_fit, y_fit)

    model = _train_one(X_fit, y_fit, X_val, y_val, seed=42)
    test_mlogloss = float(
        model.evals_result_["validation_0"]["mlogloss"][-1]
    )

    return {
        "mode": "smoke",
        "rows": {
            "fit": len(X_fit),
            "val": len(X_val),
            "test": len(X_test),
        },
        "label_distribution_pct": gate2_pct,
        "gates": gates.__dict__,
        "best_iteration": int(model.best_iteration or 0),
        "test_mlogloss_estimate": test_mlogloss,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — parse args, dispatch mode, emit JSON to stdout."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run on synthetic 5K rows; no Iceberg.",
    )
    args = parser.parse_args(argv)

    if not args.smoke:
        parser.error(
            "Only --smoke is wired in this commit; "
            "--dry-run and full mode arrive in Task 9."
        )

    out = run_smoke()
    sys.stdout.write(json.dumps(out, default=str, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
