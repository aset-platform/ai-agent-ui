# Algo Intraday Entry Window + Entry-Strength Filter — Design

**Date:** 2026-08-09
**Strategy under study:** `RSI(2) Connors Daily v5 + 5% price stop`
(`strategy_id = 5c4aa66f-887c-44d6-a945-cefd4feec926`, live)
**Status:** Design approved (Release 1 scope); Release 2 is research-gated.

---

## 1. Motivation

A July-2026 post-mortem of the live strategy (32 closed trades) established:

- **Win rate 62.5% but ~breakeven** — avg loss (−3.3%) ≈ 1.6× avg win (+2.8%).
- **Entries are not separable at entry.** Across three independent lenses
  — the strategy AST features, recomputed indicators, and the platform's
  own QM/ESS quality scores (backfilled for all 32 trades) — winning and
  losing entries look statistically identical. The ESS ("Entry Strength
  Score") gate passed 100% of trades. The **only** quality factor that
  separated winners from losers was **MDD-percentile** (drawdown
  resilience: winners median 86th vs losers 66th).
- **Losers reveal themselves post-entry via fast MAE** — of intraday-covered
  losers, all dipped ≥2% underwater quickly; winners rarely did.
- **The entry clock is an accidental gate.** The live runtime enters at
  **14:20 IST** against the still-forming daily bar (`runtime.py:141-149`,
  `ALGO_DAILY_MIN_EVAL_TIME_IST`), silently deviating from the strategy's
  own `15:25` bar-close spec, and misses brief intraday oversold dips that
  bounce back before the gate.

**Goal:** replace the confusing 14:20 gate with an all-day intraday entry
window, coupled with an entry-strength filter so opening the window does
not simply let us catch falling knives.

## 2. Scope & sequencing

Two coupled sub-projects, shipped **phased** (approved):

- **Release 1 (this spec):** the timing change + a high-confidence
  **falling-knife veto** (the only strength dimension we can calibrate
  today) + **shadow logging** of the other strength dimensions
  (non-gating). Unblocks the timing fix, protects live capital with the
  one guard we trust, and produces the labeled dataset to calibrate the
  rest.
- **Release 2 (separate spec, research-gated):** the full 4-dimension
  entry-strength composite gate, calibrated on the shadow data + a longer
  backtest, only turned on once it demonstrably separates winners from
  losers out-of-sample. NOT designed here.

## 3. Current behavior (as-is)

`runtime.py` `_on_bar_close` entry path (~L3916-4024) has two gates:

- **Gate A — `_MIN_BUY_TIME_IST` (09:30):** no BUY before 09:30
  (09:00–09:29 observation). **Kept.**
- **Gate B — `_MIN_EVAL_TIME_IST` (14:20):** before 14:20 a BUY fires only
  if yesterday's closed bar was oversold **AND** today's forming bar still
  confirms (dual-bar AND); a fresh intraday-only dip is deferred to 14:20.
  **Removed.**

Safety exits (stop-loss, time-stop, STOP_HIT, GTT-triggered, MIS
square-off) are never time-gated today.

## 4. Release 1 design

### 4.1 Entry triggers — OR, all day

Remove Gate B. From `_MIN_BUY_TIME_IST` (09:30), on each bar eval, a flat
allowed-ticker fires a BUY if **either**:

- **closed-bar trigger** — yesterday's completed daily close satisfies the
  strategy entry condition (RSI2 ≤ 5 + the AST's AND-filters), via the
  existing `_eval_entry_on_closed_bar(history)`; **or**
- **forming-bar trigger** — the full-history-including-today's-forming-bar
  signal is BUY (today's intraday RSI2 ≤ 5 at this bar-close).

This deletes the dual-bar AND-confirmation and the
"premature / deferred-until-14:20" branch (`runtime.py:3959-4024`). The
firing is per bar-close, so a brief intraday dip is caught the moment it
prints ≤ 5, and a gap-into-oversold carried from yesterday's close still
fires even if today has bounced.

Dedup / re-entry unchanged: once-per-day-per-ticker via the existing
`_closed_entry_cache` + `_ticker_locked`; same-day re-entry after an exit
remains blocked by the strategy's `cooldown_after_failed_exit_days` (7d).

### 4.2 Observation window / sell floor

- 09:00–09:29 stays observation (no orders placed).
- Keep the 09:30 **BUY** floor.
- **Add a symmetric 09:30 floor for signal-based SELLs** (rebalance / AST /
  discretionary), via a new `_MIN_SELL_TIME_IST` (`ALGO_MIN_SELL_TIME_IST`,
  default 09:30).
- **Safety exits are NEVER gated** — GTT-triggered, hard stop-loss,
  STOP_HIT emergency, time-stop, MIS square-off fire anytime including
  pre-09:30. This preserves the `.claude/rules/algo.md` safety invariant.

### 4.3 Falling-knife veto (the one active gate)

Before firing any BUY, hard-reject if the name is in free-fall; emit a
`signal_rejected` event, `reason="falling_knife_veto"`, carrying the
metric values. Approved thresholds (validated against the 32 July trades —
blocks both SUPRIYA losers, zero winners):

- prior-3-day return ≤ **−10%** (`ALGO_ENTRY_FALLING_KNIFE_3D_PCT`), **or**
- entry-day gap-down ≤ **−4%** (`ALGO_ENTRY_FALLING_KNIFE_GAP_PCT`).

Both are env-overridable so they can be tuned without a redeploy. Metrics
computed from the same daily history already loaded for the signal
(prior-3-day = `close[-1]/close[-4] − 1`; gap = `open_today/close[-1] − 1`).

**Overfit caveat:** thresholds are informed by a single 32-trade month.
Shadow logging (§4.4) exists precisely to refine them on more data before
Release 2.

### 4.4 Shadow instrumentation (non-gating)

On every RSI2 ≤ 5 BUY candidate, emit an `entry_strength_snapshot` event
(non-gating; must never block or alter the order path) capturing the live
context needed to calibrate Release 2:

- trigger type: `yesterday_close` | `intraday_forming` | `both`
- intraday RSI2 (forming) and yesterday-close RSI2
- ATR%, distance from SMA50 / SMA200
- prior-1d / prior-3d return, entry-day gap
- **breadth:** count and % of the allowed universe currently RSI2 ≤ 5
- veto outcome (fired? which threshold?)

The QM/ESS quality dimensions (incl. MDD-percentile) are joined later from
the EOD `stocks.entry_quality_daily` table by `(ticker, trade_date)` — no
need to recompute cohort percentiles live.

### 4.5 Config knobs

- Retire `ALGO_DAILY_MIN_EVAL_TIME_IST` (Gate B removed).
- New: `ALGO_ENTRY_FALLING_KNIFE_3D_PCT` (−10), `ALGO_ENTRY_FALLING_KNIFE_GAP_PCT` (−4),
  `ALGO_MIN_SELL_TIME_IST` (09:30).
- Unchanged: `ALGO_MIN_BUY_TIME_IST` (09:30).

## 5. Safety invariants (must not regress)

Per `.claude/rules/algo.md`, the entry-path edit must not touch:

- GTT two-path accounting (Piece A poll + Piece B postback).
- Budget-reservation release on GTT-triggered exits.
- STOP_HIT emergency SELL sacrosanct (cancel-failure must not skip SELL).
- Caps freshness (`self._caps` re-read, allowed_tickers).
- No silent qty=0 drop (emit `signal_rejected insufficient_capital_qty_zero`).

Change-impact discipline: grep every call site of the entry-gate constants;
trace both GTT paths; verify no downstream state (budget ledger, position
tracker, `_in_flight`, events) is affected by the entry-path change.

## 6. Testing (TDD — happy + error paths minimum)

1. Intraday-only dip fires a BUY after 09:30 (previously deferred to 14:20).
2. Yesterday-oversold-but-bounced still fires (OR trigger).
3. BUY before 09:30 deferred (observation).
4. Signal-based SELL before 09:30 deferred; **safety SELL (STOP_HIT / GTT)
   fires pre-09:30** (never gated).
5. Falling-knife veto rejects a free-fall entry and emits
   `signal_rejected reason=falling_knife_veto` with metric values.
6. Veto does NOT block a normal oversold dip (winner-like inputs pass).
7. `entry_strength_snapshot` emitted with correct fields and never blocks
   or mutates the order path.
8. Once-per-day dedup / cooldown respected.

## 7. Rollout

- Behind the env knobs above so behavior is togglable without a redeploy.
- **Restarting the backend kills the live Kite WS session** (`algo.md`) —
  the user must reconnect from the Algo Trading UI; coordinate the deploy,
  never restart mid-session unprompted.
- After deploy, monitor `entry_strength_snapshot` + `falling_knife_veto`
  events for a few sessions before designing Release 2.

## 8. Release 2 (out of scope here — sketch only)

Calibrate a 4-dimension entry-strength composite — (1) pullback
depth/absorption, (2) stock strength/resilience incl. MDD-percentile,
(3) market breadth/regime, (4) falling-knife veto (promoted from Release 1)
— against the shadow dataset + a longer backtest (likely needs two-clock
engine work to simulate intraday entries faithfully). Turn the composite
gate on only if it separates winners from losers out-of-sample. Its own
spec.

## 9. Open questions / follow-ups

- Optional: backfill the product `stocks.entry_quality_daily` table for
  June 24–July 12 (a write-to-Iceberg run) so the page shows historical
  quality for those dates — separate task.
- Consider auditing `algo.live_caps` edits (no history table today), so
  future post-mortems can recover the allowed_tickers set as-of a date.
