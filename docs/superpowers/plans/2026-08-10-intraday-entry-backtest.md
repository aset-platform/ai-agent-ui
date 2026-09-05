# Intraday Entries in Two-Clock Backtest (PRE-3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Extend the two-clock backtest so a daily-signal strategy evaluates ENTRIES on the 15-min execution clock (mirroring live R1's all-day OR-trigger), so a normal multi-year backtest yields the volume of realistic labeled trades R2 calibrates on.

**Architecture:** All changes in `backend/algo/backtest/runner.py` (+ small helpers). The exec-bar loop already runs EXITS ungated on every exec bar; add an entry path that runs the same way, gated only by "two-clock daily-signal mode" so every other path (plain-daily, native-intraday, non-two-clock) stays byte-identical. Spec: `docs/superpowers/specs/2026-08-10-intraday-entry-backtest-design.md` (read it).

**Tech Stack:** Python 3.12, pytest (Docker: `docker compose exec -e PYTHONPATH=.:backend backend python -m pytest <path> -v`, foreground, targeted files only — broad runs auto-background and stall).

## Global Constraints
- Line ≤79; `X | None`; no bare `except`; black/isort unavailable (format by hand); zero NEW flake8.
- **Fidelity = live R1 entry stack:** OR-trigger (yesterday daily-close OR intraday-forming `rsi_2<=5`), 09:30 floor (skip the 09:15 opening exec bar), falling-knife veto (`ret_3d <= ALGO_ENTRY_FALLING_KNIFE_3D_PCT` (-10) OR `gap <= ALGO_ENTRY_FALLING_KNIFE_GAP_PCT` (-4)), once-per-day-per-ticker dedup, existing `cooldown_after_failed_exit_days`. Reference: `backend/algo/live/runtime.py:3991-4016` + `_eval_entry_on_closed_bar` (:5493).
- **No lookahead:** an intraday entry triggering at exec bar T fills at the NEXT exec bar's open via `exec_sim_broker`.
- **Fee-product (algo.md ★ gotcha):** the entry `OrderIntent` MUST set `product` from `strategy.product` (CNC→DELIVERY, MIS→INTRADAY); never rely on `SimBroker`'s ts-based inference (`sim_broker.py:195-199`).
- **Universe = strategy's own** (discovery + `min_adtv`); do NOT apply account gates (allow-list/budget/caps).
- **PARITY IS SACROSANCT:** only two-clock daily-signal behavior may change. Plain-daily, native-intraday, and non-two-clock paths must stay byte-identical; all existing `test_two_clock_runner.py` exit tests must stay green. Make the new entry evaluation a single function that is a no-op unless `two_clock and not is_intraday`.
- Key sites: outer loop `runner.py:591`; `is_signal_bar` `594-596`/`567-575`; entry block `1150`, feature assembly `1195-1219`, AST `1227-1230`, entry fill routing `1288`; exit blocks (mirror) `609-1123`; `exec_sim_broker` `579-583`; native intraday-feature load `198-215`; `_action_to_intent` `1728`/`1766-1795`; `OrderIntent.product` `types.py:128`; per-trade feature snapshot `~1540`.

---

### Task 1: Load intraday features at the exec grain (data prerequisite)

**Files:**
- Modify: `backend/algo/backtest/runner.py` (two-clock setup block, ~L479-583)
- Test: `backend/algo/backtest/tests/test_two_clock_entries.py` (new)

**Interfaces produced:** in two-clock mode, an `exec_intraday_features` structure indexed `(ticker, ts_ns) -> feature dict` (incl. `rsi_2`), populated for `exec_covered` tickers; empty for `daily_fallback_tickers`. Consumed by Task 2.

- [ ] **Step 1: Write failing test** — build a two-clock `run_backtest` scenario (mock `load_ohlcv_window`, `intraday_coverage`, `load_intraday_bars_window`, and the new `load_intraday_features_window`) and assert the run loads per-15m features for a covered ticker (spy that `load_intraday_features_window` is called with `interval_sec=execution_interval_sec` and the exec-covered tickers). Mirror the mocking style in `test_two_clock_runner.py`.
- [ ] **Step 2: Run (Docker) — verify fail.**
- [ ] **Step 3: Implement** — in the two-clock setup (~L510-528, alongside `load_intraday_bars_window`), also call `load_intraday_features_window(tickers=exec_covered, interval_sec=execution_interval_sec, period_start=…, period_end=…)` (reuse the exact call shape from the native-intraday branch at L198-215) and store indexed by `(ticker, ts_ns)`. Guard: only when `two_clock and not is_intraday`; skip uncovered tickers.
- [ ] **Step 4: Run — verify pass.**
- [ ] **Step 5: Commit** (`feat(algo): load intraday features at exec grain for two-clock daily strategies`; co-author trailer; no push).

