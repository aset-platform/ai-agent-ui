"""REGIME-5 env-flag toggle for the 5-gate live check.

Verifies that:
  * default (env unset) → behaves like V2-2 (5-gate logic skipped)
  * env=1 + gates pass → live-toggle still enabled
  * env=1 + any gate fails → walkforward_recent = False
"""
from __future__ import annotations

import os

import pytest


def _evaluate_5gate_env_flag(
    walkforward_recent: bool,
    aggregate: dict,
    env_value: str | None,
) -> bool:
    """Mirror the in-line check in routes/live.py::_check_gates."""
    if env_value == "1" and walkforward_recent:
        gates = aggregate.get("gates_passed") or {}
        if not gates or not all(gates.values()):
            return False
    return walkforward_recent


def test_env_off_skips_5gate_check():
    aggregate = {
        "avg_pnl_pct": "1.5",
        "gates_passed": {"max_dd_ok": False},  # would fail if checked
    }
    assert _evaluate_5gate_env_flag(True, aggregate, None) is True
    assert _evaluate_5gate_env_flag(True, aggregate, "0") is True


def test_env_on_all_pass_keeps_recent():
    aggregate = {
        "gates_passed": {
            "max_dd_ok": True,
            "recovery_ok": True,
            "per_regime_non_neg": True,
            "dsr_ok": True,
            "pbo_ok": True,
        }
    }
    assert _evaluate_5gate_env_flag(True, aggregate, "1") is True


def test_env_on_partial_fail_blocks():
    aggregate = {
        "gates_passed": {
            "max_dd_ok": True,
            "pbo_ok": False,  # one fail
        }
    }
    assert _evaluate_5gate_env_flag(True, aggregate, "1") is False


def test_env_on_legacy_aggregate_blocks():
    """Pre-REGIME-5 walkforward (no gates_passed at all)
    must NOT pass the 5-gate gate when env=1."""
    aggregate = {"avg_pnl_pct": "1.5"}
    assert _evaluate_5gate_env_flag(True, aggregate, "1") is False


def test_env_on_walkforward_already_failed_unchanged():
    """If walkforward_recent is False (e.g. > 30d old), env flag
    cannot magically make it True."""
    aggregate = {
        "gates_passed": {
            "max_dd_ok": True, "recovery_ok": True,
            "per_regime_non_neg": True, "dsr_ok": True,
            "pbo_ok": True,
        }
    }
    assert _evaluate_5gate_env_flag(False, aggregate, "1") is False
