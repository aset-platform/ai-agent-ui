"""SHAP attribution for FE-11's trade-outcome classifier
(ASETPLTFRM-402 / FE-12 / ASETPLTFRM-414).

Stacks on top of :func:`backend.algo.features.importance.
train_classifier` — the same GBC fit FE-11's importance ranking
uses — and explains each per-row prediction with per-feature
SHAP contributions.

Why SHAP on top of feature_importances_?
  * ``feature_importances_`` is GLOBAL — total impurity-reduction
    summed across the whole forest. Tells you which features the
    model cares about overall.
  * SHAP is PER-ROW — for THIS trade, why did the model think it
    was a winner? Attributes the prediction to individual feature
    contributions vs the baseline expected value.

Implementation notes:
  * Uses ``shap.TreeExplainer`` — fast (polynomial in tree size)
    for tree-based models like ``GradientBoostingClassifier``.
  * Binary classifier output shape varies by shap version /
    model: older shap returns ``list[ndarray]`` (one per class);
    newer shap returns a single ``ndarray`` of shape (n, m).
    We normalize to "positive-class contributions" either way.
  * ``shap`` is imported lazily inside
    :func:`compute_shap_for_trades` so the rest of the algo
    module imports cleanly even when the optional dep isn't
    installed yet. The FE-12 route surfaces a 503 (lifted
    by the caller) when ``import shap`` fails.

CLAUDE.md §5.1: SHAP value extraction is CPU-bound — wrap the
caller in ``asyncio.to_thread`` (the route does this).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np

from backend.algo.features.importance import TrainedClassifier

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FillShap:
    """SHAP attribution for a single trade fill.

    Attributes:
        fill_id: Row identifier (matches
            ``TrainedClassifier.fill_ids`` entry).
        shap_values: ``feature_name → contribution`` mapping.
            Positive values push the prediction toward class 1
            (winner); negative values push toward class 0.
        base_value: Model's expected output before any feature
            contributions (i.e. ``E[f(X)]``). The sum of
            ``base_value + sum(shap_values.values())`` should
            approximate the model's raw output for this row.
        prediction: ``model.predict_proba(...)[:, 1]`` — the
            positive-class probability for this row.
    """

    fill_id: str
    shap_values: dict[str, float]
    base_value: float
    prediction: float


@dataclass(frozen=True)
class ShapAnalysisResult:
    """Aggregate SHAP analysis for a strategy / period.

    Attributes:
        strategy_id: Strategy UUID as a string.
        period_start: Inclusive start date of the training
            window (forwarded from the trained classifier).
        period_end: Inclusive end date.
        n_fills: Number of rows the SHAP attribution covers.
        n_features: Number of design-matrix columns.
        classifier_version: Reproducibility stamp (forwarded
            from :class:`TrainedClassifier`).
        top_features_by_mean_abs_shap: List of
            ``(feature_name, mean_abs_shap)`` sorted by
            mean absolute SHAP contribution across all
            attributed rows, capped at ``top_n``.
        per_fill: SHAP attribution per row.
        computed_at: UTC timestamp when SHAP was extracted.
    """

    strategy_id: str
    period_start: date
    period_end: date
    n_fills: int
    n_features: int
    classifier_version: str
    top_features_by_mean_abs_shap: list[tuple[str, float]]
    per_fill: list[FillShap]
    computed_at: datetime


class _UnknownFillIdsError(ValueError):
    """Raised when caller passes ``fill_ids`` that aren't in
    the training set. Public alias: re-raised as plain
    ``ValueError`` so callers don't need a private import."""

    def __init__(self, unknown: list[str]) -> None:
        self.unknown = unknown
        super().__init__(
            "fill_ids not found in training set: " + ", ".join(unknown)
        )


def _positive_class_array(
    raw: object,
) -> np.ndarray:
    """Normalize shap.TreeExplainer output to a single ndarray
    of shape ``(n_rows, n_features)`` for the positive class.

    ``shap.TreeExplainer.shap_values`` for a binary GBC returns:
      * older shap (<0.40): ``list[ndarray]`` of length 2 — one
        per class. Take index 1 (positive class).
      * newer shap (>=0.40): single ``ndarray`` of shape
        ``(n, m)`` — already the positive-class array (sklearn
        binary GBC).
      * some builds: ``(n, m, 2)`` — 3-D with class as last
        axis. Slice ``[..., 1]``.

    Be defensive — the public shap API has rotated this shape
    a few times.
    """
    if isinstance(raw, list):
        if len(raw) == 0:
            raise ValueError("shap.TreeExplainer returned an empty list")
        return np.asarray(raw[-1])
    arr = np.asarray(raw)
    if arr.ndim == 3:
        return arr[..., -1]
    return arr


def _positive_class_base(raw: object) -> float:
    """Normalize ``explainer.expected_value`` to a single float
    representing the positive-class baseline.

    Older shap returns ``list[float]`` (one per class); newer
    returns a scalar float / 0-d ndarray.
    """
    if isinstance(raw, list):
        if not raw:
            return 0.0
        return float(raw[-1])
    arr = np.asarray(raw)
    if arr.ndim == 0:
        return float(arr)
    # 1-D length-2 → class 1 baseline.
    return float(arr.flatten()[-1])


