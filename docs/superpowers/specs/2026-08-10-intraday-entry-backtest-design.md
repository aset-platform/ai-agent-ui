# Intraday Entries in the Two-Clock Backtest (PRE-3) — Design

**Date:** 2026-08-10
**Ticket:** ASETPLTFRM-477 (PRE-3), under ASETPLTFRM-474 (R2 entry-strength gate)
**Status:** Design approved; spec for review before planning.

---

## 1. Motivation

Release-2's entry-strength gate needs an out-of-sample labeled dataset far
larger than the ~42 real live trades. The live runtime (R1) already enters
**intraday, all-day** (OR-trigger: yesterday's daily close OR any intraday
bar with `rsi_2 <= 5`, from 09:30), but the **backtest still enters once per
day** — so a backtest cannot faithfully reproduce what R1 trades, and cannot
generate the volume R2 calibrates on.

Two structural facts (verified in `backend/algo/backtest/runner.py`):

- In two-clock mode the outer loop walks the **execution** timeline; **exits**
  (trailing / stop / time-stop / regime) run on every exec bar (ungated,
  ~L609-1123), but **entries** are gated by `is_signal_bar`
  (L594-596), which is true only on the day's last exec bar (L567-575) — so
  entries fire once/day.
- For a daily-signal strategy, **per-15m-bar `rsi_2` is never computed**:
  `load_intraday_features_window(...)` is called only in the native-intraday
  branch (L198-215); the two-clock block loads raw OHLCV exec bars
  (`load_intraday_bars_window`, L510-516) for stops, not features. Entry
  features are daily-close only (L246-264).

## 2. Goal & scope

**Goal:** extend the two-clock engine so a daily-signal strategy evaluates
**entries on the execution clock** (mirroring live R1), so a normal
multi-year backtest yields a large volume of realistic trades whose
`(entry features → win/loss outcome)` records are the R2 labeled set.

