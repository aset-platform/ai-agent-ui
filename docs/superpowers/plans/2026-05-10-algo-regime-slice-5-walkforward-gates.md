# Regime-Aware Multi-Factor System — Slice REGIME-5: Walk-Forward + DSR/PBO + 5 Gates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Extend the V2-2 walk-forward harness with regime-stratified splits, per-regime metric breakdown, DSR (Deflated Sharpe Ratio) + PBO (Probability of Backtest Overfitting), and 5 acceptance gates. Live-mode toggle now requires walkforward report passing all 5 gates.

**Architecture:** `walkforward.py` extends `WalkForwardConfig` with gate thresholds + `regime_stratified` flag, extends `WalkForwardAggregate` with `per_regime` + `dsr` + `pbo` + `gates_passed`. New `metrics.py` implements DSR closed-form (Bailey/López de Prado), PBO via CSCV (combinatorially symmetric cross-validation), per-regime breakdown, recovery-time helper. Live-toggle gate change in `routes/live.py::_check_gates()` (additive; v2 grandfathered). Frontend `WalkForwardSubTab.tsx` gains 5-traffic-light gate strip + per-regime metric grid.

**Tech Stack:** Python 3.12, scipy.stats (norm CDF), numpy, pandas, Pydantic v2. Frontend: React 19, ECharts, Tailwind.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` §3.5 + §5.1 REGIME-5 + §6.1 + §7.1.
**Research:** §4 (Walk-Forward CV), §6 (Performance Metric Hierarchy).

**Branch:** `feature/regime-slice-5-walkforward-gates` (already created, tracking `origin`).

**Estimated SP:** 13

---

## Pre-flight (MUST DO before code)

Verify BEFORE each task:

- **Walkforward harness:** `backend/algo/backtest/walkforward.py`. `WalkForwardConfig` at line ~70 (Pydantic v2 with `extra="forbid"`). `WalkForwardAggregate` at line ~118. `walk_windows(...)` generator at line ~150+. `BacktestRunsRepo` import at top.
- **Walkforward routes:** `backend/algo/routes/walkforward.py::create_walkforward_router()`. `POST /run` at line ~53, `GET /runs/{id}` at line ~108, `GET /runs` at line ~159.
- **Live gate logic:** `backend/algo/routes/live.py::_check_gates()`. Existing tests in `backend/algo/tests/test_live_walkforward_gate.py` exercise the 30-day + positive-win-rate gates — DO NOT REGRESS THEM. The 5-gate check is ADDITIVE.
- **Regime history reader:** `backend.algo.regime.repo.get_regime_history(start, end) -> list[RegimeRow]` (REGIME-1).
- **Equity curve in `WindowSummary`:** `equity_curve: list[dict[str, Any]]` per `walkforward.py:113`. Each dict has `bar_date` + `equity_inr` (verify via `grep -n "equity_inr\|equity_curve" backend/algo/backtest/runs_repo.py`).
- **Iceberg `algo.runs`:** schema check via `grep -n "_runs_schema\|gates_passed" backend/algo/iceberg_init.py backend/algo/runs_repo.py`. May need to add JSONB-equivalent (StringType) columns: `gates_passed_json`, `per_regime_metrics_json`. **CRITICAL:** Iceberg add_column requires backend restart per CLAUDE.md §6.2. If `algo.runs` is the V2 table, additive columns work; if it's PG, run an Alembic migration.
- **Frontend WalkForwardSubTab:** `frontend/components/algo-trading/WalkForwardSubTab.tsx` (existing). The new gate strip + per-regime grid mount inline.
- **scipy:** `import scipy.stats as stats; stats.norm.cdf(...)` — confirm scipy is in requirements: `grep "scipy" backend/requirements.txt`.

If any name doesn't resolve, STOP.

---

## File Structure

**Backend — new:**
- `backend/algo/backtest/metrics.py` — DSR + PBO + per-regime breakdown + recovery-time helpers. Pure functions.
- `backend/algo/backtest/gates.py` — `evaluate_5_gates(aggregate, per_regime, dsr, pbo) -> dict[str, bool]`. Pure.
- `backend/algo/tests/test_metrics_dsr.py`
- `backend/algo/tests/test_metrics_pbo.py`
- `backend/algo/tests/test_metrics_per_regime.py`
- `backend/algo/tests/test_metrics_recovery.py`
- `backend/algo/tests/test_gates_5.py`
- `backend/algo/tests/test_walkforward_regime_stratified.py`

**Backend — modified:**
- `backend/algo/backtest/walkforward.py` — extend `WalkForwardConfig` with gate-threshold fields + `regime_stratified: bool = True`; extend `WalkForwardAggregate` with `per_regime`, `dsr`, `pbo`, `gates_passed`; modify `_aggregate_windows` to call new helpers; modify `walk_windows` to enforce regime stratification when flag set.
- `backend/algo/backtest/runs_repo.py` — persist `gates_passed_json`, `per_regime_metrics_json` (and Iceberg schema additions if needed).
- `backend/algo/routes/walkforward.py` — extend GET response; add `GET /runs/{id}/gates`.
- `backend/algo/routes/live.py` — extend `_check_gates()` with optional 5-gate check (controlled by feature flag `ALGO_REGIME_5_GATES_REQUIRED` env, default False — turn on after rollout day 21 per spec §7.1).

**Frontend — modified:**
- `frontend/components/algo-trading/WalkForwardSubTab.tsx` — add 5-light gate strip + per-regime metric grid + per-regime equity-curve coloring.
- `frontend/components/algo-trading/LiveModeToggle.tsx` — fetch `/v1/algo/walkforward/runs/{id}/gates` before enable; show per-failed-gate tooltip.

**E2E:**
- `e2e/tests/frontend/algo-walkforward-gates.spec.ts` — at least: 5-light strip renders after walkforward submission.

---

## Task 1 — DSR formula + tests

**Files:**
- Create: `backend/algo/backtest/metrics.py`, `backend/algo/tests/test_metrics_dsr.py`.

DSR closed-form per Bailey & López de Prado (2014). Formula:

```
SR_0(N) = sqrt(Var(SR_estimate)) * ((1 - γ) * Φ⁻¹(1 - 1/N) +
          γ * Φ⁻¹(1 - 1/(N*e)))    # γ = Euler-Mascheroni ≈ 0.5772

