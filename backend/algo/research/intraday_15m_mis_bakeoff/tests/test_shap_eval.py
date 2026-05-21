"""Tests for the per-class SHAP aggregator.

SHAP itself is library code we trust. We test our aggregation
logic against a frozen SHAP-shaped fixture: list of 3 arrays
of shape ``(n_rows, n_features)``.
"""

from __future__ import annotations

import numpy as np

from backend.algo.research.intraday_15m_mis_bakeoff.shap_eval import (
    BUCKET_LONG_SIDE,
    BUCKET_SHORT_SIDE,
    BUCKET_SYMMETRIC,
    aggregate_per_feature,
    bucket_features,
    compute_stable_features,
)


def _frozen_shap_values():
    """Three classes × 100 rows × 3 features.

    Feature 0: strongly LONG-positive in class LONG.
    Feature 1: strongly SHORT-positive in class SHORT.
    Feature 2: equally important in both classes (symmetric).
    """
    rng = np.random.default_rng(0)
    n_rows = 100
    sv_short = rng.normal(0, 0.01, (n_rows, 3))
    sv_flat = rng.normal(0, 0.01, (n_rows, 3))
    sv_long = rng.normal(0, 0.01, (n_rows, 3))

    sv_long[:, 0] += 0.5
    sv_short[:, 1] += 0.5
    sv_long[:, 2] += 0.3
    sv_short[:, 2] += 0.3

    return [sv_short, sv_flat, sv_long]


def test_aggregate_returns_expected_columns():
    sv = _frozen_shap_values()
    out = aggregate_per_feature(sv, feature_names=["f0", "f1", "f2"])
    assert set(out.columns) == {
        "feature", "mean_abs_long", "mean_abs_short",
        "directional_long", "directional_short", "asymmetry",
    }
    assert len(out) == 3


def test_aggregate_directional_signs_match_fixture():
    sv = _frozen_shap_values()
    out = aggregate_per_feature(sv, feature_names=["f0", "f1", "f2"])
    out = out.set_index("feature")
    assert out.loc["f0", "directional_long"] > 0.3
    assert out.loc["f1", "directional_short"] > 0.3


def test_bucket_classifies_long_short_symmetric():
    sv = _frozen_shap_values()
    out = aggregate_per_feature(sv, feature_names=["f0", "f1", "f2"])
    bucketed = bucket_features(out)
    by_feat = bucketed.set_index("feature")["bucket"].to_dict()
    assert by_feat["f0"] == BUCKET_LONG_SIDE
    assert by_feat["f1"] == BUCKET_SHORT_SIDE
    assert by_feat["f2"] == BUCKET_SYMMETRIC


def test_stable_features_intersection_across_seeds():
    rankings_per_seed = [
        {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7"},
        {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "f9"},
        {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "f10"},
        {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7"},
        {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "f8"},
    ]
    result = compute_stable_features(rankings_per_seed)
    assert result["stable"] == {"f0", "f1", "f2", "f3", "f4", "f5", "f6"}
    # f7 appears in 2/5 seeds — below mostly_overlap=4 default.
    assert "f7" not in result["mostly_stable"]
    # f0-f6 appear in 5/5 seeds — strictly above the threshold.
    assert {"f0", "f1", "f2", "f3", "f4", "f5", "f6"} <= result["mostly_stable"]
