# Regime-Aware Multi-Factor System — Slice REGIME-5: Walk-Forward Extension + 5 Gates

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session is about to start.

> **For agentic workers:** RECOMMENDED SUB-SKILL: After expansion, use `superpowers:executing-plans` (sequential TDD); optionally `superpowers:subagent-driven-development` (parallel component work).

**Goal:** Extend the V2-2 walk-forward harness with regime-stratified train/test splits, per-regime metric breakdown, DSR (Deflated Sharpe Ratio) and PBO (Probability of Backtest Overfitting) computation, and five acceptance gates (max DD ≤25%, recovery ≤18mo, per-regime non-negative, DSR ≥0.95, PBO ≤0.3). Live-mode toggle now requires walkforward report passing all five gates instead of v2's single "exists and <30 days old" gate.

**Architecture:** Extends `backend/algo/backtest/walkforward.py` with regime-aware window stratification (ensuring BULL+SIDEWAYS+BEAR present in train+test of every window), new `backend/algo/backtest/metrics.py` implementing DSR + PBO closed-form formulas (Bailey/López de Prado), and per-regime metric breakdown (Sharpe, Sortino, max DD per regime). `WalkForwardSubTab.tsx` gains five traffic-light gate indicators and per-regime equity curves color-coded (green/gray/red). Live-mode toggle gate change wired in v2-5 module (backwards compatible: existing live runs grandfathered).

**Tech Stack:** Python 3.12 / scipy.stats for normal CDF / pandas / Pydantic v2 / SQLAlchemy 2.0 async. Frontend: React 19 / ECharts color-coded curves / Tailwind.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §3.5 (WalkForwardConfig + Pydantic extensions), §5.1 REGIME-5 row, §6.1 REGIME-5 test row, §7.1 (live-mode toggle migration policy).

**Research:** `docs/superpowers/research/2026-05-10-regime-aware-multifactor-research.md` — §4 (Walk-Forward CV best practices + CPCV deferral), §6 (Performance Metric Hierarchy).

**Branch:** `feature/algo-regime-slice-5-walkforward-gates` off `feature/algo-regime-multifactor-integration`.

**Depends on:** REGIME-2a (vol/breadth factors from cache for window labeling); regime history populated from REGIME-1.

**Estimated SP:** 13

---

## File Structure

**Backend (extend):**
- `backend/algo/backtest/walkforward.py` — extend `WalkForwardConfig` Pydantic model with `regime_stratified: bool = True`, `require_per_regime_non_negative: bool = True`, `require_dsr_min: Decimal = Decimal("0.95")`, `require_pbo_max: Decimal = Decimal("0.30")`, `require_max_dd_pct: Decimal = Decimal("25")`, `require_recovery_months_max: int = 18`. Add `PerRegimeMetrics(regime, n_days, cum_return_pct, sharpe, sortino, max_dd_pct, hit_rate)` and `gates_passed: dict[str, bool]` to `WalkForwardAggregate`. Modify window-generation logic to enforce regime stratification (BULL, SIDEWAYS, BEAR each present in train AND test proportionally). Rename `_aggregate_windows` to `_aggregate_windows_with_gates`.

**Backend (new):**
- `backend/algo/backtest/metrics.py` — DSR closed-form via `deflated_sharpe_ratio(obs_sharpe: float, n_trials: int, sample_length: int) -> float` (Bailey paper Φ formula with skew/kurtosis adjustment). PBO via `probability_of_backtest_overfitting(is_ranks: list[int]) -> float` (CSCV logic: for each fold, OOS rank of best-IS variant; PBO = fraction in bottom half). `per_regime_breakdown(equity_curve: list[dict], regime_labels: dict[date, str]) -> PerRegimeMetrics` aggregating returns/Sharpe/Sortino/DD per BULL/SIDEWAYS/BEAR window. Recovery time `recovery_months_from_dd(equity_curve) -> int` (number of months from peak DD until recovery to HWM).

**Backend (modified):**
- `backend/algo/backtest/runs_repo.py` — persist `gates_passed_json JSONB NULL` and `per_regime_metrics_json JSONB NULL` on `algo.runs` rows (mode='walkforward').
- `backend/algo/routes/walkforward.py` — extend GET response to include `gates_passed` and per-regime breakdown; add `/v1/algo/walkforward/{id}/gates` endpoint returning `{max_dd_ok, recovery_ok, per_regime_non_neg, dsr_ok, pbo_ok, overall_pass}`.

**Frontend (extend):**
- `frontend/components/algo-trading/WalkForwardSubTab.tsx` — add five traffic-light gate strip (green ✓ / amber ⚠ / red ✗) rendered below aggregate summary cards. Per-regime metric grid (3 rows: BULL, SIDEWAYS, BEAR with Sharpe, Sortino, max DD, return %). Equity curves re-colored by regime (green for BULL windows, gray for SIDEWAYS, red for BEAR). Gate-state tooltip on each light (e.g. "Max DD: 22% ✓ | Recovery: 14mo ✓").

**Frontend (modified):**
- `frontend/components/algo-trading/LiveModeToggle.tsx` (or embedded in TradePanel) — add gate check before enabling toggle. Disabled state shows tooltip "Walk-forward gate: 4/5 passed (PBO 0.42 > 0.30)" when gates not all green. Call `GET /v1/algo/walkforward/{id}/gates` on toggle-click pre-submission.

**E2E:**
- `e2e/tests/frontend/algo-trading-regime-walkforward.spec.ts` — kick off walkforward on a test strategy (over 2007–2025, regime-stratified); verify 5 traffic-light gates render + at least 1 regime present in output; click live-toggle disabled state and verify tooltip.

---