DSR = Φ(  (SR_obs - SR_0) * sqrt(T - 1) /
          sqrt(1 - skew*SR_obs + (kurt-1)/4 * SR_obs²)
       )
```

Where:
- `SR_obs` = observed Sharpe ratio (annualised, sample)
- `SR_0` = expected max-of-N Sharpe under null hypothesis
- `N` = n_trials (number of strategy variants tried)
- `T` = sample length (number of return observations)
- `skew, kurt` = sample skewness + excess kurtosis of returns
- `Φ` = standard normal CDF (`scipy.stats.norm.cdf`)
- `Φ⁻¹` = standard normal inverse CDF (`scipy.stats.norm.ppf`)
- `e` = Euler's number ≈ 2.7183

Output ∈ [0, 1] — DSR ≥ 0.95 = "real, deflated alpha". DSR ≤ 0.5 = noise.

- [ ] **Step 1.1: Failing test**

```python
"""Deflated Sharpe Ratio tests — anchored on Bailey paper sample."""
from __future__ import annotations

import pytest

from backend.algo.backtest.metrics import deflated_sharpe_ratio


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
    for same Sharpe (left-tail risk penalty)."""
    common = dict(
        obs_sharpe=1.5, n_trials=10, sample_length=252, kurt=3.0,
    )
    flat = deflated_sharpe_ratio(skew=0.0, **common)
    left = deflated_sharpe_ratio(skew=-0.5, **common)
    assert left < flat


def test_dsr_short_sample_returns_zero() -> None:
    """T ≤ 1 cannot produce a meaningful DSR."""
    out = deflated_sharpe_ratio(
        obs_sharpe=1.5, n_trials=10, sample_length=1,
        skew=0.0, kurt=3.0,
    )
    assert out == 0.0
```

- [ ] **Step 1.2: Run to verify fail**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_metrics_dsr.py -v
```

- [ ] **Step 1.3: Implement DSR**

Create `backend/algo/backtest/metrics.py`:

```python
"""DSR / PBO / per-regime metric helpers for walk-forward CV.

