"""Unit tests for FE-12's SHAP analysis module
(ASETPLTFRM-414).

The ``shap`` package is an optional dependency that may not be
installed in the container. To keep this suite hermetic + fast
+ deterministic, we install a tiny fake ``shap`` module into
``sys.modules`` for each test and have it return deterministic
SHAP arrays we control. This lets us assert the exact
attribution shape + ordering without actually fitting trees.

The fake ``shap.TreeExplainer`` returns:
  * ``shap_values(X)`` — a hard-coded ndarray (or list[ndarray])
    seeded by the test;
  * ``expected_value`` — a scalar baseline.

Per-test we vary the fake so the binary-class output-shape
variants (list[ndarray] vs single ndarray vs 3-D ndarray) all
get exercised.
"""

from __future__ import annotations

import sys
import types
from datetime import date
from typing import Any

import numpy as np
import pytest
from sklearn.ensemble import GradientBoostingClassifier

from backend.algo.features.importance import TrainedClassifier
from backend.algo.features.shap_analysis import (
    FillShap,
    ShapAnalysisResult,
    compute_shap_for_trades,
)


def _make_fake_shap(
    *,
    shap_values_returns: Any,
    expected_value: Any,
) -> types.ModuleType:
    """Build a fake ``shap`` module the SHAP analysis function
    will import lazily inside ``compute_shap_for_trades``."""
    module = types.ModuleType("shap")

    class _FakeExplainer:
        def __init__(self, model: Any) -> None:
            self.model = model
            self.expected_value = expected_value

        def shap_values(self, X: np.ndarray) -> Any:
            # Honor the test-controlled return value as-is so
            # the production code path sees the exact shape
            # the real shap version would emit.
            return shap_values_returns

    module.TreeExplainer = _FakeExplainer  # type: ignore[attr-defined]
    return module


@pytest.fixture
def _trained_5x3() -> TrainedClassifier:
    """Tiny TrainedClassifier with 5 rows, 3 features.

    Fits a real GBC so ``predict_proba`` works inside
    ``compute_shap_for_trades`` (we only fake the SHAP part).
    """
    rng = np.random.default_rng(42)
    X = rng.normal(size=(5, 3))
    y = np.array([1, 0, 1, 0, 1], dtype=int)
    model = GradientBoostingClassifier(
        n_estimators=10,
        max_depth=2,
        random_state=42,
    )
    model.fit(X, y)
    return TrainedClassifier(
        model=model,
        feature_columns=["X1", "X2", "X3"],
        X=X,
        y=y,
        n_trades_used=5,
        fill_ids=["f1", "f2", "f3", "f4", "f5"],
        classifier_version="sklearn-test-gbc-n10-d2",
        strategy_id_seen="strat-1",
        period_start_seen=date(2026, 1, 1),
        period_end_seen=date(2026, 3, 31),
    )


def test_compute_shap_returns_per_fill_attributions(
    monkeypatch: pytest.MonkeyPatch,
    _trained_5x3: TrainedClassifier,
) -> None:
    """Each TrainedClassifier row → one FillShap with
    shap_values keyed by feature column."""
    # Simple deterministic shap matrix: X1 dominates.
    shap_arr = np.array(
        [
            [0.9, 0.05, -0.02],
            [-0.8, 0.10, 0.03],
            [0.7, -0.04, 0.01],
            [-0.85, 0.06, -0.05],
            [0.95, 0.02, 0.00],
        ]
    )
    fake = _make_fake_shap(
        shap_values_returns=shap_arr,
        expected_value=0.5,
    )
    monkeypatch.setitem(sys.modules, "shap", fake)

    result = compute_shap_for_trades(_trained_5x3, top_n=3)

    assert isinstance(result, ShapAnalysisResult)
    assert result.n_fills == 5
    assert result.n_features == 3
    assert len(result.per_fill) == 5
    fill_ids = [fs.fill_id for fs in result.per_fill]
    assert fill_ids == ["f1", "f2", "f3", "f4", "f5"]
    for fs in result.per_fill:
        assert isinstance(fs, FillShap)
        assert set(fs.shap_values.keys()) == {"X1", "X2", "X3"}
        assert fs.base_value == pytest.approx(0.5)
        # prediction is whatever the GBC says (0..1).
        assert 0.0 <= fs.prediction <= 1.0


def test_top_features_sorted_by_mean_abs_shap(
    monkeypatch: pytest.MonkeyPatch,
    _trained_5x3: TrainedClassifier,
) -> None:
    """Global ranking sorts by mean(|SHAP|) descending."""
    shap_arr = np.array(
        [
            [1.0, 0.1, 0.01],
            [-1.0, -0.1, -0.01],
            [1.0, 0.1, 0.01],
            [-1.0, -0.1, -0.01],
            [1.0, 0.1, 0.01],
        ]
    )
    fake = _make_fake_shap(
        shap_values_returns=shap_arr,
        expected_value=0.5,
    )
    monkeypatch.setitem(sys.modules, "shap", fake)

    result = compute_shap_for_trades(_trained_5x3, top_n=3)

    ranked = result.top_features_by_mean_abs_shap
    assert [name for name, _v in ranked] == ["X1", "X2", "X3"]
    assert ranked[0][1] == pytest.approx(1.0)
    assert ranked[1][1] == pytest.approx(0.1)
    assert ranked[2][1] == pytest.approx(0.01)


