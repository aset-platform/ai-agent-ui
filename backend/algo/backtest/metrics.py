"""DSR / PBO / per-regime metric helpers for walk-forward CV.

DSR closed-form per Bailey & Lopez de Prado (2014):
"The Deflated Sharpe Ratio: Correcting for Selection Bias,
Backtest Overfitting and Non-Normality."

PBO via CSCV per Bailey, Borwein, Lopez de Prado, Zhu (2014):
"The Probability of Backtest Overfitting."

All functions are pure - no I/O. Anchored on REGIME-5 (slice 5
of the regime-aware multi-factor system) but reusable by any
walk-forward harness that needs DSR/PBO + per-regime breakdown.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable

import numpy as np
from scipy.stats import norm

EULER_MASCHERONI = 0.5772156649015329

# ---------------------------------------------------------------
# DSR
# ---------------------------------------------------------------


def _expected_max_sharpe(n_trials: int) -> float:
    """E[max SR] under the null hypothesis (Bailey 2014, eq. 5).

    For ``n_trials = 1`` returns 0.0 (no multiple-comparison
    deflation needed).
    """
    if n_trials <= 1:
        return 0.0
    g = EULER_MASCHERONI
    a = float(norm.ppf(1.0 - 1.0 / n_trials))
    b = float(norm.ppf(1.0 - 1.0 / (n_trials * math.e)))
    return (1.0 - g) * a + g * b


def deflated_sharpe_ratio(
    obs_sharpe: float,
    n_trials: int,
    sample_length: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """DSR in [0, 1] adjusted for multiple-trial bias and
    non-normality (skew + kurt).

    DSR >= 0.95 = "real, deflated alpha".
    DSR <= 0.5  = noise.

    Returns 0.0 when ``sample_length <= 1`` or ``n_trials <= 0``
    (pre-conditions for the closed form fail).
    """
    if sample_length <= 1 or n_trials <= 0:
        return 0.0
    sr0 = _expected_max_sharpe(n_trials)
    excess_kurt = kurt - 3.0
    denom_sq = (
        1.0
        - skew * obs_sharpe
        + (excess_kurt / 4.0) * obs_sharpe * obs_sharpe
    )
    if denom_sq <= 0:
        return 0.0
    z = (
        (obs_sharpe - sr0)
        * math.sqrt(sample_length - 1)
        / math.sqrt(denom_sq)
    )
    return float(norm.cdf(z))


# ---------------------------------------------------------------
# PBO via CSCV
# ---------------------------------------------------------------


def _block_sharpe(returns: np.ndarray) -> np.ndarray:
    """Sharpe per column (no annualisation - relative ranking
    only). Columns with ~zero variance produce NaN."""
    mu = returns.mean(axis=0)
    sigma = returns.std(axis=0, ddof=0)
    sigma = np.where(sigma > 1e-12, sigma, np.nan)
    return mu / sigma


def probability_of_backtest_overfitting(
    R: np.ndarray, n_blocks: int = 16,
) -> float:
    """PBO via CSCV. ``R`` is a (T, N) returns matrix.

    Splits T rows into ``n_blocks`` equal blocks. For every
    n_blocks/2 IS / n_blocks/2 OOS combination:
      1. Pick variant with best IS Sharpe.
      2. Compute its OOS Sharpe rank in [1, N] (1 = winner).
      3. logit lam = log(rank / (N - rank + 1)).
    PBO = fraction of combinations with **lam > 0**, i.e. the
    IS winner's OOS rank lies in the BOTTOM half (overfit).

    Returns NaN when preconditions fail:
      - n_blocks < 4 or odd or > T
      - N < 2 (can't rank a single variant)
    """
    T, N = R.shape
    if n_blocks < 4 or n_blocks % 2 != 0 or T < n_blocks:
        return float("nan")
    if N < 2:
        return float("nan")
    block_size = T // n_blocks
    blocks = [
        R[i * block_size: (i + 1) * block_size]
        for i in range(n_blocks)
    ]
    half = n_blocks // 2
    overfit_count = 0
    total = 0
    for is_idx in combinations(range(n_blocks), half):
        oos_idx = tuple(
            i for i in range(n_blocks) if i not in is_idx
        )
        is_R = np.vstack([blocks[i] for i in is_idx])
        oos_R = np.vstack([blocks[i] for i in oos_idx])
        is_sr = _block_sharpe(is_R)
        if np.all(np.isnan(is_sr)):
            continue
        winner = int(np.nanargmax(is_sr))
        oos_sr = _block_sharpe(oos_R)
        # Replace NaN with -inf so they sort to the bottom.
        oos_sr_safe = np.where(
            np.isnan(oos_sr), -np.inf, oos_sr,
        )
        order = np.argsort(-oos_sr_safe)  # descending
        rank = int(np.where(order == winner)[0][0]) + 1
        # rank > N/2 => lam > 0 => winner sits in bottom half.
        lam = math.log(rank / (N - rank + 1))
        if lam > 0:
            overfit_count += 1
        total += 1
    if total == 0:
        return float("nan")
    return overfit_count / total


# ---------------------------------------------------------------
# Per-regime breakdown + recovery time (placeholders for Task 3)
# ---------------------------------------------------------------


@dataclass
class PerRegimeMetrics:
    """Per-regime aggregate slice of a walk-forward equity curve."""

    regime: str        # BULL / SIDEWAYS / BEAR
    n_days: int
    cum_return_pct: float
    sharpe: float
    sortino: float
    max_dd_pct: float
    hit_rate: float    # fraction of days with positive return