DSR closed-form per Bailey & López de Prado (2014):
"The Deflated Sharpe Ratio: Correcting for Selection Bias,
Backtest Overfitting and Non-Normality."

PBO via CSCV per the same paper:
"The Probability of Backtest Overfitting" (Bailey, Borwein,
Lopez de Prado, Zhu, 2014).

All functions are pure — no I/O.
"""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import numpy as np
from scipy.stats import norm

EULER_MASCHERONI = 0.5772156649015329


def _expected_max_sharpe(n_trials: int) -> float:
    """E[max SR] under the null hypothesis (Bailey 2014, eq. 5)."""
    if n_trials <= 1:
        return 0.0
    g = EULER_MASCHERONI
    a = norm.ppf(1.0 - 1.0 / n_trials)
    b = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return (1.0 - g) * a + g * b


def deflated_sharpe_ratio(
    obs_sharpe: float,
    n_trials: int,
    sample_length: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """DSR ∈ [0, 1] adjusted for multiple-trial bias and
    non-normality (skew + kurt). DSR ≥ 0.95 = robust alpha."""
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
```

- [ ] **Step 1.4: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_metrics_dsr.py -v
git add backend/algo/backtest/metrics.py backend/algo/tests/test_metrics_dsr.py
git commit -m "feat(algo): deflated_sharpe_ratio closed-form (REGIME-5)"
```

---

## Task 2 — PBO via CSCV

PBO = Probability of Backtest Overfitting via Combinatorially Symmetric Cross-Validation (Bailey, Borwein, Lopez de Prado, Zhu 2014).

Pragmatic implementation:
- Input: matrix of returns `R[T x N]` (T observations, N strategy variants).
- Split T into S=16 equal sub-blocks (paper recommends S ≥ 14).
- For each combination of S/2 IS blocks (training set) vs S/2 OOS blocks (test set):
  - Pick best variant by IS Sharpe.
  - Compute its OOS Sharpe rank (out of N variants).
  - Logit-transform: `λ = log(rank / (N - rank + 1))`.
  - Negative λ = winner overfit (worse than median OOS).
- PBO = fraction of combinations where λ < 0.

For unit testing, use a small synthetic example: N=4 variants × T=8 observations, split S=4. Expected PBO is close to 0.5 for random returns and close to 0 for one strategy that's consistently best.

- [ ] **Step 2.1: Test**

```python
"""PBO via CSCV — synthetic + paper sample."""
from __future__ import annotations

import numpy as np

from backend.algo.backtest.metrics import (
    probability_of_backtest_overfitting,
)


def test_pbo_random_returns_around_half() -> None:
    """Random IID returns → PBO should be roughly 0.5 (no edge,
    no overfit signal either way)."""
    rng = np.random.default_rng(42)
    R = rng.normal(0, 0.01, size=(64, 8))  # 64 obs × 8 variants
    pbo = probability_of_backtest_overfitting(R, n_blocks=8)
    assert 0.2 <= pbo <= 0.8


def test_pbo_dominant_strategy_low() -> None:
    """One variant consistently outperforms — PBO should be low
    (no overfitting, just real edge)."""
    rng = np.random.default_rng(7)
    R = rng.normal(0, 0.01, size=(64, 8))
    R[:, 0] += 0.005  # variant 0 has +50bps/period real edge
    pbo = probability_of_backtest_overfitting(R, n_blocks=8)
    assert pbo < 0.4


def test_pbo_in_unit_interval() -> None:
    rng = np.random.default_rng(0)
    R = rng.normal(0, 0.01, size=(32, 4))
    pbo = probability_of_backtest_overfitting(R, n_blocks=4)
    assert 0.0 <= pbo <= 1.0


def test_pbo_too_few_blocks_returns_nan() -> None:
    R = np.zeros((10, 3))
    pbo = probability_of_backtest_overfitting(R, n_blocks=2)
    assert np.isnan(pbo)
```

- [ ] **Step 2.2: Implement (append to `metrics.py`)**

```python
from itertools import combinations


def _block_sharpe(returns: np.ndarray) -> np.ndarray:
    """Sharpe per column (no annualisation — relative ranking only)."""
    mu = returns.mean(axis=0)
    sigma = returns.std(axis=0, ddof=0)
    sigma = np.where(sigma > 1e-12, sigma, np.nan)
    return mu / sigma


def probability_of_backtest_overfitting(
    R: np.ndarray, n_blocks: int = 16,
) -> float:
    """PBO via CSCV. ``R`` is (T, N) returns matrix.

    Splits T rows into ``n_blocks`` equal blocks. For each
    n_blocks/2 IS / n_blocks/2 OOS combination:
      1. Pick variant with best IS Sharpe.
      2. Compute its OOS Sharpe rank ∈ [1, N].
      3. ``λ = log(rank / (N - rank + 1))``.
    PBO = fraction of combinations with λ < 0.
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
        # Rank of winner in OOS (1 = best, N = worst)
        order = np.argsort(-oos_sr)  # descending
        rank = int(np.where(order == winner)[0][0]) + 1
        # logit
        lam = math.log(rank / (N - rank + 1))
        if lam < 0:
            overfit_count += 1
        total += 1
    if total == 0:
        return float("nan")
    return overfit_count / total