def compute_shap_for_trades(
    trained: TrainedClassifier,
    *,
    fill_ids: list[str] | None = None,
    top_n: int = 10,
) -> ShapAnalysisResult:
    """Compute SHAP attribution for the trained classifier.

    Args:
        trained: Output of :func:`train_classifier` — the fitted
            model + design matrix to explain.
        fill_ids: If ``None``, attribute every training row. If
            a list, restrict attribution to those rows. The
            provided ids MUST all be members of
            ``trained.fill_ids`` — otherwise raise ValueError
            listing the unknowns (the route maps this to 400).
        top_n: Maximum number of features in the global ranking
            (``top_features_by_mean_abs_shap``). Per-row
            ``shap_values`` dicts are NOT truncated.

    Returns:
        :class:`ShapAnalysisResult` — per-row attribution +
        ranked global feature contributions.

    Raises:
        ValueError: Unknown ``fill_ids`` OR ``top_n <= 0``.
        RuntimeError: ``shap`` module is not installed
            (deferred import). Caller maps to 503.
    """
    if top_n <= 0:
        raise ValueError("top_n must be > 0")

    try:
        import shap  # noqa: WPS433 — deferred optional import
    except ImportError as exc:
        raise RuntimeError(
            "the 'shap' package is not installed; SHAP "
            "attribution is unavailable until it's added "
            "to requirements.txt and the backend is rebuilt"
        ) from exc

    # ── Resolve subset ────────────────────────────────────────
    if fill_ids is None:
        subset_idx = list(range(len(trained.fill_ids)))
        subset_ids = list(trained.fill_ids)
    else:
        id_to_idx = {fid: i for i, fid in enumerate(trained.fill_ids)}
        unknown = [fid for fid in fill_ids if fid not in id_to_idx]
        if unknown:
            raise _UnknownFillIdsError(unknown)
        # Preserve caller order; allow duplicates → de-dup but
        # keep first-seen order so the response is stable.
        seen: set[str] = set()
        subset_ids = []
        subset_idx = []
        for fid in fill_ids:
            if fid in seen:
                continue
            seen.add(fid)
            subset_ids.append(fid)
            subset_idx.append(id_to_idx[fid])

    if not subset_idx:
        # Caller passed an empty list → degenerate result.
        return ShapAnalysisResult(
            strategy_id=trained.strategy_id_seen,
            period_start=trained.period_start_seen,
            period_end=trained.period_end_seen,
            n_fills=0,
            n_features=len(trained.feature_columns),
            classifier_version=trained.classifier_version,
            top_features_by_mean_abs_shap=[],
            per_fill=[],
            computed_at=datetime.now(timezone.utc),
        )

    X_subset = trained.X[subset_idx, :]
    feature_columns = trained.feature_columns

    # ── SHAP attribution ──────────────────────────────────────
    explainer = shap.TreeExplainer(trained.model)
    raw_shap = explainer.shap_values(X_subset)
    shap_arr = _positive_class_array(raw_shap)
    base_value = _positive_class_base(explainer.expected_value)

    if shap_arr.shape != (len(subset_idx), len(feature_columns)):
        raise RuntimeError(
            "unexpected shap.TreeExplainer output shape "
            f"{shap_arr.shape}; expected "
            f"({len(subset_idx)}, {len(feature_columns)})"
        )

    # Positive-class predicted probabilities for the subset.
    proba = trained.model.predict_proba(X_subset)
    if proba.ndim == 2 and proba.shape[1] >= 2:
        positive_proba = proba[:, -1]
    else:
        # Single-class probability vector — pad with zeros.
        positive_proba = np.zeros(len(subset_idx), dtype=float)

    per_fill: list[FillShap] = []
    for i, fid in enumerate(subset_ids):
        row_shap = shap_arr[i, :]
        shap_dict = {
            feature_columns[j]: float(row_shap[j])
            for j in range(len(feature_columns))
        }
        per_fill.append(
            FillShap(
                fill_id=fid,
                shap_values=shap_dict,
                base_value=float(base_value),
                prediction=float(positive_proba[i]),
            )
        )

    # ── Global ranking by mean(|SHAP|) ────────────────────────
    mean_abs = np.abs(shap_arr).mean(axis=0)
    ranked = sorted(
        (
            (feature_columns[j], float(mean_abs[j]))
            for j in range(len(feature_columns))
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )[:top_n]

    computed_at = datetime.now(timezone.utc)
    _logger.info(
        "[shap] attribution complete strategy_id=%s "
        "n_fills=%d n_features=%d top_n=%d",
        trained.strategy_id_seen,
        len(subset_idx),
        len(feature_columns),
        len(ranked),
    )

    return ShapAnalysisResult(
        strategy_id=trained.strategy_id_seen,
        period_start=trained.period_start_seen,
        period_end=trained.period_end_seen,
        n_fills=len(subset_idx),
        n_features=len(feature_columns),
        classifier_version=trained.classifier_version,
        top_features_by_mean_abs_shap=ranked,
        per_fill=per_fill,
        computed_at=computed_at,
    )


__all__ = [
    "FillShap",
    "ShapAnalysisResult",
    "compute_shap_for_trades",
]
