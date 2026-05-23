"""SHAP per-class aggregation + asymmetry bucketing.

Spec §6.2, §6.3, §7.2.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BUCKET_LONG_SIDE = "long_side"
BUCKET_SHORT_SIDE = "short_side"
BUCKET_SYMMETRIC = "symmetric"


def aggregate_per_feature(
    shap_values: list[np.ndarray],
    feature_names: list[str],
) -> pd.DataFrame:
    """Compute the 4 per-feature aggregations from the spec table.

    Args:
        shap_values: As returned by ``TreeExplainer.shap_values`` —
            list of 3 arrays in class order
            ``[SHORT, FLAT, LONG]``, each shape ``(n_rows, n_features)``.
        feature_names: Column names matching the feature axis.

    Returns:
        Long frame: one row per feature, columns
        ``feature, mean_abs_long, mean_abs_short, directional_long,
        directional_short, asymmetry``.
    """
    sv_short, _sv_flat, sv_long = shap_values
    if sv_short.shape[1] != len(feature_names):
        raise ValueError(
            f"feature_names length {len(feature_names)} != "
            f"SHAP feature axis {sv_short.shape[1]}"
        )
    rows = []
    for i, fname in enumerate(feature_names):
        rows.append({
            "feature": fname,
            "mean_abs_long": float(np.abs(sv_long[:, i]).mean()),
            "mean_abs_short": float(np.abs(sv_short[:, i]).mean()),
            "directional_long": float(sv_long[:, i].mean()),
            "directional_short": float(sv_short[:, i].mean()),
        })
    out = pd.DataFrame(rows)
    out["asymmetry"] = out["mean_abs_long"] - out["mean_abs_short"]
    return out


def bucket_features(agg: pd.DataFrame) -> pd.DataFrame:
    """Tag each feature as long/short/symmetric/interaction_only.

    Bucketing per spec §6.3:
      - long_side  if asymmetry > +0.5 × σ_asym
      - short_side if asymmetry < -0.5 × σ_asym
      - symmetric  otherwise
    """
    sigma = agg["asymmetry"].std(ddof=0) or 1e-12
    out = agg.copy()
    cond_long = out["asymmetry"] > 0.5 * sigma
    cond_short = out["asymmetry"] < -0.5 * sigma

    bucket = np.where(
        cond_long, BUCKET_LONG_SIDE,
        np.where(cond_short, BUCKET_SHORT_SIDE, BUCKET_SYMMETRIC),
    )
    out["bucket"] = bucket
    return out


def compute_stable_features(
    rankings_per_seed: list[set[str]],
    *,
    mostly_overlap: int = 4,
) -> dict[str, set[str]]:
    """Gate 5 — intersection + mostly-overlap sets across seeds.

    Args:
        rankings_per_seed: Per-seed top-K feature sets (same K).
        mostly_overlap: Min seeds (out of N) a feature must appear
            in to land in ``mostly_stable``.

    Returns:
        ``stable``: features that appear in ALL seeds (strict intersection).
        ``mostly_stable``: features that appear in at least
        ``mostly_overlap`` seeds.  Callers apply their own gate
        threshold on ``len(result["stable"])``.
    """
    if not rankings_per_seed:
        return {"stable": set(), "mostly_stable": set()}

    universe: set[str] = set().union(*rankings_per_seed)
    counts = {f: sum(f in r for r in rankings_per_seed) for f in universe}
    stable = {f for f, c in counts.items() if c == len(rankings_per_seed)}
    mostly_stable = {
        f for f, c in counts.items() if c >= mostly_overlap
    }
    return {"stable": stable, "mostly_stable": mostly_stable}