```

- [ ] **Step 2.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_metrics_pbo.py -v
git add backend/algo/backtest/metrics.py backend/algo/tests/test_metrics_pbo.py
git commit -m "feat(algo): probability_of_backtest_overfitting via CSCV (REGIME-5)"
```

---

## Task 3 — Per-regime breakdown + recovery time

**Files:**
- Append to `backend/algo/backtest/metrics.py`: `per_regime_breakdown(equity_curve, regime_labels)` + `recovery_months_from_dd(equity_curve)`.
- Test: `backend/algo/tests/test_metrics_per_regime.py`, `backend/algo/tests/test_metrics_recovery.py`.

```python
@dataclass
class PerRegimeMetrics:
    regime: str        # BULL / SIDEWAYS / BEAR
    n_days: int
    cum_return_pct: float
    sharpe: float
    sortino: float
    max_dd_pct: float
    hit_rate: float    # fraction of days with positive return
```

`per_regime_breakdown(equity_curve, regime_labels)`:
- Group equity-curve days by regime label.
- Compute per-regime daily returns from equity diffs.
- Sharpe = mean / std × sqrt(252). Sortino = mean / downside_std × sqrt(252).
- Max DD per regime = max drawdown within that regime's contiguous days.

`recovery_months_from_dd(equity_curve)`:
- Find global max DD.
- Months from DD trough until equity returns to or exceeds the pre-DD HWM.
- If never recovers within window, return total window months.

- [ ] **Step 3.1: Tests** — straightforward; mirror the patterns above. Cover BULL+SIDEWAYS+BEAR present + missing-regime case + recovery happens / never-happens.

- [ ] **Step 3.2: Implementation** — straightforward pandas/numpy. Use `pd.DataFrame(equity_curve).groupby(regime_labels)` for per-regime aggregation.

