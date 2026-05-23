"""Gate 6 — harness self-test on synthetic, deterministic data.

If one feature linearly drives the 3-class label, the harness
must rank it #1 by mean_abs SHAP across both LONG and SHORT
classes, with at least 3× the next feature's importance.
"""

from __future__ import annotations

import numpy as np
import shap
import xgboost as xgb

from backend.algo.research.intraday_15m_mis_bakeoff.shap_eval import (
    aggregate_per_feature,
)


def _synthetic_dataset(n_rows: int = 5_000, seed: int = 42):
    """Feature 0 drives the label; features 1-9 are pure noise."""
    rng = np.random.default_rng(seed)
    n_features = 10
    X = rng.normal(0, 1, size=(n_rows, n_features))
    # class 0 = SHORT, class 1 = FLAT, class 2 = LONG
    y = np.where(X[:, 0] > 0.5, 2,
         np.where(X[:, 0] < -0.5, 0,
                  1))
    feature_names = [f"f{i}" for i in range(n_features)]
    return X, y, feature_names


def test_harness_ranks_injected_feature_first():
    X, y, names = _synthetic_dataset()

    model = xgb.XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        n_estimators=200,
        max_depth=4,
        learning_rate=0.1,
        tree_method="hist",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X, y)

    sv = shap.TreeExplainer(model).shap_values(X)
    # TreeExplainer returns either a list of per-class arrays or a single
    # 3-D array (n_rows, n_features, n_classes) depending on shap version.
    if isinstance(sv, list):
        sv_list = sv  # already [SHORT, FLAT, LONG]
    else:
        sv_list = [sv[..., k] for k in range(3)]

    agg = aggregate_per_feature(sv_list, feature_names=names)
    agg["combined"] = agg["mean_abs_long"] + agg["mean_abs_short"]
    agg = agg.sort_values("combined", ascending=False).reset_index(drop=True)

    top = agg.iloc[0]
    runner_up = agg.iloc[1]
    assert top["feature"] == "f0", (
        f"expected f0 #1, got {top['feature']}; full ranking:\n{agg}"
    )
    assert top["combined"] >= 3 * runner_up["combined"], (
        f"top feature only {top['combined'] / runner_up['combined']:.2f}× "
        f"runner-up — harness is not picking up the signal cleanly"
    )
