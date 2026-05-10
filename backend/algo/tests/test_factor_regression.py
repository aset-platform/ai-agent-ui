"""Factor regression tests — synthetic data with known coefs."""
from __future__ import annotations

import numpy as np
import pytest

from backend.algo.attribution.factor_regression import (
    FactorRegressionResult,
    fit_ols_regression,
)


def test_extracts_known_alpha_and_betas() -> None:
    """Generate noisy returns from a known model and verify
    that OLS recovers alpha + each beta within a tight tolerance.
    """
    rng = np.random.default_rng(42)
    n = 252
    mkt = rng.normal(0.0005, 0.01, n)
    smb = rng.normal(0.0001, 0.005, n)
    hml = rng.normal(0.0002, 0.005, n)
    eps = rng.normal(0, 0.001, n)
    strategy = (
        0.0003 + 0.8 * mkt + 0.3 * smb + -0.1 * hml + eps
    )
    factors = {"MKT": mkt, "SMB": smb, "HML": hml}

    out = fit_ols_regression(strategy, factors)
    assert isinstance(out, FactorRegressionResult)
    assert out.alpha == pytest.approx(0.0003, abs=0.0002)
    assert out.betas["MKT"] == pytest.approx(0.8, abs=0.02)
    assert out.betas["SMB"] == pytest.approx(0.3, abs=0.05)
    assert out.betas["HML"] == pytest.approx(-0.1, abs=0.05)
    assert 0.95 <= out.r_squared <= 1.0
    assert out.n_observations == n


def test_short_history_returns_nan_alpha() -> None:
    """Below MIN_OBSERVATIONS rows the fit refuses; alpha is NaN
    and n_observations reports the actual sample size."""
    out = fit_ols_regression(
        np.array([0.01, -0.01, 0.02]),
        {"MKT": np.array([0.005, -0.003, 0.01])},
    )
    assert np.isnan(out.alpha)
    assert out.n_observations == 3


def test_no_factors_returns_nan() -> None:
    """An empty factors dict is a programming error; we degrade
    gracefully with NaN alpha rather than raising."""
    out = fit_ols_regression(np.zeros(100), {})
    assert np.isnan(out.alpha)
    assert out.n_observations == 0


def test_drops_nan_rows_before_fit() -> None:
    """A NaN in either y or any factor column at row i drops
    that row from the regression."""
    rng = np.random.default_rng(7)
    n = 60
    mkt = rng.normal(0.0005, 0.01, n)
    strategy = 0.5 * mkt + rng.normal(0, 0.0005, n)
    # Poison rows 0, 5, 10 in y; row 7 in mkt.
    strategy[0] = np.nan
    strategy[5] = np.nan
    strategy[10] = np.nan
    mkt[7] = np.nan

    out = fit_ols_regression(strategy, {"MKT": mkt})
    assert out.n_observations == n - 4
    assert out.betas["MKT"] == pytest.approx(0.5, abs=0.05)


def test_length_mismatch_returns_nan() -> None:
    out = fit_ols_regression(
        np.zeros(100), {"MKT": np.zeros(50)},
    )
    assert np.isnan(out.alpha)
    assert out.n_observations == 0
