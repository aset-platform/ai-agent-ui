"""Unit tests for the FE-11 feature-importance compute module.

Covers outcome-derivation priority, non-numeric feature
filtering, the ``InsufficientDataError`` floor, classifier
version stamping, and sort stability.
"""

from __future__ import annotations

import json
import math
from datetime import date

import pytest
import sklearn

from backend.algo.features.importance import (
    FeatureImportanceResult,
    InsufficientDataError,
    compute_feature_importance,
)


def _synth_row(
    *,
    pnl: float | None = None,
    label: str | None = None,
    features: dict | None = None,
    strategy_id: str = "11111111-1111-1111-1111-111111111111",
    period_start: date = date(2026, 1, 1),
    period_end: date = date(2026, 3, 31),
) -> dict:
    return {
        "strategy_id": strategy_id,
        "period_start": period_start,
        "period_end": period_end,
        "features_json": json.dumps(features or {}),
        "realised_pnl_inr": pnl,
        "outcome_label": label,
    }


def _signal_row(*, sig: float, pnl: float) -> dict:
    """Row where one feature ``perfect_signal`` is perfectly
    correlated with the outcome derived from ``pnl``."""
    feats = {
        "perfect_signal": sig,
        "noise_1": 0.1,
        "noise_2": -0.05,
        "rsi_14": 50.0,
    }
    return _synth_row(features=feats, pnl=pnl)


def _gen_perfectly_correlated_dataset(n: int) -> list[dict]:
    """``n`` rows where ``perfect_signal`` perfectly predicts
    outcome. Splits half winners (sig=+1, pnl=+10) and half
    losers (sig=-1, pnl=-10) so GBC has two balanced classes.
    """
    rows: list[dict] = []
    for i in range(n):
        if i % 2 == 0:
            rows.append(_signal_row(sig=1.0, pnl=10.0))
        else:
            rows.append(_signal_row(sig=-1.0, pnl=-10.0))
    return rows


def test_compute_feature_importance_happy_path() -> None:
    rows = _gen_perfectly_correlated_dataset(100)
    result = compute_feature_importance(
        rows,
        top_n=10,
        min_trades=30,
    )

    assert isinstance(result, FeatureImportanceResult)
    assert result.n_trades_used == 100
    assert len(result.top_features) <= 10
    # ``perfect_signal`` must be rank-1 because it's the only
    # feature with any predictive power.
    assert result.top_features[0].name == "perfect_signal"
    assert result.top_features[0].importance > 0.5


def test_handles_non_numeric_features() -> None:
    """Mixed numeric + string features: string keys are
    silently dropped from the design matrix; numeric keys are
    still ranked."""
    rows: list[dict] = []
    for i in range(60):
        sig = 1.0 if i % 2 == 0 else -1.0
        pnl = 5.0 if sig > 0 else -5.0
        feats = {
            "perfect_signal": sig,
            "time_of_day_bucket": "opening",
            "regime_label": "bull",
            "rsi_14": 55.0,
        }
        rows.append(_synth_row(features=feats, pnl=pnl))

    result = compute_feature_importance(
        rows,
        top_n=10,
        min_trades=30,
    )

    feat_names = {fs.name for fs in result.top_features}
    assert "time_of_day_bucket" not in feat_names
    assert "regime_label" not in feat_names
    assert "perfect_signal" in feat_names
    # n_features tracks DISTINCT numeric features observed.
    assert result.n_features == 2  # perfect_signal + rsi_14


def test_raises_insufficient_data_below_min_trades() -> None:
    rows = _gen_perfectly_correlated_dataset(5)
    with pytest.raises(InsufficientDataError):
        compute_feature_importance(
            rows,
            top_n=10,
            min_trades=30,
        )