- [ ] **Step 3.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_metrics_per_regime.py backend/algo/tests/test_metrics_recovery.py -v
git add backend/algo/backtest/metrics.py backend/algo/tests/test_metrics_per_regime.py backend/algo/tests/test_metrics_recovery.py
git commit -m "feat(algo): per_regime_breakdown + recovery_months_from_dd (REGIME-5)"
```

---

## Task 4 — `evaluate_5_gates` + tests

**Files:**
- Create: `backend/algo/backtest/gates.py`, `backend/algo/tests/test_gates_5.py`.

```python
@dataclass
class GateThresholds:
    max_dd_pct: float = 25.0
    recovery_months_max: int = 18
    require_per_regime_non_negative: bool = True
    dsr_min: float = 0.95
    pbo_max: float = 0.30


def evaluate_5_gates(
    aggregate_max_dd_pct: float,
    recovery_months: int,
    per_regime: list[PerRegimeMetrics],
    dsr: float,
    pbo: float,
    thresholds: GateThresholds = GateThresholds(),
) -> dict[str, bool]:
    return {
        "max_dd_ok": aggregate_max_dd_pct <= thresholds.max_dd_pct,
        "recovery_ok": recovery_months <= thresholds.recovery_months_max,
        "per_regime_non_neg": all(
            r.cum_return_pct >= 0 for r in per_regime
        ) if thresholds.require_per_regime_non_negative else True,
        "dsr_ok": dsr >= thresholds.dsr_min,
        "pbo_ok": pbo <= thresholds.pbo_max,
    }


def all_gates_pass(gates: dict[str, bool]) -> bool:
    return all(gates.values())
```

Test: 6 cases (all pass; each gate fail individually = overall fail).

- [ ] **Step 4.1-4.3** — write test, implement, commit.

---

## Task 5 — Walk-forward Pydantic extension + regime-stratified windows

**Files:**
- Modify: `backend/algo/backtest/walkforward.py`.
- Test: `backend/algo/tests/test_walkforward_regime_stratified.py`.

Extend `WalkForwardConfig`:
```python
class WalkForwardConfig(BaseModel):
    # existing fields ...
    regime_stratified: bool = True
    require_per_regime_non_negative: bool = True
    require_dsr_min: Decimal = Decimal("0.95")
    require_pbo_max: Decimal = Decimal("0.30")
    require_max_dd_pct: Decimal = Decimal("25")
    require_recovery_months_max: int = 18
```

Extend `WalkForwardAggregate`:
```python
class WalkForwardAggregate(BaseModel):
    # existing ...
    per_regime: list[PerRegimeMetrics] = Field(default_factory=list)
    dsr: Decimal = Decimal("0")
    pbo: Decimal | None = None     # None when CSCV preconditions fail
    recovery_months: int = 0
    gates_passed: dict[str, bool] = Field(default_factory=dict)