**Decided scope (approved):**
- **Output:** engine change only; the backtest's closed trades **are** the
  labeled set. No separate per-candidate / counterfactual emission (that
  stays with PRE-6's deferred counterfactuals / future work).
- **Fidelity:** replicate the **full live R1 entry stack** — OR-trigger +
  09:30 floor (skip the 09:15 opening bar) + falling-knife veto
  (`ret_3d <= -10%` OR `gap <= -4%`) + once-per-day dedup + existing
  `cooldown_after_failed_exit_days`.
- **Universe:** the strategy's own universe filter (discovery + `min_adtv`),
  **not** the live account gates (allow-list / budget / caps) — those are
  operational constraints, not the strategy's edge, and applying the
  3-ticker allow-list would kill backtest volume.

**Non-goals:** walkforward (inherits via per-fold `run_backtest`, no separate
change); paper/live runtimes (already intraday via R1); 1m/5m grains (no
history — 15m only); changing exit logic.

## 3. Design

### 3.1 Load intraday features at the exec grain (the missing piece)
In two-clock mode, additionally call `load_intraday_features_window(
tickers=exec_covered, interval_sec=execution_interval_sec (=900), period…)`
and index by `(ticker, ts_ns)`, exactly as the native-intraday branch does
(L187-194). Tickers without 15m coverage (`daily_fallback_tickers`,
L526-528) get no intraday features → daily-close-only entry (§3.4).

### 3.2 New ungated intraday-entry block in the exec-bar loop
Inside `for bar_date, ts_ns in walk_timeline` (L591), add an entry-evaluation
block that runs on **every exec bar** (not gated by `is_signal_bar`), sited
alongside the exit blocks, for each **flat** strategy-universe ticker, from
the **09:30 bar onward**:

- **OR-trigger** — fire a BUY if the AST (`Evaluator.eval_node` on
  `strategy.root`) evaluates BUY against the **intraday-forming** features at
  this exec bar, **OR** the prior **daily-close** signal fired (evaluated on
  the day's first exec bar), mirroring live `runtime.py` `_eval_entry_on_closed_bar`
  + forming-bar OR (`live/runtime.py:3991-4016`).
- **Falling-knife veto** — skip if prior-3-day return `<= ALGO_ENTRY_FALLING_KNIFE_3D_PCT`
  (−10) OR entry-day gap `<= ALGO_ENTRY_FALLING_KNIFE_GAP_PCT` (−4), reusing
  the live thresholds/semantics.
- **09:30 floor** — no entry on the first (09:15) exec bar of the day.
- **Once-per-day-per-ticker dedup** + the existing cooldown. The existing
  `is_signal_bar`-gated block (L1150) is reconciled into this path (see §5).
- Assemble entry-time features for the trade record here (its own
  `assemble_per_bar_features` call — the L1207 call currently lives only in
  the signal-bar block).

### 3.3 Fill model (no lookahead)
An intraday entry triggering at exec bar `T` fills at the **next exec bar's
open** via `exec_sim_broker` (not the daily `sim`; today `_entry_ts_ns = None
if two_clock`, L1288 — this changes for intraday-triggered entries). The
entry `OrderIntent` MUST set `product` from `strategy.product`
(CNC→DELIVERY, MIS→INTRADAY) — `_action_to_intent` (L1728) currently never
sets `product` on buys (L1766-1772), so `SimBroker`'s ts-based inference
(`sim_broker.py:195-199`) would wrongly bill INTRADAY. This is the exact
algo.md "★ Fee-product gotcha", previously fixed only on exits (L862-866).

### 3.4 Coverage fallback
Tickers without 15m coverage keep the current daily-close, once/day entry
(via the reconciled signal-bar path on the day's representative bar), and are
surfaced in `BacktestSummary.daily_fallback_tickers`. Daily-resolution
falling-knife inputs (3d/gap) come from `stocks.ohlcv`.

### 3.5 Labeled output
No new table. A normal backtest run now produces intraday-entered trades; the
per-trade feature snapshot (`runner.py` ~L1540) is extended to capture the
entry-time features (rsi_2, dist_sma50/200, ret_3d, gap, trigger leg) so each
closed trade carries its entry context. R2 reads these closed-trade records.

## 4. Key code sites (from the engine map)
- Outer loop / `is_signal_bar`: `runner.py:587-596`, `567-575`.
- Entry block (to reconcile): `runner.py:1150`, features `1195-1219`, AST
  `1227-1230`, entry fill routing `1288`.
- Exit blocks (pattern to mirror — ungated exec-bar): `runner.py:609-1123`;
  `ExecutionSimulator` `execution_simulator.py:34`; `exec_sim_broker`
  `runner.py:579-583`.
- Intraday features loader (native branch to reuse): `runner.py:198-215`,
  `backend/algo/features/loader.py`.
- Fee/product: `sim_broker.py:195-199`, `249-253`; `_action_to_intent`
  `runner.py:1728`, `1766-1795`; `OrderIntent.product` `types.py:128`.
- Live parity reference: `live/runtime.py:3991-4016`, `_eval_entry_on_closed_bar` `:5493`.

## 5. Main implementation risk — reconciling the entry gate
Today `is_signal_bar` fires the L1150 entry block on the day's **last** exec
bar. This design moves entry evaluation to **every** exec bar (from 09:30),
with the daily-close leg acted on the day's **first** exec bar. The refactor
must: (a) not double-enter a ticker within a day; (b) preserve the plain
daily / native-intraday / no-two-clock paths **byte-identically** (only
two-clock daily-signal behavior changes); (c) keep all exit-only two-clock
tests green. Recommended approach: unify entry evaluation into a single
exec-bar function that is a no-op outside two-clock daily-signal mode.

## 6. Testing
New `backend/algo/backtest/tests/test_two_clock_entries.py`:
1. Intraday-only dip (`rsi_2<=5` at a mid-day 15m bar, daily close not
   confirming) → entry fires mid-day (was: no entry).
2. Daily-close oversold but intraday-bounced → still enters (OR semantics).
3. Falling-knife veto blocks a −12%/3d or −5% gap candidate.
4. 09:30 floor — no entry on the 09:15 bar.
5. Once-per-day dedup — one entry per ticker/day across many exec bars.
6. Fee product — an intraday-timed CNC entry bills DELIVERY (regression for
   the fee gotcha, mirrored onto entries).
7. Uncovered ticker → daily-fallback once/day entry.
8. Parity — existing `test_two_clock_runner.py` exit tests + plain-daily /
   native-intraday paths unchanged.

## 7. Config
Reuse the live env knobs for the veto (`ALGO_ENTRY_FALLING_KNIFE_3D_PCT`,
`_GAP_PCT`). No new schema. No scheduler/job changes.

## 8. Follow-ups (out of scope)
- Counterfactual per-candidate labeling (rejected candidates' outcomes) —
  remains PRE-6-deferred / future.
- If backtest volume proves the gate, R2 wires the calibrated composite into
  live/paper/backtest (its own ticket).
