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
from datetime import date, datetime
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


def gate4_random_baseline(
    y_train: np.ndarray, y_test: np.ndarray, test_mlogloss: float
) -> tuple[str, float]:
    """Test mlogloss must beat a stratified random classifier by 0.05.

    The random baseline emits the train class distribution as
    its prediction for every test row.
    """
    pi = np.bincount(y_train, minlength=3) / len(y_train)
    eps = 1e-15
    pi_clipped = np.clip(pi, eps, 1.0)
    baseline = float(-np.log(pi_clipped[y_test]).mean())
    delta = baseline - test_mlogloss
    ok = delta >= 0.05
    return (
        (
            "pass"
            if ok
            else (
                f"fail: model {test_mlogloss:.4f} vs "
                f"baseline {baseline:.4f} (delta={delta:.4f})"
            )
        ),
        baseline,
    )


def gate5_ranking_stability(
    X_fit: pd.DataFrame,
    y_fit: np.ndarray,
    X_val: pd.DataFrame,
    y_val: np.ndarray,
    X_test: pd.DataFrame,
    *,
    feature_names: list[str],
    seeds: list[int],
    top_k: int = 8,
    min_overlap: int = 6,
) -> tuple[str, dict]:
    """Train *seeds* boosters; intersect top-K by SHAP magnitude.

    Returns the gate result string + the stability dict from
    ``compute_stable_features``.
    """
    import shap

    from backend.algo.research.intraday_15m_mis_bakeoff.shap_eval import (
        compute_stable_features,
    )

    rankings: list[set[str]] = []
    for seed in seeds:
        model = _train_one(X_fit, y_fit, X_val, y_val, seed=seed)
        sv = shap.TreeExplainer(model).shap_values(X_test)
        sv_list = (
            sv if isinstance(sv, list) else [sv[..., k] for k in range(3)]
        )
        importance = np.abs(sv_list[0]).mean(0) + np.abs(sv_list[2]).mean(0)
        top_idx = importance.argsort()[-top_k:]
        rankings.append({feature_names[i] for i in top_idx})

    stab = compute_stable_features(rankings, mostly_overlap=4)
    overlap = len(stab["stable"])
    ok = overlap >= min_overlap
    return (
        (
            "pass"
            if ok
            else f"fail: only {overlap} features stable across all seeds"
        ),
        {
            "stable": list(stab["stable"]),
            "mostly_stable": list(stab["mostly_stable"]),
            "rankings_per_seed": [list(r) for r in rankings],
        },
    )


def gate7_per_regime(
    df_test: pd.DataFrame,
    y_test: np.ndarray,
    y_pred_proba: np.ndarray,
    *,
    min_rows: int = 500,
) -> dict[str, dict]:
    """Per-regime mlogloss + count.

    Soft gate: stamps caveats, does not fail.
    """
    if "regime_label" not in df_test.columns:
        return {}
    result: dict[str, dict] = {}
    eps = 1e-15
    proba = np.clip(y_pred_proba, eps, 1.0)
    for regime in ("BULL", "SIDEWAYS", "BEAR"):
        mask = df_test["regime_label"].to_numpy() == regime
        n = int(mask.sum())
        if n == 0:
            result[regime] = {
                "rows": 0,
                "mlogloss": None,
                "underpowered": True,
            }
            continue
        ll = float(-np.log(proba[mask, y_test[mask]]).mean())
        result[regime] = {
            "rows": n,
            "mlogloss": ll,
            "underpowered": n < min_rows,
        }
    return result


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
        0.6 * X[:, 0] + 0.5 * X[:, 1] + 0.4 * X[:, 2] + 0.3 * X[:, 3] + noise
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
    _y_test = test["label"].to_numpy()  # noqa: F841 — kept for future gates

    gate2_status, gate2_pct = gate2_label_distribution(y_fit)
    gates.label_distribution = gate2_status
    gates.leak_audit = gate3_leak_audit(X_fit, y_fit)

    model = _train_one(X_fit, y_fit, X_val, y_val, seed=42)
    test_mlogloss = float(model.evals_result_["validation_0"]["mlogloss"][-1])

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