```

`walk_windows()` extension: when `regime_stratified=True`, fetch regime labels from `stocks.regime_history` for the period; ensure each window's TRAIN slice contains ≥1 sample of each regime present in the FULL period (don't require all 3 if the period genuinely lacks one — e.g. backtest start 2020 has no BEAR yet). If the constraint can't be satisfied for any window, raise `ValueError` with guidance to lengthen the period.

Test: synthesize a regime_history with BULL+SIDEWAYS+BEAR distributed; verify every yielded window has all 3 regimes in train.

- [ ] **Step 5.1-5.3** — test, implement, commit.

---

## Task 6 — Wire into walkforward orchestrator + persist gates

**Files:**
- Modify: `backend/algo/backtest/walkforward.py` (`_aggregate_windows` extension).
- Modify: `backend/algo/backtest/runs_repo.py` (persist new fields).
- Test: integration test on a small synthetic dataset.

After all per-window backtests complete, the aggregator now:
1. Builds the per-window equity curve concat.
2. Loads regime labels for the full period.
3. Calls `per_regime_breakdown` → `PerRegimeMetrics` list.
4. Computes `recovery_months_from_dd` on aggregated curve.
5. Builds R matrix (T obs × N variants if applicable; for V2-2 single-strategy, N=1 → DSR with `n_trials=1`, PBO returns NaN).
6. Calls `deflated_sharpe_ratio(obs_sharpe, n_trials, T, skew, kurt)`.
7. Calls `probability_of_backtest_overfitting` if N ≥ 2.
8. Calls `evaluate_5_gates(...)` → `gates_passed`.
9. Persists `per_regime_metrics_json` + `gates_passed_json` on the parent walkforward row.

For V2-2 single-strategy walkforward, `n_trials=1` → DSR ≈ Φ(SR × √(T-1)) (no deflation). PBO is NaN — gate "pbo_ok" defaults True when None.

- [ ] **Step 6.1: Persistence schema check**

If `algo.runs` is Iceberg, run an `update_schema().add_column()` migration via a one-shot script. If PG, write an Alembic migration. Grep first:
```bash
grep -n "algo.runs\|_runs_table\|_runs_schema" backend/algo/iceberg_init.py backend/algo/runs_repo.py backend/algo/backtest/runs_repo.py 2>/dev/null
```

After Iceberg add_column → backend restart per CLAUDE.md §6.2.

- [ ] **Step 6.2: Wire `_aggregate_windows`** with the 9-step flow above.

- [ ] **Step 6.3: Run + commit**

```bash
docker compose exec -T -e PYTHONPATH=/app:/app/backend backend pytest backend/algo/tests/test_walkforward*.py -v
git add backend/algo/backtest/walkforward.py backend/algo/backtest/runs_repo.py backend/algo/tests/test_walkforward_aggregate_with_gates.py
git commit -m "feat(algo): walkforward aggregator computes DSR + PBO + per-regime + gates (REGIME-5)"
```

---

## Task 7 — Routes: extended GET + new `/gates` endpoint

**Files:**
- Modify: `backend/algo/routes/walkforward.py`.

Existing GET `/runs/{id}` response includes new fields automatically once aggregator persists them. Add:

```python
@router.get("/runs/{walkforward_run_id}/gates")
async def get_gates(walkforward_run_id: UUID, ...) -> dict:
    """Return {gates_passed: dict, overall_pass: bool, recommendations: list[str]}."""
```

Recommendations are simple lookups:
```python
RECS = {
  "max_dd_ok": "Max DD exceeded 25%. Tighten stop loss or reduce position sizing.",
  "recovery_ok": "Recovery > 18 months. Strategy may not survive prolonged drawdowns.",
  "per_regime_non_neg": "Negative return in at least one regime. Add applicable_regimes filter or regime-specific entry conditions.",
  "dsr_ok": "DSR < 0.95 — observed Sharpe likely inflated by multiple-comparison bias. Reduce hyperparameter search.",
  "pbo_ok": "PBO > 0.30 — high overfit probability. Lengthen test window or use simpler model.",
}
```

- [ ] **Step 7.1-7.3** — test (route returns 200 + correct shape), implement, commit.

---

## Task 8 — Live-toggle gate integration (feature-flagged)

**Files:**
- Modify: `backend/algo/routes/live.py::_check_gates()`.

Add 5-gate check ONLY when env `ALGO_REGIME_5_GATES_REQUIRED=1`. Default OFF — existing v2 toggle gate (30-day + positive win-rate) stays the only requirement until the operator flips the flag (per spec §7.1 day-21 cutover).

```python
if os.environ.get("ALGO_REGIME_5_GATES_REQUIRED") == "1":
    gates = await _fetch_walkforward_gates(strategy_id)
    if not all(gates.values()):
        return False, f"Walk-forward gates: {sum(gates.values())}/5 passed"
