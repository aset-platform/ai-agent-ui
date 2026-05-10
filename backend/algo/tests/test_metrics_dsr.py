"""Deflated Sharpe Ratio tests — anchored on Bailey (2014) sample."""
from __future__ import annotations

import pytest

from backend.algo.backtest.metrics import (
    _expected_max_sharpe,
    deflated_sharpe_ratio,
)


def test_dsr_in_unit_interval() -> None:
    out = deflated_sharpe_ratio(
        obs_sharpe=1.2, n_trials=10, sample_length=252,
        skew=0.0, kurt=3.0,
    )
    assert 0.0 <= out <= 1.0


def test_dsr_higher_obs_sharpe_higher_dsr() -> None:
    common = dict(n_trials=10, sample_length=252, skew=0.0, kurt=3.0)
    low = deflated_sharpe_ratio(obs_sharpe=0.5, **common)
    mid = deflated_sharpe_ratio(obs_sharpe=1.5, **common)
    high = deflated_sharpe_ratio(obs_sharpe=2.5, **common)
    assert low < mid < high


def test_dsr_more_trials_lower_dsr() -> None:
    """Same observed Sharpe, more variants tried → lower DSR
    (multiple-comparison deflation)."""
    common = dict(obs_sharpe=1.5, sample_length=252, skew=0.0, kurt=3.0)
    n_few = deflated_sharpe_ratio(n_trials=2, **common)
    n_many = deflated_sharpe_ratio(n_trials=50, **common)
    assert n_few > n_many


def test_dsr_negative_skew_lowers_dsr() -> None:
    """Negative skew (left tail) → lower DSR vs zero-skew baseline
    for same Sharpe (left-tail risk penalty).

    Effect direction depends on (SR - sr0): when SR > sr0 the
    skew penalty inflates the denominator and shrinks the z, so
    DSR(left) < DSR(flat). We pick SR > sr0(N=10) ≈ 1.575."""
    common = dict(
        obs_sharpe=2.0, n_trials=10, sample_length=20, kurt=3.0,
    )
    flat = deflated_sharpe_ratio(skew=0.0, **common)
    left = deflated_sharpe_ratio(skew=-0.5, **common)
    assert left < flat


def test_dsr_short_sample_returns_zero() -> None:
    """T <= 1 cannot produce a meaningful DSR."""
    out = deflated_sharpe_ratio(
        obs_sharpe=1.5, n_trials=10, sample_length=1,
        skew=0.0, kurt=3.0,
    )
    assert out == 0.0


def test_expected_max_sharpe_bailey_table() -> None:
    """E[max SR] under null with N=10 trials. Closed form
    (1-γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(Ne)) ≈ 1.575."""
    val = _expected_max_sharpe(10)
    assert val == pytest.approx(1.575, abs=0.01)


def test_expected_max_sharpe_monotone_increasing() -> None:
    """More trials → higher expected max SR under null."""
    a = _expected_max_sharpe(5)
    b = _expected_max_sharpe(50)
    c = _expected_max_sharpe(500)
    assert a < b < c


def test_expected_max_sharpe_zero_for_single_trial() -> None:
    assert _expected_max_sharpe(1) == 0.0
