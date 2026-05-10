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
# Per-regime breakdown + recovery time
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


def _max_drawdown_pct(equity: np.ndarray) -> float:
    """Max drawdown of an equity series, in percent of HWM."""
    if equity.size == 0:
        return 0.0
    hwm = np.maximum.accumulate(equity)
    safe_hwm = np.where(hwm > 1e-12, hwm, np.nan)
    dd = (equity - hwm) / safe_hwm
    worst = float(np.nanmin(dd)) if dd.size else 0.0
    if math.isnan(worst):
        return 0.0
    return abs(worst) * 100.0


def _annualised_sharpe(returns: np.ndarray) -> float:
    """Sharpe = mean / std * sqrt(252). Returns 0 when std ~ 0."""
    if returns.size == 0:
        return 0.0
    mu = float(returns.mean())
    sigma = float(returns.std(ddof=0))
    if sigma <= 1e-12:
        return 0.0
    return mu / sigma * math.sqrt(252)


def _annualised_sortino(returns: np.ndarray) -> float:
    """Sortino = mean / downside_std * sqrt(252)."""
    if returns.size == 0:
        return 0.0
    mu = float(returns.mean())
    downside = returns[returns < 0]
    if downside.size == 0:
        # No losses - return Sharpe-equivalent upper bound (large
        # but finite) so the metric stays JSON-serialisable.
        return 0.0 if mu <= 0 else float("inf")
    sigma_d = float(downside.std(ddof=0))
    if sigma_d <= 1e-12:
        return 0.0
    return mu / sigma_d * math.sqrt(252)


def per_regime_breakdown(
    equity_curve: list[dict[str, Any]],
    regime_labels: dict[Any, str],
) -> list[PerRegimeMetrics]:
    """Group an equity curve by regime label and emit one
    PerRegimeMetrics row per regime present.

    Args:
      equity_curve: list of dicts with keys ``bar_date`` (str
          ISO date or date) and ``equity_inr`` (str/Decimal/float).
      regime_labels: mapping of bar_date (ISO str) → regime label
          (BULL / SIDEWAYS / BEAR). Days missing from the map
          are dropped from the breakdown silently.

    Returns:
      Sorted list of PerRegimeMetrics, one per regime that has
      at least one day. Empty list when the input is empty.
    """
    if not equity_curve:
        return []

    # Normalise to (date_key: str, equity: float) pairs.
    pts: list[tuple[str, float]] = []
    for ep in equity_curve:
        bd = ep.get("bar_date")
        if bd is None:
            continue
        key = bd.isoformat() if hasattr(bd, "isoformat") else str(bd)
        eq_raw = ep.get("equity_inr")
        if eq_raw is None:
            continue
        try:
            eq = float(eq_raw)
        except (TypeError, ValueError):
            continue
        pts.append((key, eq))

    if not pts:
        return []

    # Bucket by regime, preserving order so daily-return diffs
    # respect calendar sequence within each regime.
    buckets: dict[str, list[tuple[str, float]]] = {}
    for key, eq in pts:
        label = regime_labels.get(key)
        if label is None:
            continue
        buckets.setdefault(label, []).append((key, eq))

    out: list[PerRegimeMetrics] = []
    for regime, rows in sorted(buckets.items()):
        if len(rows) < 2:
            # Need at least 2 days to compute returns.
            equity_arr = np.array([r[1] for r in rows])
            cum = 0.0
            if len(rows) >= 1 and rows[0][1] > 0:
                cum = (rows[-1][1] / rows[0][1] - 1.0) * 100.0
            out.append(PerRegimeMetrics(
                regime=regime,
                n_days=len(rows),
                cum_return_pct=cum,
                sharpe=0.0,
                sortino=0.0,
                max_dd_pct=_max_drawdown_pct(equity_arr),
                hit_rate=0.0,
            ))
            continue

        equity_arr = np.array([r[1] for r in rows])
        # Daily returns within this regime (sequential days only).
        rets = np.diff(equity_arr) / equity_arr[:-1]
        rets = rets[np.isfinite(rets)]

        cum = 0.0
        if equity_arr[0] > 0:
            cum = (equity_arr[-1] / equity_arr[0] - 1.0) * 100.0

        hit = 0.0
        if rets.size > 0:
            hit = float((rets > 0).sum()) / float(rets.size)

        out.append(PerRegimeMetrics(
            regime=regime,
            n_days=len(rows),
            cum_return_pct=cum,
            sharpe=_annualised_sharpe(rets),
            sortino=_annualised_sortino(rets),
            max_dd_pct=_max_drawdown_pct(equity_arr),
            hit_rate=hit,
        ))
    return out


def recovery_months_from_dd(
    equity_curve: list[dict[str, Any]],
) -> int:
    """Months from the global max-DD trough until equity recovers
    to or exceeds the pre-DD HWM.

    Returns 0 when there is no drawdown. Returns the total window
    length in months (rounded up) when the equity never recovers
    within the curve.
    """
    if not equity_curve:
        return 0

    pts: list[tuple[str, float]] = []
    for ep in equity_curve:
        bd = ep.get("bar_date")
        if bd is None:
            continue
        key = bd.isoformat() if hasattr(bd, "isoformat") else str(bd)
        eq_raw = ep.get("equity_inr")
        if eq_raw is None:
            continue
        try:
            eq = float(eq_raw)
        except (TypeError, ValueError):
            continue
        pts.append((key, eq))

    if len(pts) < 2:
        return 0

    equity = np.array([p[1] for p in pts])
    dates = [p[0] for p in pts]

    hwm = np.maximum.accumulate(equity)
    safe_hwm = np.where(hwm > 1e-12, hwm, np.nan)
    dd = (equity - hwm) / safe_hwm
    if not np.any(dd < 0):
        return 0

    trough_idx = int(np.nanargmin(dd))
    pre_dd_hwm = float(hwm[trough_idx])

    recovered_idx: int | None = None
    for j in range(trough_idx + 1, len(equity)):
        if equity[j] >= pre_dd_hwm:
            recovered_idx = j
            break

    from datetime import date as _date

    def _parse(s: str) -> _date:
        try:
            return _date.fromisoformat(s)
        except ValueError:
            # Fallback to first 10 chars (YYYY-MM-DD).
            return _date.fromisoformat(s[:10])

    if recovered_idx is None:
        # Never recovered within window → return total window
        # months (ceil).
        d0 = _parse(dates[trough_idx])
        d1 = _parse(dates[-1])
    else:
        d0 = _parse(dates[trough_idx])
        d1 = _parse(dates[recovered_idx])

    days = max((d1 - d0).days, 0)
    # Convert to whole months, rounding up.
    months = (days + 29) // 30
    return int(months)