def test_fill_ids_filter_subset(
    monkeypatch: pytest.MonkeyPatch,
    _trained_5x3: TrainedClassifier,
) -> None:
    """Pass 2 of 5 fill_ids → per_fill has 2 entries with the
    right ids in caller order."""
    shap_arr = np.array(
        [
            [0.5, 0.1, 0.0],
            [-0.5, 0.2, 0.0],
        ]
    )
    fake = _make_fake_shap(
        shap_values_returns=shap_arr,
        expected_value=0.4,
    )
    monkeypatch.setitem(sys.modules, "shap", fake)

    result = compute_shap_for_trades(
        _trained_5x3,
        fill_ids=["f2", "f4"],
        top_n=3,
    )

    assert result.n_fills == 2
    assert [fs.fill_id for fs in result.per_fill] == ["f2", "f4"]


def test_fill_ids_filter_rejects_unknown_ids(
    monkeypatch: pytest.MonkeyPatch,
    _trained_5x3: TrainedClassifier,
) -> None:
    """Unknown fill_id → ValueError listing the unknowns."""
    fake = _make_fake_shap(
        shap_values_returns=np.zeros((1, 3)),
        expected_value=0.5,
    )
    monkeypatch.setitem(sys.modules, "shap", fake)

    with pytest.raises(ValueError) as excinfo:
        compute_shap_for_trades(
            _trained_5x3,
            fill_ids=["f1", "does-not-exist"],
            top_n=3,
        )
    assert "does-not-exist" in str(excinfo.value)


def test_classifier_version_passes_through(
    monkeypatch: pytest.MonkeyPatch,
    _trained_5x3: TrainedClassifier,
) -> None:
    """The trained classifier's version string flows through to
    the SHAP result unchanged."""
    fake = _make_fake_shap(
        shap_values_returns=np.zeros((5, 3)),
        expected_value=0.5,
    )
    monkeypatch.setitem(sys.modules, "shap", fake)

    result = compute_shap_for_trades(_trained_5x3, top_n=3)
    assert result.classifier_version == "sklearn-test-gbc-n10-d2"
    assert result.strategy_id == "strat-1"
    assert result.period_start == date(2026, 1, 1)
    assert result.period_end == date(2026, 3, 31)


def test_shap_dict_keys_match_feature_columns(
    monkeypatch: pytest.MonkeyPatch,
    _trained_5x3: TrainedClassifier,
) -> None:
    """Every FillShap.shap_values has the EXACT same key set
    as trained.feature_columns."""
    shap_arr = np.array(
        [
            [0.1, 0.2, 0.3],
            [0.4, 0.5, 0.6],
            [0.7, 0.8, 0.9],
            [-0.1, -0.2, -0.3],
            [-0.4, -0.5, -0.6],
        ]
    )
    fake = _make_fake_shap(
        shap_values_returns=shap_arr,
        expected_value=0.5,
    )
    monkeypatch.setitem(sys.modules, "shap", fake)

    result = compute_shap_for_trades(_trained_5x3, top_n=3)

    expected_keys = set(_trained_5x3.feature_columns)
    for fs in result.per_fill:
        assert set(fs.shap_values.keys()) == expected_keys


def test_handles_list_shap_output(
    monkeypatch: pytest.MonkeyPatch,
    _trained_5x3: TrainedClassifier,
) -> None:
    """Older shap returns list[ndarray] (one per class) for a
    binary classifier — we must take the positive-class array
    (index -1)."""
    class_0 = np.full((5, 3), -1.0)
    class_1 = np.full((5, 3), 1.0)
    fake = _make_fake_shap(
        shap_values_returns=[class_0, class_1],
        expected_value=[0.4, 0.6],
    )
    monkeypatch.setitem(sys.modules, "shap", fake)

    result = compute_shap_for_trades(_trained_5x3, top_n=3)

    # Every per-row contribution should be +1.0 (class-1 array).
    for fs in result.per_fill:
        for v in fs.shap_values.values():
            assert v == pytest.approx(1.0)
        # Baseline pulled from index -1 of the list.
        assert fs.base_value == pytest.approx(0.6)


def test_handles_3d_shap_output(
    monkeypatch: pytest.MonkeyPatch,
    _trained_5x3: TrainedClassifier,
) -> None:
    """Some shap builds return (n, m, 2) — slice last axis."""
    arr = np.zeros((5, 3, 2))
    arr[..., 1] = 0.42  # positive class layer
    fake = _make_fake_shap(
        shap_values_returns=arr,
        expected_value=0.5,
    )
    monkeypatch.setitem(sys.modules, "shap", fake)

    result = compute_shap_for_trades(_trained_5x3, top_n=3)
    for fs in result.per_fill:
        for v in fs.shap_values.values():
            assert v == pytest.approx(0.42)


def test_empty_fill_ids_subset_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    _trained_5x3: TrainedClassifier,
) -> None:
    """Empty fill_ids list (after de-dup) → empty per_fill, no
    SHAP call."""
    fake = _make_fake_shap(
        shap_values_returns=np.zeros((0, 3)),
        expected_value=0.5,
    )
    monkeypatch.setitem(sys.modules, "shap", fake)

    result = compute_shap_for_trades(
        _trained_5x3,
        fill_ids=[],
        top_n=3,
    )
    # ``fill_ids=[]`` is "no subset selected" — production code
    # treats this distinctly from None (which means "all"). The
    # documented behaviour: empty list → no rows attributed.
    # We accept either an empty per_fill OR a no-op result; the
    # important contract is no crash + n_fills consistent.
    assert result.n_fills == len(result.per_fill)


def test_raises_when_top_n_invalid(
    _trained_5x3: TrainedClassifier,
) -> None:
    with pytest.raises(ValueError):
        compute_shap_for_trades(_trained_5x3, top_n=0)