def test_outcome_from_realised_pnl_when_label_missing() -> None:
    """``outcome_label=None`` + ``realised_pnl_inr`` populated
    → outcome derived from pnl sign."""
    rows: list[dict] = []
    for i in range(60):
        # Winner if sig>0, loser if sig<0. No label populated
        # — outcome MUST fall back to pnl-sign.
        sig = 1.0 if i % 2 == 0 else -1.0
        pnl = 12.0 if sig > 0 else -3.0
        rows.append(
            _synth_row(
                features={
                    "perfect_signal": sig,
                    "noise": 0.0,
                },
                pnl=pnl,
                label=None,
            )
        )
    result = compute_feature_importance(
        rows,
        top_n=5,
        min_trades=30,
    )
    assert result.n_trades_used == 60
    assert result.top_features[0].name == "perfect_signal"


def test_outcome_from_label_when_present() -> None:
    """``outcome_label`` takes precedence over pnl sign.

    Construct adversarial rows where pnl says LOSER but label
    says WINNER — the fit should track the label, NOT the pnl.
    """
    rows: list[dict] = []
    for i in range(60):
        # Even rows: label=winner, pnl=-1 (sign would disagree).
        # Odd rows : label=loser , pnl=+1 (sign would disagree).
        if i % 2 == 0:
            rows.append(
                _synth_row(
                    features={"perfect_signal": 1.0},
                    pnl=-1.0,
                    label="winner",
                )
            )
        else:
            rows.append(
                _synth_row(
                    features={"perfect_signal": -1.0},
                    pnl=1.0,
                    label="loser",
                )
            )
    result = compute_feature_importance(
        rows,
        top_n=5,
        min_trades=30,
    )
    assert result.n_trades_used == 60
    # If the label drove training (not pnl sign), then
    # perfect_signal=+1 must map to class 1 and -1 to class 0
    # — i.e. perfect_signal is still rank-1 with high score.
    assert result.top_features[0].name == "perfect_signal"
    assert result.top_features[0].importance > 0.5


def test_breakeven_label_maps_to_loser() -> None:
    """``"breakeven"`` label → class 0 (loser side)."""
    rows: list[dict] = []
    # 30 winners + 30 breakeven labelled.
    for i in range(30):
        rows.append(
            _synth_row(
                features={"perfect_signal": 1.0},
                pnl=None,
                label="winner",
            )
        )
    for i in range(30):
        rows.append(
            _synth_row(
                features={"perfect_signal": -1.0},
                pnl=None,
                label="breakeven",
            )
        )
    result = compute_feature_importance(
        rows,
        top_n=5,
        min_trades=30,
    )
    assert result.n_trades_used == 60
    assert result.top_features[0].name == "perfect_signal"


def test_drops_rows_with_no_outcome() -> None:
    """Rows with BOTH ``outcome_label=None`` AND
    ``realised_pnl_inr`` None/NaN must be filtered out before
    the count is checked against ``min_trades``."""
    # 30 labelled valid rows + 100 un-labeled rows. After
    # drop, only 30 survive — exactly meets ``min_trades=30``.
    rows = _gen_perfectly_correlated_dataset(30)
    for _ in range(100):
        rows.append(
            _synth_row(
                features={"perfect_signal": 1.0},
                pnl=None,
                label=None,
            )
        )
    result = compute_feature_importance(
        rows,
        top_n=5,
        min_trades=30,
    )
    assert result.n_trades_used == 30

    # Drop NaN pnl too.
    rows_nan: list[dict] = list(_gen_perfectly_correlated_dataset(30))
    for _ in range(10):
        rows_nan.append(
            _synth_row(
                features={"perfect_signal": 1.0},
                pnl=float("nan"),
                label=None,
            )
        )
    result_nan = compute_feature_importance(
        rows_nan,
        top_n=5,
        min_trades=30,
    )
    assert result_nan.n_trades_used == 30


def test_classifier_version_includes_sklearn_version() -> None:
    rows = _gen_perfectly_correlated_dataset(50)
    result = compute_feature_importance(
        rows,
        top_n=5,
        min_trades=30,
    )
    assert "sklearn-" in result.classifier_version
    assert sklearn.__version__ in result.classifier_version
    assert "gbc" in result.classifier_version


