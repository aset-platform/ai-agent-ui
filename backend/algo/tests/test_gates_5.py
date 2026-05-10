"""5-gate acceptance tests."""
from __future__ import annotations

import math

from backend.algo.backtest.gates import (
    GateThresholds,
    all_gates_pass,
    evaluate_5_gates,
)
from backend.algo.backtest.metrics import PerRegimeMetrics


def _good_per_regime() -> list[PerRegimeMetrics]:
    return [
        PerRegimeMetrics("BULL", 100, 5.0, 1.0, 1.5, 3.0, 0.6),
        PerRegimeMetrics("SIDEWAYS", 50, 1.0, 0.5, 0.7, 5.0, 0.5),
        PerRegimeMetrics("BEAR", 30, 0.0, 0.0, 0.0, 8.0, 0.5),
    ]


def test_all_pass() -> None:
    """All five gates pass with healthy inputs."""
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=10.0,
        recovery_months=6,
        per_regime=_good_per_regime(),
        dsr=0.97,
        pbo=0.20,
    )
    assert gates == {
        "max_dd_ok": True,
        "recovery_ok": True,
        "per_regime_non_neg": True,
        "dsr_ok": True,
        "pbo_ok": True,
    }
    assert all_gates_pass(gates)


def test_max_dd_fails_individually() -> None:
    """Max DD over threshold fails just that gate."""
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=30.0,  # above 25
        recovery_months=6,
        per_regime=_good_per_regime(),
        dsr=0.97,
        pbo=0.20,
    )
    assert gates["max_dd_ok"] is False
    assert all(v for k, v in gates.items() if k != "max_dd_ok")
    assert not all_gates_pass(gates)


def test_recovery_fails_individually() -> None:
    """Recovery > 18 months fails just that gate."""
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=10.0,
        recovery_months=24,
        per_regime=_good_per_regime(),
        dsr=0.97,
        pbo=0.20,
    )
    assert gates["recovery_ok"] is False
    assert all(v for k, v in gates.items() if k != "recovery_ok")


def test_per_regime_negative_fails() -> None:
    """Any negative regime cum_return fails just that gate."""
    bad = _good_per_regime()
    bad[2] = PerRegimeMetrics("BEAR", 30, -2.0, 0.0, 0.0, 12.0, 0.4)
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=10.0,
        recovery_months=6,
        per_regime=bad,
        dsr=0.97,
        pbo=0.20,
    )
    assert gates["per_regime_non_neg"] is False
    assert all(v for k, v in gates.items() if k != "per_regime_non_neg")


def test_dsr_below_threshold_fails() -> None:
    """DSR < 0.95 fails just that gate."""
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=10.0,
        recovery_months=6,
        per_regime=_good_per_regime(),
        dsr=0.80,
        pbo=0.20,
    )
    assert gates["dsr_ok"] is False
    assert all(v for k, v in gates.items() if k != "dsr_ok")


def test_pbo_above_threshold_fails() -> None:
    """PBO > 0.30 fails just that gate."""
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=10.0,
        recovery_months=6,
        per_regime=_good_per_regime(),
        dsr=0.97,
        pbo=0.50,
    )
    assert gates["pbo_ok"] is False
    assert all(v for k, v in gates.items() if k != "pbo_ok")


def test_pbo_none_passes_gate() -> None:
    """PBO=None (V2-2 single strategy) passes the PBO gate."""
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=10.0,
        recovery_months=6,
        per_regime=_good_per_regime(),
        dsr=0.97,
        pbo=None,
    )
    assert gates["pbo_ok"] is True


def test_pbo_nan_passes_gate() -> None:
    """PBO=NaN (CSCV preconditions failed) passes the PBO gate."""
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=10.0,
        recovery_months=6,
        per_regime=_good_per_regime(),
        dsr=0.97,
        pbo=float("nan"),
    )
    assert gates["pbo_ok"] is True


def test_empty_per_regime_passes_when_required() -> None:
    """Empty per-regime list passes the per_regime gate (no
    rows means nothing to fail)."""
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=10.0,
        recovery_months=6,
        per_regime=[],
        dsr=0.97,
        pbo=0.20,
    )
    assert gates["per_regime_non_neg"] is True


def test_per_regime_disabled_via_threshold() -> None:
    """When require_per_regime_non_negative=False, the gate
    always passes regardless of per-regime returns."""
    bad = [
        PerRegimeMetrics("BEAR", 30, -50.0, 0, 0, 30, 0.0),
    ]
    th = GateThresholds(require_per_regime_non_negative=False)
    gates = evaluate_5_gates(
        aggregate_max_dd_pct=10.0,
        recovery_months=6,
        per_regime=bad,
        dsr=0.97,
        pbo=0.20,
        thresholds=th,
    )
    assert gates["per_regime_non_neg"] is True


def test_all_gates_pass_helper_empty_dict() -> None:
    """Empty dict is falsy - cannot pass nothing."""
    assert all_gates_pass({}) is False
