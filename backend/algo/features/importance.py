"""Feature importance ranking via sklearn
GradientBoostingClassifier (ASETPLTFRM-402 / FE-11).

Trains on the ``(features_json, outcome)`` pairs persisted to
``stocks.trade_feature_snapshots`` by FE-5. The downstream
caller (the FE-11 route) loads the snapshot rows and passes
them in as a list of dicts; this module is a PURE FUNCTION —
no Iceberg / Redis / network I/O.

Outcome derivation (priority order):
  1. If ``outcome_label`` is a non-empty string → use it
     directly. ``"winner"`` maps to ``1``;
     ``"loser"`` / ``"breakeven"`` (and anything else
     non-empty) map to ``0``.
  2. Else if ``realised_pnl_inr`` is a real number
     (not None, not NaN) → ``1`` when ``> 0``, else ``0``.
  3. Else → row is dropped (un-labeled).

The sklearn classifier (``GradientBoostingClassifier``) is fit
deterministically with ``random_state=42`` so the importance
ranking is stable for a given input set — important for the
Redis cache hit-rate the route layer relies on.

CLAUDE.md §5.1 reminder: CPU-bound sklearn fits MUST be
wrapped in ``asyncio.to_thread`` by the calling route.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import sklearn
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.impute import SimpleImputer

_logger = logging.getLogger(__name__)

_GBC_N_ESTIMATORS = 100
_GBC_MAX_DEPTH = 3
_GBC_RANDOM_STATE = 42

# sentinel string outcomes that count as a winning trade.
_WINNER_LABELS: frozenset[str] = frozenset({"winner"})


class InsufficientDataError(Exception):
    """Raised when fewer than ``min_trades`` labeled rows are
    available for a stable importance fit."""


@dataclass(frozen=True)
class FeatureScore:
    """Single ``(feature_name, importance)`` row."""

    name: str
    importance: float


@dataclass
class TrainedClassifier:
    """Fitted classifier + design matrix + row alignment.

    Returned by :func:`train_classifier` and consumed by FE-12's
    SHAP module. ``X`` is post-imputation (median-filled), so
    SHAP sees the same matrix the GBC fit saw.

    Attributes:
        model: Fitted ``GradientBoostingClassifier``.
        feature_columns: Column names aligned with ``X``.
        X: Design matrix used to fit (n_rows, n_features).
        y: Outcome labels aligned with ``X`` (n_rows,).
        n_trades_used: Number of LABELED rows that fit on.
        fill_ids: Row identifiers aligned with ``X`` rows. For
            now sourced from snapshot ``fill_id`` if present;
            otherwise a positional placeholder ``"row-{i}"``.
        classifier_version: Reproducibility stamp.
        strategy_id_seen: Strategy_id pulled from the first
            labeled row's header (may be empty).
        period_start_seen: First non-None period_start.
        period_end_seen: First non-None period_end.
    """

    model: GradientBoostingClassifier
    feature_columns: list[str]
    X: np.ndarray
    y: np.ndarray
    n_trades_used: int
    fill_ids: list[str]
    classifier_version: str
    strategy_id_seen: str
    period_start_seen: date
    period_end_seen: date


@dataclass(frozen=True)
class FeatureImportanceResult:
    """Sorted top-N feature importance for a strategy / period.

    Attributes:
        strategy_id: Strategy UUID as a string.
        period_start: Inclusive start date of the training
            window (matches the route query param).
        period_end: Inclusive end date of the training window.
        n_trades_used: Number of LABELED rows that made it
            past outcome derivation (drops un-labeled rows).
        n_features: Distinct numeric features observed in the
            training set (post column-alignment).
        top_features: List of ``FeatureScore`` sorted
            descending by importance. Length capped at the
            caller's ``top_n``.
        classifier_version: Reproducibility stamp —
            sklearn version + model class + key hyperparams.
        fitted_at: UTC timestamp when the fit completed.
    """

    strategy_id: str
    period_start: date
    period_end: date
    n_trades_used: int
    n_features: int
    top_features: list[FeatureScore]
    classifier_version: str
    fitted_at: datetime


def _classifier_version() -> str:
    """Reproducibility stamp.

    Format: ``sklearn-{version}-gbc-n{n_estimators}-d{depth}``.
    Locking the sklearn version + key GBC hyperparams lets
    downstream consumers tell whether two importance rankings
    were fit with comparable settings (or whether a re-fit is
    required after a library upgrade).
    """
    return (
        f"sklearn-{sklearn.__version__}-gbc-"
        f"n{_GBC_N_ESTIMATORS}-d{_GBC_MAX_DEPTH}"
    )


def _is_real_number(v: Any) -> bool:
    """True iff ``v`` is a finite real number (not NaN / Inf)."""
    if v is None:
        return False
    if isinstance(v, bool):
        return False
    if not isinstance(v, (int, float)):
        return False
    try:
        fv = float(v)
    except (TypeError, ValueError, OverflowError):
        return False
    return not (math.isnan(fv) or math.isinf(fv))


def _derive_outcome(row: dict[str, Any]) -> int | None:
    """Outcome priority: label → pnl-sign → drop.

    Returns ``1`` for a winning trade, ``0`` for a losing /
    breakeven trade, or ``None`` if the row carries no
    usable outcome signal (both ``outcome_label`` and
    ``realised_pnl_inr`` are missing / NaN).
    """
    label = row.get("outcome_label")
    if isinstance(label, str) and label.strip():
        # Non-empty string label is authoritative.
        return 1 if label.strip().lower() in _WINNER_LABELS else 0

    pnl = row.get("realised_pnl_inr")
    if _is_real_number(pnl):
        return 1 if float(pnl) > 0.0 else 0

    return None


def _parse_features_json(blob: Any) -> dict[str, float]:
    """Parse a ``features_json`` cell into a numeric-only map.

    Non-numeric features (e.g. ``time_of_day_bucket``,
    ``regime_label``) are dropped — sklearn requires a
    numeric design matrix and the research workflow already
    treats those as categorical context, not predictors.

    NaN / Inf values are also dropped so the imputer sees
    them as MISSING (per-cell), not as poison values that
    bias the GBC split finder.
    """
    if blob is None:
        return {}
    if isinstance(blob, dict):
        raw = blob
    elif isinstance(blob, (bytes, bytearray)):
        try:
            raw = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
    elif isinstance(blob, str):
        if not blob.strip():
            return {}
        try:
            raw = json.loads(blob)
        except json.JSONDecodeError:
            return {}
    else:
        return {}

    if not isinstance(raw, dict):
        return {}

    out: dict[str, float] = {}
    for k, v in raw.items():
        if v is None:
            continue
        if isinstance(v, bool):
            # bools sneak through ``isinstance(int)`` in Python
            # and silently become 0/1 in a design matrix. We
            # treat them as numeric since 0/1 is meaningful
            # for boolean features (e.g. ``is_in_position``).
            out[str(k)] = float(v)
            continue
        if isinstance(v, (int, float)):
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                continue
            out[str(k)] = fv
            continue
        if isinstance(v, str):
            # Try numeric coercion (the writer serializes
            # Decimals as strings to preserve precision).
            try:
                fv = float(v)
            except (TypeError, ValueError):
                # Genuine string category → drop.
                continue
            if math.isnan(fv) or math.isinf(fv):
                continue
            out[str(k)] = fv
            continue
        # Unknown type → drop silently.
    return out


def _build_design_matrix(
    parsed_rows: list[dict[str, float]],
) -> tuple[np.ndarray, list[str]]:
    """Column-align parsed feature dicts into a 2-D float
    ndarray + the matching feature-name list.

    Missing cells become ``np.nan`` — the GBC pipeline imputes
    them with the column median before fit.
    """
    feature_names: list[str] = sorted(
        {k for row in parsed_rows for k in row.keys()}
    )
    if not feature_names:
        # Empty universe — return an (n, 0) matrix so the
        # caller can detect the degenerate case cleanly.
        return np.empty((len(parsed_rows), 0), dtype=float), []

    n_rows = len(parsed_rows)
    n_cols = len(feature_names)
    X = np.full((n_rows, n_cols), np.nan, dtype=float)
    for i, row in enumerate(parsed_rows):
        for j, name in enumerate(feature_names):
            v = row.get(name)
            if v is None:
                continue
            X[i, j] = v
    return X, feature_names


def train_classifier(
    rows: list[dict[str, Any]],
    *,
    min_trades: int = 30,
) -> TrainedClassifier:
    """Train the GBC and return the fitted model + design matrix.

    Shared core used by both :func:`compute_feature_importance`
    (FE-11) and FE-12's SHAP analysis. Performs the same outcome
    derivation + features_json parse + median imputation +
    deterministic GBC fit; the only difference vs the importance
    code path is that this function exposes the fitted model and
    the post-imputation matrix so downstream callers can run
    per-row explanations.

    Args:
        rows: List of trade snapshot dicts (see
            :func:`compute_feature_importance` for the schema).
        min_trades: Raise ``InsufficientDataError`` when fewer
            than this many rows survive outcome derivation.

    Returns:
        :class:`TrainedClassifier` — fitted GBC + matrix + the
        row metadata SHAP needs to attribute predictions.

    Raises:
        InsufficientDataError: < ``min_trades`` labeled rows
            OR zero numeric features after parsing.
        ValueError: All labeled rows share the same outcome
            (GBC needs >= 2 classes).
    """
    if min_trades <= 0:
        raise ValueError("min_trades must be > 0")

    # ── Phase 1: outcome derivation + feature parsing. ────────
    parsed_rows: list[dict[str, float]] = []
    outcomes: list[int] = []
    fill_ids: list[str] = []
    strategy_id_seen: str | None = None
    period_start_seen: date | None = None
    period_end_seen: date | None = None
    for idx, r in enumerate(rows):
        y = _derive_outcome(r)
        if y is None:
            continue
        feats = _parse_features_json(r.get("features_json"))
        fid_raw = r.get("fill_id")
        if isinstance(fid_raw, str) and fid_raw.strip():
            fill_ids.append(fid_raw)
        else:
            fill_ids.append(f"row-{idx}")
        # Pull header context from the first labeled row.
        if strategy_id_seen is None:
            sid = r.get("strategy_id")
            if isinstance(sid, str):
                strategy_id_seen = sid
        if period_start_seen is None:
            ps = r.get("period_start")
            if isinstance(ps, date):
                period_start_seen = ps
        if period_end_seen is None:
            pe = r.get("period_end")
            if isinstance(pe, date):
                period_end_seen = pe
        parsed_rows.append(feats)
        outcomes.append(int(y))

    n_labeled = len(outcomes)
    if n_labeled < min_trades:
        raise InsufficientDataError(
            f"only {n_labeled} labeled trade(s) available; "
            f"need >= {min_trades} for stable importance "
            "ranking"
        )

    X_raw, feature_names = _build_design_matrix(parsed_rows)
    if not feature_names:
        raise InsufficientDataError(
            f"{n_labeled} labeled trade(s) carry zero numeric "
            "features after features_json parse — nothing to "
            "rank"
        )

    y_arr = np.asarray(outcomes, dtype=int)
    if len(np.unique(y_arr)) < 2:
        raise ValueError(
            "all labeled rows share a single outcome class; "
            "GradientBoostingClassifier requires at least two "
            "classes for a meaningful fit"
        )

    # ── Phase 2: impute + fit. ────────────────────────────────
    imputer = SimpleImputer(strategy="median")
    X = imputer.fit_transform(X_raw)

    clf = GradientBoostingClassifier(
        n_estimators=_GBC_N_ESTIMATORS,
        max_depth=_GBC_MAX_DEPTH,
        random_state=_GBC_RANDOM_STATE,
    )
    clf.fit(X, y_arr)

    _logger.info(
        "[feature-importance] train_classifier complete "
        "strategy_id=%s n_trades=%d n_features=%d",
        strategy_id_seen,
        n_labeled,
        len(feature_names),
    )

    return TrainedClassifier(
        model=clf,
        feature_columns=feature_names,
        X=X,
        y=y_arr,
        n_trades_used=n_labeled,
        fill_ids=fill_ids,
        classifier_version=_classifier_version(),
        strategy_id_seen=strategy_id_seen or "",
        period_start_seen=period_start_seen or date.min,
        period_end_seen=period_end_seen or date.min,
    )


def compute_feature_importance(
    rows: list[dict[str, Any]],
    *,
    top_n: int = 10,
    min_trades: int = 30,
) -> FeatureImportanceResult:
    """Train a GBC and return top-N features by importance.

    Thin wrapper over :func:`train_classifier` that extracts
    ``feature_importances_`` from the fitted model and returns
    the ranked list. The training pipeline is DRY-shared with
    FE-12's SHAP analysis to guarantee the two surfaces explain
    the SAME classifier fit.

    Args:
        rows: List of trade snapshot dicts. Each row must
            carry at least ``features_json`` (the str-encoded
            feature map) plus EITHER ``outcome_label`` OR
            ``realised_pnl_inr``. ``strategy_id``,
            ``period_start``, ``period_end`` are also read for
            the result metadata.
        top_n: Maximum number of features to return.
        min_trades: Raise ``InsufficientDataError`` when fewer
            than this many rows survive outcome derivation.

    Returns:
        :class:`FeatureImportanceResult` — sorted descending
        by importance.

    Raises:
        InsufficientDataError: < ``min_trades`` labeled rows.
        ValueError: All labeled rows share the same outcome
            (GBC needs at least two classes to fit).
    """
    if top_n <= 0:
        raise ValueError("top_n must be > 0")

    trained = train_classifier(rows, min_trades=min_trades)

    importances = trained.model.feature_importances_
    ranked: list[FeatureScore] = sorted(
        (
            FeatureScore(name=n, importance=float(i))
            for n, i in zip(trained.feature_columns, importances)
        ),
        key=lambda fs: fs.importance,
        reverse=True,
    )[:top_n]

    fitted_at = datetime.now(timezone.utc)
    _logger.info(
        "[feature-importance] fit complete strategy_id=%s "
        "n_trades=%d n_features=%d top_n=%d",
        trained.strategy_id_seen,
        trained.n_trades_used,
        len(trained.feature_columns),
        len(ranked),
    )

    return FeatureImportanceResult(
        strategy_id=trained.strategy_id_seen,
        period_start=trained.period_start_seen,
        period_end=trained.period_end_seen,
        n_trades_used=trained.n_trades_used,
        n_features=len(trained.feature_columns),
        top_features=ranked,
        classifier_version=trained.classifier_version,
        fitted_at=fitted_at,
    )


__all__ = [
    "FeatureImportanceResult",
    "FeatureScore",
    "InsufficientDataError",
    "TrainedClassifier",
    "compute_feature_importance",
    "train_classifier",
]