def _augment_with_label(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Apply labeler.label_bars and return the labelled frame.

    Drops unlabellable rows. The label column is integer 0/1/2.
    """
    labelled = label_bars(df, threshold=threshold)
    if labelled.empty:
        raise RuntimeError("labeler produced 0 rows — check input data")
    return labelled


def run_real(
    *,
    tickers: list[str],
    date_min: date,
    date_max: date,
    train_fit_end: date,
    train_val_end: date,
    threshold: float,
    seeds: list[int],
    output_dir: Path,
) -> dict[str, Any]:
    """Real-data mode: load Iceberg → label → split → all gates → report.

    Used by both --dry-run (3 tickers, 2 weeks) and full mode.
    """
    from backend.algo.research.intraday_15m_mis_bakeoff.dataset import (
        load_research_frame,
    )

    _logger.info(
        "Loading research frame for %d tickers %s..%s",
        len(tickers),
        date_min,
        date_max,
    )
    raw = load_research_frame(
        tickers=tickers,
        date_min=date_min,
        date_max=date_max,
    )
    _logger.info(
        "Raw frame: %d rows × %d cols",
        len(raw),
        raw.shape[1] if not raw.empty else 0,
    )
    if raw.empty:
        raise RuntimeError(
            f"load_research_frame returned 0 rows for "
            f"{len(tickers)} tickers {date_min}..{date_max}"
        )

    # The dataset loader does not include atr_14 in OHLCV; it's in
    # the EAV feature pivot. Coerce the feature_value-derived
    # columns to float (they come back as object/Decimal from the pivot).
    feature_value_cols = [
        c
        for c in raw.columns
        if c
        not in {
            "ticker",
            "bar_open_ts_ns",
            "bar_date",
            "interval_sec",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "regime_label",
            "time_of_day_bucket",
        }
    ]
    for c in feature_value_cols:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")

    labelled = _augment_with_label(raw, threshold=threshold)
    _logger.info(
        "Labelled frame: %d rows after dropping unlabellable",
        len(labelled),
    )

    # Pre-training data assertions §7.3.
    assert labelled["entry_px"].notna().all(), "t+1 open missing"
    assert labelled["exit_px"].notna().all(), "t+4 close missing"
    assert (labelled["atr_14"] > 0).all(), "ATR_14 zero"
    assert (
        labelled.duplicated(["ticker", "bar_open_ts_ns"]).sum() == 0
    ), "pivot duplicates"

    labelled = labelled.sort_values("bar_date").reset_index(drop=True)
    train_fit, train_val, test = chronological_split(
        labelled,
        date_col="bar_date",
        train_fit_end=train_fit_end,
        train_val_end=train_val_end,
    )

    # Feature columns are everything except identifiers + label + the
    # forward-looking aux columns the labeler added.
    excluded = {
        "ticker",
        "bar_open_ts_ns",
        "bar_date",
        "interval_sec",
        "label",
        "entry_px",
        "exit_px",
        "r_norm",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    work = labelled.copy()
    for cat_col in ("regime_label", "time_of_day_bucket"):
        if cat_col in work.columns:
            dummies = pd.get_dummies(
                work[cat_col], prefix=cat_col, dummy_na=False
            )
            work = pd.concat([work.drop(columns=[cat_col]), dummies], axis=1)
            excluded.add(cat_col)

    feature_names = [
        c
        for c in work.columns
        if c not in excluded and pd.api.types.is_numeric_dtype(work[c])
    ]

    # Re-split after one-hot using the same row-index partitioning.
    train_fit_idx = labelled.index[labelled["bar_date"] <= train_fit_end]
    train_val_idx = labelled.index[
        (labelled["bar_date"] > train_fit_end)
        & (labelled["bar_date"] <= train_val_end)
    ]
    test_idx = labelled.index[labelled["bar_date"] > train_val_end]

    X_fit = work.loc[train_fit_idx][feature_names]
    X_val = work.loc[train_val_idx][feature_names]
    X_test = work.loc[test_idx][feature_names]
    y_fit = work.loc[train_fit_idx]["label"].to_numpy()
    y_val = work.loc[train_val_idx]["label"].to_numpy()
    y_test = work.loc[test_idx]["label"].to_numpy()

    gates = GateResults()
    gates.chronology = gate1_chronology(
        labelled.loc[train_fit_idx],
        labelled.loc[train_val_idx],
        labelled.loc[test_idx],
    )
    gates.label_distribution, label_dist = gate2_label_distribution(y_fit)
    gates.leak_audit = gate3_leak_audit(X_fit, y_fit)

    primary_model = _train_one(X_fit, y_fit, X_val, y_val, seed=seeds[0])
    proba_test = primary_model.predict_proba(X_test)
    eps = 1e-15
    proba_clipped = np.clip(proba_test, eps, 1.0)
    test_mlogloss = float(
        -np.log(proba_clipped[np.arange(len(y_test)), y_test]).mean()
    )

    gates.random_baseline, baseline = gate4_random_baseline(
        y_fit,
        y_test,
        test_mlogloss,
    )
    gates.ranking_stability, stability = gate5_ranking_stability(
        X_fit,
        y_fit,
        X_val,
        y_val,
        X_test,
        feature_names=feature_names,
        seeds=seeds,
    )
    test_with_regime = labelled.loc[test_idx][
        ["bar_date"] + [c for c in ["regime_label"] if c in labelled.columns]
    ]
    gates.per_regime = gate7_per_regime(
        test_with_regime,
        y_test,
        proba_test,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    primary_model.save_model(str(output_dir / "model.json"))

    summary = {
        "mode": "real",
        "tickers": len(tickers),
        "date_window": [str(date_min), str(date_max)],
        "rows": {
            "fit": len(X_fit),
            "val": len(X_val),
            "test": len(X_test),
        },
        "feature_count": len(feature_names),
        "gates": gates.__dict__,
        "best_iteration": int(primary_model.best_iteration or 0),
        "test_mlogloss": test_mlogloss,
        "random_baseline_mlogloss": baseline,
        "label_distribution": label_dist,
        "stability": stability,
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, default=str, indent=2)
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Synthetic 5K rows; no Iceberg.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Real Iceberg, 3 tickers, 2 weeks.",
    )
    parser.add_argument(
        "--train-end",
        type=lambda s: date.fromisoformat(s),
        default=date(2026, 2, 28),
        help="Last date (inclusive) of the train_val split.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="sigma-multiple threshold for LONG/SHORT.",
    )
    parser.add_argument(
        "--seeds",
        type=str,
        default="42,43,44,45,46",
        help="Comma-separated random seeds for Gate 5.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home()
        / ".ai-agent-ui"
        / "research_runs"
        / f"{datetime.now().date()}-intraday-15m-bakeoff",
        help="Output directory for run artifacts.",
    )
    parser.add_argument(
        "--tickers-cap",
        type=int,
        default=None,
        help="Optional cap on F&O universe for debugging.",
    )
    args = parser.parse_args(argv)

    seeds = [int(s) for s in args.seeds.split(",")]

    if args.smoke:
        out = run_smoke()
        sys.stdout.write(json.dumps(out, default=str, indent=2) + "\n")
        return 0

    from backend.algo.research.intraday_15m_mis_bakeoff.universe import (
        load_fno_universe,
    )

    if args.dry_run:
        tickers = ["RELIANCE.NS", "HDFCBANK.NS", "INFY.NS"]
        date_min = date(2026, 1, 1)
        date_max = date(2026, 1, 14)
        train_fit_end = date(2026, 1, 7)
        train_val_end = date(2026, 1, 10)
    else:
        tickers = load_fno_universe()
        if args.tickers_cap is not None:
            tickers = tickers[: args.tickers_cap]
        date_min = date(2025, 11, 17)
        date_max = date(2026, 5, 21)
        # train_fit ends 20 days before train_val.
        train_fit_end_ts = pd.Timestamp(args.train_end) - pd.Timedelta(days=20)
        train_fit_end = train_fit_end_ts.date()
        train_val_end = args.train_end

    out = run_real(
        tickers=tickers,
        date_min=date_min,
        date_max=date_max,
        train_fit_end=train_fit_end,
        train_val_end=train_val_end,
        threshold=args.threshold,
        seeds=seeds,
        output_dir=args.out,
    )
    sys.stdout.write(json.dumps(out, default=str, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
