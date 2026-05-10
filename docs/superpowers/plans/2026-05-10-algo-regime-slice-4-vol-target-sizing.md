# Regime-Aware Multi-Factor System — Slice REGIME-4: Volatility-Targeted Sizing

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session is about to start.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Implement a three-stage sizer (volatility-target → hard caps → drawdown throttle) as the composition backbone for position sizing across all three runtimes (backtest / paper / live). Add new AST sizing modes `qty: {vol_target_pct: 1.5}` and `qty: {kelly_fraction: 0.25}` to complement existing `{shares}`, `{notional_inr}`, `{all}` modes. Drawdown throttle reads peak NAV from `algo.runs.equity_curve` and applies a 5-tier multiplier ladder (5/10/15/20% DD → 0.75/0.5/0.25/0×).

**Architecture:** Pure functions in a new `backend/algo/sizing/` package (3 modules: `vol_target.py`, `caps.py`, `drawdown_throttle.py`). Sizer composition wired into each of the three runtime entry points (backtest / paper / live) after qty resolution but before pre-trade checks. Kelly sizing requires strategy `expected_edge` metadata (links to REGIME-3). Backward compat: existing sizing modes stay untouched.

**Tech Stack:** Python 3.12 / Decimal for precision / Pydantic v2.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §3.4 (sizing module) + §5.1 REGIME-4 row (8 SP) + §6.1 REGIME-4 test row.

**Research anchor:** `docs/superpowers/research/2026-05-10-regime-aware-multifactor-research.md` — §5 (Position Sizing — Volatility-Targeted Approach) + §6 (metrics / drawdown throttle ladder).

**Branch:** `feature/algo-regime-slice-4-vol-target-sizing` off `feature/algo-regime-multifactor-integration`.

**Depends on:** REGIME-2a merged (provides `realized_vol_60d` factor in cache via `stocks.daily_factors` Iceberg).

**Estimated SP:** 8

---

## File Structure

**Backend (new):**
- `backend/algo/sizing/__init__.py` — public API exports (`vol_target_qty`, `PositionCaps`, `dd_multiplier`).
- `backend/algo/sizing/vol_target.py` — `vol_target_qty(target_portfolio_vol_pct: Decimal, nav: Decimal, stock_price: Decimal, stock_realized_vol_annual: Decimal, n_positions_target: int) -> int` pure function.
- `backend/algo/sizing/caps.py` — `PositionCaps` class with configurable `per_position_max_pct`, `per_sector_max_pct`, `cash_floor_pct` + `cap(intended_qty, intended_value, nav, sector, current_sector_exposure) -> int` method.
- `backend/algo/sizing/drawdown_throttle.py` — `dd_multiplier(dd_from_peak_pct: Decimal) -> Decimal` pure function implementing the 5-tier ladder. Helper `read_nav_peak(user_id: str, strategy_id: str, run_date: date) -> Decimal` to fetch from `algo.runs.equity_curve`.
- `backend/algo/tests/test_sizing_vol_target.py` — unit tests for vol-target qty calculation (inverse vol scaling, portfolio vol budget distribution across N positions).
- `backend/algo/tests/test_sizing_caps.py` — unit tests for per-position + per-sector cap enforcement (truncation, priority rules).
- `backend/algo/tests/test_sizing_drawdown_throttle.py` — table-driven boundary tests for DD ladder (5%, 10%, 15%, 20% thresholds) + multiplier output.
- `backend/algo/tests/test_sizing_composition.py` — integration test: vol-target → cap → DD multiplier composition order (cap applied before DD throttle).

**Backend (modified):**
- `backend/algo/strategy/ast.py` — extend discriminated union `qty: { ... }` with two new variants:
  - `qty: { vol_target_pct: Decimal }` — volatility-targeted (percentage of portfolio vol budget).
  - `qty: { kelly_fraction: Decimal }` — fractional Kelly (requires strategy `expected_edge: Decimal` metadata; reads from `algo.strategy_metadata`).
- `backend/algo/backtest/runner.py` — after qty resolution from AST, apply composition: `final_qty = dd_multiplier(dd_pct) * caps.cap(base_qty, ...)`. Same pattern in paper + live.
- `backend/algo/paper/runtime.py` — integrate sizer composition in signal execution path.
- `backend/algo/live/runtime.py` — integrate sizer composition in signal execution path.
- `backend/algo/runs_repo.py` — no schema changes (equity_curve already exists from V2-2).