def test_top_n_default_is_10() -> None:
    """Build a dataset with 25 numeric features and confirm
    the default top_n cap surfaces only 10 of them."""
    rows: list[dict] = []
    for i in range(80):
        sig = 1.0 if i % 2 == 0 else -1.0
        pnl = 5.0 if sig > 0 else -5.0
        feats = {f"feat_{n:02d}": float(n + (i % 3)) for n in range(25)}
        feats["perfect_signal"] = sig
        rows.append(_synth_row(features=feats, pnl=pnl))
    result = compute_feature_importance(
        rows,
        min_trades=30,
    )
    assert len(result.top_features) == 10


def test_returns_features_sorted_descending() -> None:
    rows = _gen_perfectly_correlated_dataset(60)
    result = compute_feature_importance(
        rows,
        top_n=10,
        min_trades=30,
    )
    scores = [fs.importance for fs in result.top_features]
    for a, b in zip(scores, scores[1:]):
        assert a >= b, (
            f"importance scores not monotonically " f"non-increasing: {scores}"
        )


def test_raises_when_all_rows_share_one_outcome() -> None:
    """GBC needs >= 2 classes — surface as ValueError."""
    rows = [
        _synth_row(
            features={"perfect_signal": 1.0},
            pnl=5.0,
        )
        for _ in range(40)
    ]
    with pytest.raises(ValueError):
        compute_feature_importance(
            rows,
            top_n=5,
            min_trades=30,
        )


def test_features_json_handles_decimal_strings() -> None:
    """Snapshot writer encodes Decimal features as strings to
    preserve precision; parser must coerce them back to
    floats and still rank correctly."""
    rows: list[dict] = []
    for i in range(60):
        if i % 2 == 0:
            rows.append(
                _synth_row(
                    features={
                        "perfect_signal": "1.000000",
                        "rsi_14": "55.123456",
                    },
                    pnl=5.0,
                )
            )
        else:
            rows.append(
                _synth_row(
                    features={
                        "perfect_signal": "-1.000000",
                        "rsi_14": "44.987654",
                    },
                    pnl=-5.0,
                )
            )
    result = compute_feature_importance(
        rows,
        top_n=5,
        min_trades=30,
    )
    assert result.n_trades_used == 60
    feat_names = {fs.name for fs in result.top_features}
    assert "perfect_signal" in feat_names


def test_invalid_features_json_drops_row_features() -> None:
    """Garbage ``features_json`` (non-JSON string) leaves the
    row WITHOUT features but doesn't crash. The row still
    counts toward the labeled-row floor (outcome derivable),
    but the design matrix sees only NaN for that row."""
    rows = _gen_perfectly_correlated_dataset(40)
    # Replace one row's features_json with garbage.
    rows[0]["features_json"] = "not-json-at-all"
    result = compute_feature_importance(
        rows,
        top_n=5,
        min_trades=30,
    )
    # Still trained; n_trades_used unchanged.
    assert result.n_trades_used == 40


def test_empty_features_raises_insufficient_data() -> None:
    """40 labeled rows, every features_json = ``{}`` → zero
    numeric features to rank → InsufficientDataError."""
    rows = [_synth_row(features={}, pnl=5.0) for _ in range(20)] + [
        _synth_row(features={}, pnl=-5.0) for _ in range(20)
    ]
    with pytest.raises(InsufficientDataError):
        compute_feature_importance(
            rows,
            top_n=5,
            min_trades=30,
        )


def test_nan_inf_feature_values_dropped() -> None:
    """NaN / Inf feature cells are treated as MISSING (imputed
    to column median), not as poison values that bias the
    split finder."""
    rows: list[dict] = []
    for i in range(60):
        sig = 1.0 if i % 2 == 0 else -1.0
        pnl = 5.0 if sig > 0 else -5.0
        feats = {
            "perfect_signal": sig,
            "noisy": float("nan") if i % 4 == 0 else 0.1,
            "infy": math.inf if i % 5 == 0 else 0.2,
        }
        rows.append(_synth_row(features=feats, pnl=pnl))
    # Should fit without raising — imputer handles NaN columns.
    result = compute_feature_importance(
        rows,
        top_n=5,
        min_trades=30,
    )
    assert result.top_features[0].name == "perfect_signal"
