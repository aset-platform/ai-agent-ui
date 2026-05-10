"""OLS factor regression via numpy.linalg.lstsq (REGIME-6).

Fits a linear model::

    R_strategy = alpha + sum_i beta_i * factor_i + eps

with an explicit intercept column. No statsmodels dependency —
keeps the requirements light. For Indian Fama-French inputs the
project doesn't yet have a clean source; ``backend/algo/
attribution/job.py`` uses mock factor returns for v3 and flags
the persisted row with ``betas["__mock_data__"] = 1.0`` so the
UI can render an "experimental" chip. Real factor data wiring
is v3.1.

Refuses to fit on fewer than ``MIN_OBSERVATIONS`` clean rows —
returns NaN alpha + the row count, which the orchestrator
inspects.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MIN_OBSERVATIONS = 30


@dataclass
class FactorRegressionResult:
    """OLS output bundle."""

    alpha: float
    betas: dict[str, float] = field(default_factory=dict)
    r_squared: float = 0.0
    n_observations: int = 0


def _empty_result(n: int = 0) -> FactorRegressionResult:
    return FactorRegressionResult(
        alpha=float("nan"), n_observations=int(n),
    )


def fit_ols_regression(
    strategy_returns: np.ndarray,
    factor_returns: dict[str, np.ndarray],
) -> FactorRegressionResult:
    """Fit ``y = alpha + sum_i beta_i * x_i`` via OLS.

    All inputs must have equal length. Rows with NaN in any
    column are dropped before the fit. Refuses to fit when fewer
    than ``MIN_OBSERVATIONS`` clean rows survive — the caller
    inspects ``alpha is NaN`` to detect this.
    """
    factor_keys = list(factor_returns.keys())
    if not factor_keys:
        return _empty_result(0)

    y = np.asarray(strategy_returns, dtype=float)
    cols = [
        np.asarray(factor_returns[k], dtype=float)
        for k in factor_keys
    ]
    if any(len(c) != len(y) for c in cols):
        return _empty_result(0)

    stack = np.column_stack([y] + cols)
    mask = ~np.isnan(stack).any(axis=1)
    stack = stack[mask]
    if stack.shape[0] < MIN_OBSERVATIONS:
        return _empty_result(stack.shape[0])

    y_clean = stack[:, 0]
    x_cols = [stack[:, i + 1] for i in range(len(cols))]
    X = np.column_stack(
        [np.ones(stack.shape[0])] + x_cols,
    )
    coefs, _residuals, _rank, _sv = np.linalg.lstsq(
        X, y_clean, rcond=None,
    )
    alpha = float(coefs[0])
    betas = {
        k: float(coefs[i + 1])
        for i, k in enumerate(factor_keys)
    }

    y_mean = y_clean.mean()
    ss_tot = float(((y_clean - y_mean) ** 2).sum())
    y_pred = X @ coefs
    ss_res = float(((y_clean - y_pred) ** 2).sum())
    r_squared = (
        1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    )
    return FactorRegressionResult(
        alpha=alpha,
        betas=betas,
        r_squared=r_squared,
        n_observations=int(stack.shape[0]),
    )