---

### Task 2: Intraday entry evaluation on the exec clock (core)

**Files:**
- Modify: `backend/algo/backtest/runner.py` (new entry-eval path in the exec loop; reconcile the `is_signal_bar` block at L1150; `_action_to_intent` product fix at L1766-1795)
- Test: `backend/algo/backtest/tests/test_two_clock_entries.py`

**Interfaces:** consumes Task 1's `exec_intraday_features`. Produces intraday-timed entries filled via `exec_sim_broker` at next-exec-bar open.

- [ ] **Step 1: Write failing tests** (the §6 matrix): (1) intraday-only dip enters mid-day; (2) daily-close-oversold-but-bounced still enters (OR); (3) falling-knife veto blocks (−12%/3d and −5% gap); (4) 09:30 floor (no entry on 09:15 bar); (5) once-per-day dedup (one entry/ticker/day across many exec bars); (6) fee product = DELIVERY on an intraday CNC entry; (7) uncovered ticker → daily-fallback once/day entry; (8) parity: a plain-daily (non-two-clock) run's entries are unchanged.
- [ ] **Step 2: Run (Docker) — verify fail.**
- [ ] **Step 3: Implement the entry path.**
  - Factor a single `_evaluate_intraday_entry(...)` helper called inside the exec-bar loop (near the exit blocks), a NO-OP unless `two_clock and not is_intraday`. For each flat strategy-universe ticker, on exec bars at/after the 09:30 bar:
    - Compute the forming-bar AST decision from `exec_intraday_features[(ticker, ts_ns)]` via `Evaluator.eval_node`; and the daily-close decision (from daily features, acted on the day's first exec bar) — OR them.
    - Apply the falling-knife veto (prior-3-day + gap from daily `stocks.ohlcv`).
    - Enforce once-per-day-per-ticker dedup + `cooldown_after_failed_exit_days`.
    - On a BUY, emit an entry `OrderIntent` with `product` set from `strategy.product`, filled via `exec_sim_broker` at the next exec bar open.
  - Reconcile the `is_signal_bar` entry block (L1150): for `two_clock and not is_intraday`, entry evaluation now flows through `_evaluate_intraday_entry`; the L1150 block must NOT also fire for those tickers (prevent double-entry). Keep L1150 intact for all other modes (plain-daily, native-intraday, uncovered-ticker daily fallback).
  - `_action_to_intent` (L1766-1795): set `product` on the buy `OrderIntent`.
- [ ] **Step 4: Run — verify pass (all 8).**
- [ ] **Step 5: Parity check** — run the FULL existing `backend/algo/backtest/tests/test_two_clock_runner.py` + `test_walkforward_two_clock.py` foreground; confirm unchanged/green.
- [ ] **Step 6: flake8 zero-new, commit** (`feat(algo): intraday entry evaluation on the exec clock (two-clock)`; no push).

---

### Task 3: Entry-time feature snapshot on the trade record + smoke

**Files:**
- Modify: `backend/algo/backtest/runner.py` (per-trade feature snapshot ~L1540)
- Test: `backend/algo/backtest/tests/test_two_clock_entries.py`

- [ ] **Step 1: Failing test** — a two-clock backtest closed trade carries its entry-time features (rsi_2, dist_sma50/200, ret_3d, gap, trigger leg) in its record.
- [ ] **Step 2: Run — fail.**
- [ ] **Step 3: Implement** — ensure the intraday entry path populates the same per-trade feature snapshot the daily path produces (~L1540), sourced from the exec-bar features used to trigger.
- [ ] **Step 4: Run — pass.**
- [ ] **Step 5: Smoke** — run a real short two-clock backtest of the v5 strategy over a covered window (e.g. a 3–6 month range with 15m coverage) in the container; report: # trades, that intraday-timed entries appear (entries not all at daily-close ts), and that plain-daily mode over the same window differs (more/earlier entries). Report counts; no assertion needed beyond sanity.
- [ ] **Step 6: flake8, commit** (`feat(algo): capture entry-time features on two-clock intraday trades`; no push).

## Self-Review
- Coverage: intraday features loaded (T1); OR-trigger + floor + veto + dedup + fill + product (T2); labeled output (T3); parity guarded (T2 Step 5). All §6 tests mapped.
- Deferred: counterfactual per-candidate labeling (PRE-6/future); gate wiring (R2).
- Risk: the L1150 reconciliation — mitigated by the no-op-outside-two-clock-daily helper + the parity test gate.