```

Existing `test_live_walkforward_gate.py` tests must still pass (the env flag defaults OFF → no behavior change).

- [ ] **Step 8.1-8.3** — test (env on / env off), implement, commit.

---

## Task 9 — Frontend gate-strip + per-regime grid

**Files:**
- Modify: `frontend/components/algo-trading/WalkForwardSubTab.tsx`.
- Modify: `e2e/utils/selectors.ts` for testids.

Render below the existing aggregate summary cards:

```tsx
<div className="flex items-center gap-2" data-testid="walkforward-gates-strip">
  <GateLight name="max_dd" passed={gates.max_dd_ok} title={`Max DD: ${aggregate.avg_max_drawdown_pct}%`} />
  <GateLight name="recovery" passed={gates.recovery_ok} title={`Recovery: ${aggregate.recovery_months}mo`} />
  <GateLight name="per_regime" passed={gates.per_regime_non_neg} title="All regimes non-negative" />
  <GateLight name="dsr" passed={gates.dsr_ok} title={`DSR: ${aggregate.dsr.toFixed(2)}`} />
  <GateLight name="pbo" passed={gates.pbo_ok ?? true} title={`PBO: ${aggregate.pbo?.toFixed(2) ?? "n/a"}`} />
</div>
```

Per-regime grid: 3-row table (BULL/SIDEWAYS/BEAR) × (n_days, return %, Sharpe, Sortino, max DD %).

- [ ] **Step 9.1-9.3** — testids, component edit, lint + smoke + commit.

---

## Task 10 — LiveModeToggle gate check

**Files:**
- Modify: `frontend/components/algo-trading/LiveModeToggle.tsx`.

Before submitting toggle ON, fetch `/v1/algo/walkforward/runs/{id}/gates`; if `overall_pass === false`, render a tooltip with failed gate names. Don't actually block (backend gate is the source of truth) — just warn user pre-emptively.

- [ ] **Step 10.1-10.3** — hook + tooltip + commit.

---

## Task 11 — E2E + ship

**Files:**
- Create: `e2e/tests/frontend/algo-walkforward-gates.spec.ts`.

Permissive: navigate Walk-forward tab; assert testid `walkforward-gates-strip` exists in the rendered page (whether populated or not). Mirror REGIME-1's lightweight pattern.

- [ ] **Step 11.1: E2E + final push**

```bash
cd e2e && npx playwright test --project=frontend-chromium tests/frontend/algo-walkforward-gates.spec.ts -j 1
cd /Users/abhay/Documents/projects/ai-agent-ui/.worktrees/regime-slice-5-walkforward-gates
git add e2e/tests/frontend/algo-walkforward-gates.spec.ts
git commit -m "test(e2e): walkforward 5-gate strip renders (REGIME-5)"
git push origin feature/regime-slice-5-walkforward-gates
```

---

## Acceptance Checklist

- [ ] DSR formula tests pass (5 cases including monotonicity + skew penalty).
- [ ] PBO via CSCV tests pass (random ≈ 0.5; dominant ≤ 0.4).
- [ ] Per-regime breakdown emits PerRegimeMetrics for each regime present.
- [ ] Recovery time computed correctly on synthetic DD curve.
- [ ] `evaluate_5_gates` returns dict with 5 boolean keys.
- [ ] `WalkForwardConfig` extended; `regime_stratified=True` by default.
- [ ] Walk-forward orchestrator persists `gates_passed_json` + `per_regime_metrics_json`.
- [ ] `GET /v1/algo/walkforward/runs/{id}/gates` returns proper shape.
- [ ] Live-toggle gate check feature-flagged by `ALGO_REGIME_5_GATES_REQUIRED`; existing test_live_walkforward_gate.py passes unchanged.
- [ ] Frontend renders 5-light strip + per-regime grid.
- [ ] LiveModeToggle warns when gates not passed.
- [ ] E2E smoke passes.
- [ ] Branch pushed.

---

## Out of Scope for REGIME-5

- CPCV (Combinatorial Purged CV) — deferred to v4.
- Auto-promotion of strategies based on gate passes.
- Per-sector regime overlay.
- ML regime classifier (rule-based + HMM only).
- DD throttle ↔ gate interaction (separate concerns).
- Per-window equity-curve recoloring by regime (nice-to-have, deferred).
- Detail modals for each gate (tooltip is enough).