**Frontend:** No changes (existing tabs unaffected; sizing logic is backend-only).

**E2E:** No changes (existing backtest E2E covers indirect testing via vol-target mode usage).

---

## High-level task list (expand at session start)

1. **Vol-target pure function** — `vol_target_qty()` per spec; inverse square-root rule for N positions; Decimal precision throughout.
2. **Position caps class** — `PerPositionCaps`, `PerSectorCaps`, `CashFloor` validators; `cap()` method enforces trumping (caps beat vol-target on conflict).
3. **DD throttle pure function** — `dd_multiplier()` table-driven; thresholds 5/10/15/20% DD; multipliers 1.0/0.75/0.5/0.25/0×.
4. **DD throttle helper** — read NAV peak from `algo.runs.equity_curve` (Iceberg); compute current DD as `(nav_peak - nav_today) / nav_peak`.
5. **AST discriminated union extension** — `VolTargetSizingMode` + `KellySizingMode` Pydantic models; parser extends to recognize new modes.
6. **Kelly sizing integration** — `kelly_qty()` function (reads `strategy.expected_edge` from `algo.strategy_metadata` PG); fails gracefully if `expected_edge` not set.
7. **Backtest runtime integration** — `run_backtest()` adds sizer composition after qty resolution + before pre-trade checks.
8. **Paper runtime integration** — `PaperRuntime.execute_signal()` applies same composition.
9. **Live runtime integration** — `LiveRuntime.execute_signal()` applies same composition.
10. **Backward compat test** — existing strategies using `{shares}` / `{notional_inr}` / `{all}` sizing modes still parse + execute without regression.
11. **Kelly validation test** — strategy with `kelly_fraction: 0.25` but missing `expected_edge` metadata fails with clear error message.
12. **Composition order test** — vol-target generates base qty; cap applies; DD throttle applied last (3-stage pipeline verified).

---

## Acceptance

- [ ] `vol_target_qty(target_vol_pct=1.5, nav=100000, price=1000, realized_vol=0.30, n_positions=10)` returns ⌊15800 / 1000⌋ = 15 shares (per spec formula).
- [ ] `PositionCaps.cap()` with intended_value=15000 INR (15% of nav) against nav=100000, per_position_max=12% truncates to 12000 INR (120 shares @ 1000).
- [ ] `PositionCaps.cap()` enforces per-sector cap: if sector already at 25% exposure + new position would push to 35%, return truncated qty for that sector.
- [ ] DD ladder: 0–5% DD → 1.0×, 5–10% → 0.75×, 10–15% → 0.5×, 15–20% → 0.25×, >20% → 0×. Table-driven tests cover all 5 bands.
- [ ] Composition order: vol-target qty=100 → cap reduces to 80 (per-position limit) → DD multiplier 0.75 applied → final qty=60. Verify all three stages execute in order.
- [ ] `qty: {vol_target_pct: 1.5}` AST parses + resolves correctly; backtest runs end-to-end.
- [ ] `qty: {kelly_fraction: 0.25}` AST parses; fails at signal-time with "expected_edge missing from strategy metadata" if not set.
- [ ] Existing `qty: {shares: 100}` + `{notional_inr: 15000}` + `{all}` modes still work (no regression).
- [ ] Frontend backtest form + existing E2E backtests pass with no changes.

---

## Out of scope for REGIME-4

- Kelly auto-promotion (no auto-flip to Kelly based on walkforward score; user must manually set `kelly_fraction`).
- Portfolio-level drawdown (only strategy-level DD; portfolio DD is REGIME-8 future work).
- Restoration logic beyond ratchet-up guard (full restoration = "ratchet back up after equity recovers to HWM AND vol normalizes"; code comments explain but full automation is post-MVP).
- Custom per-user sizing caps (all caps are global; 5-user scale doesn't justify per-user tuning).
- Regime-adaptive vol targets (vol-target is static per AST; regime adaptivity deferred to v4 multi-strategy optimizer).
- Integration with REGIME-5 live-mode gates (walkforward gates are independent; REGIME-5 wires the gates, not this slice).