## High-level task list (expand at session start)

1. **Regime-stratified window generation** — extend `walk_windows()` to read `stocks.regime_history`, label each train/test date with BULL/SIDEWAYS/BEAR, enforce ≥1 sample per regime in train + test of every window (raise ValueError if impossible).

2. **DSR closed-form formula** — implement `deflated_sharpe_ratio(obs_sharpe, n_trials, sample_length)` per Bailey et al. paper. Unit test against paper sample (expected 0.42 → 0.35 DSR post-deflation).

3. **PBO closed-form formula** — implement `probability_of_backtest_overfitting(is_ranks)` via combinatorial validation set split (research §4.3). Unit test against Bailey paper sample.

4. **Per-regime metric breakdown** — `per_regime_breakdown()` computes Sharpe, Sortino, max DD, cum return separately for BULL/SIDEWAYS/BEAR sub-sequences of equity curve.

5. **Recovery time calculation** — `recovery_months_from_dd(equity_curve)` finds first bar where NAV ≥ HWM after DD trough; return month delta.

6. **Gate evaluation logic** — `evaluate_5_gates(aggregate, per_regime_breakdown, dsr, pbo)` returns `gates_passed: dict[str, bool]` with keys `max_dd_ok`, `recovery_ok`, `per_regime_non_neg`, `dsr_ok`, `pbo_ok`. All must be True for `overall_pass`.

7. **DB persistence** — update `run_walkforward_job()` to compute metrics post-completion, serialize `gates_passed` + `per_regime_metrics` as JSONB, persist on parent walkforward row.

8. **GET /v1/algo/walkforward/{id}/gates endpoint** — return gate status + recommendations for failed gates (e.g. "PBO 0.42 exceeds 0.30 threshold — consider lengthening test window or reducing hyperparameter sensitivity").

9. **Frontend gate-strip UI** — render 5 circles (traffic lights) with hover tooltips. Color: green (pass) / amber (close) / red (fail). Click to show detail modal with gate formula + failure reason.

10. **Per-regime metrics grid** — 3-row table (BULL / SIDEWAYS / BEAR) × columns (n_days, return %, Sharpe, Sortino, max DD %). Populate from `per_regime_metrics_json`.

11. **Regime-colored equity curves** — extend ECharts config to re-color each window's curve by its majority regime (fetch regime_history for window dates).

12. **Live-toggle gate check** — before toggle submission, call `GET .../gates`, check `overall_pass === true`, show inline error if not.

13. **Documentation** — `docs/algo-trading/regime-walkforward.md` with formulas, gate definitions, examples.

---

## Acceptance

- [ ] `walk_windows(..., regime_stratified=True)` over 2007–2025 ensures BULL+SIDEWAYS+BEAR each represent ≥10% of train+test samples in every window (or raises ValueError).
- [ ] `deflated_sharpe_ratio(obs_sharpe=1.2, n_trials=10, sample_length=252)` matches Bailey paper sample (±0.02).
- [ ] `probability_of_backtest_overfitting([...ranks...])` returns ∈ [0, 1] and matches Bailey sample (±0.05).
- [ ] `per_regime_breakdown()` on test equity curve produces PerRegimeMetrics for each of BULL, SIDEWAYS, BEAR with non-zero sample counts.
- [ ] `recover_months_from_dd(sample_equity_curve)` correctly identifies month to recovery (validated against manual chart).
- [ ] Running walkforward on Golden Cross v1 over 2007–2025 produces `gates_passed: {max_dd_ok, recovery_ok, per_regime_non_neg, dsr_ok, pbo_ok}` serialized to parent row.
- [ ] Frontend renders 5 traffic-lights; all green for a robust strategy, at least one red for a weak one.
- [ ] Live-mode toggle disabled when `overall_pass === false`; tooltip shows failing gate(s).
- [ ] E2E: submit walkforward → 5 lights + per-regime grid render within 5s → click live-toggle disabled state → tooltip shows gate status.
- [ ] No regression on V2-2 walkforward (non-regime-stratified mode still works via `regime_stratified=False`).

---

## Constraints

- **DSR formula is closed-form per Bailey/López de Prado**, NOT via Monte Carlo simulation. Anchor on Bailey paper sample for validation test.
- **PBO via CSCV per research §4.3** — verify against Bailey paper sample, not custom implementation.
- **Regime-stratified means BULL+SIDEWAYS+BEAR each present in train+test of every window.** If a window cannot satisfy this (e.g., backtest period only covers 1 month of bull), raise ValueError with guidance to lengthen backtest period.
- **Live-mode toggle migration:** Existing live runs grandfathered (don't auto-disable on day of gate change). Next *new* live-toggle activation post-integration-merge enforces 5-gate requirement per spec §7.1.
- **Anti-pattern guard:** `test_no_forward_returns_in_factor_features` is a CI gate. Per-regime labeling uses `stocks.regime_history` (pre-computed), NEVER forward returns as label.
- **Recovery time:** months from DD trough to recovery above HWM. If recovery never occurs within backtest window, return total window length in months (flag as pathological).

---

## Out of scope for REGIME-5

- **CPCV (Combinatorial Purged CV)** — deferred to v4 (research §4.2). Regime-stratified rolling walk-forward is sufficient for deterministic strategies.
- **Auto-promotion of strategies** based on gate passes — walkforward gates *enable* the live toggle; user still explicitly approves.
- **Per-sector regime overlay** — global regime + sector rotation template (REGIME-7) cover the practical use.
- **ML regime classifier** — rule-based + HMM hybrid only (REGIME-1).
- **Drawdown throttle interaction with gates** — sizing throttle is runtime (REGIME-4); gates are validation stage only.
