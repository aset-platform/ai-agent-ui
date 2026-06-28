# Intraday execution clock

> **Version:** 0.18.0 (2026-06-28) · **Spec:** `docs/superpowers/specs/2026-06-28-intraday-execution-clock-backtest-design.md`

The backtest and walkforward engines use a **two-clock design** that decouples
a strategy's signal cadence from a finer execution clock. A daily strategy
receives ~25 intraday exit checks per day, faithfully replicating the live
GTT / trailing-stop lifecycle.

---

## The problem with a single clock

Before 0.18.0, a daily strategy's ATR trailing stop, hard stop, and MIS
square-off were evaluated **once per day** — at the same grain as the entry
signal — using a conservative LOW-then-HIGH approximation of the daily bar.

This:

- **Discards intraday path order.** The daily engine cannot know whether the
  day's HIGH ratcheted the trailing stop up *before* the LOW would have hit
  it. The ATR trail is effectively pessimistic and misleading for daily
  strategies.
- **Diverges from live,** where the trailing stop is monitored throughout the
  session via broker GTT and WS ticks, independent of signal cadence.

A user had no real assurance that a trailing strategy behaved as intended until
it ran live.

---

## Two-clock design

```
signal clock  ──────────▶  AST / signal evaluation      (entries, rebalances)
(strategy cadence:          fires ONLY on signal bars
 daily / 15m)

execution clock ──────────▶  ExecutionSimulator           (exits / trailing / square-off)
(finest available:           replays EVERY exec bar
 15m today, per-window)      between signal points
```

**Signal clock** — the strategy's declared cadence. Entries and rebalances
fire on signal bars only (e.g. the daily close bar). This matches live acting
on bar close.

**Execution clock** — the finest available intraday grain for the covered
window. Exits, ATR trailing, hard stops, time stops, and MIS square-off
evaluate on **every** execution bar.

For a **daily** strategy with 15m coverage: 1 signal/day, ~25 exit checks/day.
For a **15m** strategy: signal and execution are the same grain (execution
cannot go finer than the signal data).

ATR and all strategy indicators are computed at **signal grain** — the
execution clock does not recompute indicators.

---

## Data reality and coverage

The only historical intraday source is `stocks.intraday_bars`:

| Grain (`interval_sec`) | Coverage |
|---|---|
| **15m (900)** | ~497 tickers · 2022-06-01 onwards · ~complete (median 25 bars/day) |
| **5m (300)** | none |
| **1m (60)** | none (schema exists; only 6 scratch rows) |

**Consequence:** the finest execution grain available for historical
backtest/walkforward is **15m**. The engine selects resolution from the data,
not from a hardcoded constant — it auto-upgrades when finer history exists.

**Fidelity ceiling (intrinsic):**
backtest ≤ 15m (historical) · paper ≈ 1m (live tick feed) · live = tick.

### Per-ticker routing

Resolution is chosen **per (ticker, backtest window)** via `intraday_coverage()`:

- **15m present** → execution clock = 15m.
- **Finer present** (future) → use it automatically.
- **None / partial gap** → **daily fallback** for the uncovered span, flagged
  in `daily_fallback_tickers` on the result.

`intraday_coverage()` is wrapped in a try/except so a missing catalog or
DuckDB error degrades to daily-fallback rather than aborting the run.

---

## Components

### `intraday_coverage()` — `backend/algo/backtest/coverage.py`

Single source of truth for execution resolution. Issues **one batched** query
against `stocks.intraday_bars` (never per-ticker).

```python
def intraday_coverage(
    tickers: list[str],
    period_start: date,
    period_end: date,
) -> dict[str, TickerCoverage]:
    ...
```

`TickerCoverage` fields: `finest_interval_sec: int | None`,
`covered_start: date | None`, `covered_end: date | None`, `missing_days: int`.

### `ExecutionSimulator` — `backend/algo/backtest/execution_simulator.py`

Wraps the **live `TrailingStopManager`** (`backend/algo/backtest/trailing_stop_manager.py`).
Parity with live is structural, not coincidental.

Responsibilities:

- Own per-ticker `TrailingStopManager` lifecycle (create on confirmed BUY
  fill; drop on exit).
- On each execution bar: feed LOW-then-HIGH to the trailing manager; evaluate
  hard stop, time stop, and MIS square-off.
- Emit a typed `ExitDecision { exit_reason, trigger_price, phase, hwm }`.
- The runner translates `ExitDecision` → `OrderIntent` → `Fill` → events.

### Runner two-clock wiring

1. Determine execution resolution via `intraday_coverage`.
2. Load **execution bars** at that grain for covered tickers; load **signal
   bars** at signal grain.
3. Build the timeline from execution bars (reusing the existing `is_intraday`
   timeline path).
4. In the loop: gate AST/signal eval to signal bars only; drive
   `ExecutionSimulator` on every execution bar.

Execution bars are lazy-loaded per `(ticker, open-position window)` to bound
15m data volume.

---

## Stop fill model

Stop, trailing-stop, and MIS square-off exits fill at:

```
fill_price = trigger_price × (1 − slip_bps / 10_000)   # SELL
```

using `ALGO_PAPER_SLIPPAGE_BPS` — consistent with the paper trading slippage
model. This models the live GTT (trigger → market order) rather than the
pessimistic bar-low fill used before 0.18.0.

Fees are computed on the **unslipped trigger price**. Entry fills (T+1 open)
are unchanged.

---

## Fee handling: CNC vs MIS

Two-clock exit `OrderIntent`s carry `strategy.product` (CNC → DELIVERY,
MIS → INTRADAY) so `SimBroker` applies the correct STT tier.

Without this, an exec-bar timestamp would cause `SimBroker` to infer INTRADAY
product on CNC exits — producing optimistic P&L (cheaper intraday STT
vs. the correct 0.1% delivery STT on the sell side).

---

## Result metadata

Every `BacktestSummary` (and each walkforward fold) records:

| Field | Meaning |
|---|---|
| `execution_interval_sec` | Execution grain (900 = 15m, 86400 = daily-fallback) |
| `daily_fallback_tickers` | Tickers that had no intraday coverage and ran at daily grain |

Stored in the existing JSON result blob — no schema migration required. Piece C
(transparency UI, deferred) will surface these fields as chips in the frontend.

---

## 1m/5m-cadence strategies: blocked

A strategy whose signal grain is finer than any available execution data (e.g.
a 1m or 5m strategy) cannot be backtested faithfully with current history.

**Policy: block with a clear error:**

> "No 1m/5m history for these tickers; faithful backtest unavailable.
> Run in paper to evaluate this cadence."

Running at 15m with a flag was considered and rejected — too easy to misread as
a real result. Blocking is the honest default; revisit if users need a coarse
approximation.

---

## Backward compatibility

**Two-clock is the default** — no feature flag.

A daily strategy **with trailing disabled** collapses back to the single daily
clock (no intraday exits to evaluate). These runs produce byte-identical
results to pre-0.18.0. The regression test suite verifies this.

| Configuration | Execution clock | Result vs pre-0.18.0 |
|---|---|---|
| Daily signal, trailing disabled | Daily | Byte-identical |
| Daily signal, trailing enabled, no 15m coverage | Daily (flagged) | Byte-identical |
| Daily signal, trailing enabled, 15m coverage | 15m | Intraday exit resolution |
| 15m signal, 15m coverage | 15m | Same grain as before |

---

## Walkforward inheritance

No structural change to `walkforward.py`. Each fold calls `run_backtest`
(the refactored runner) and automatically inherits the two-clock engine.
Each fold's `BacktestSummary` records its own `execution_interval_sec` and
`daily_fallback_tickers`.

---

## Zero network calls in the eval path

Backtest, walkforward, and (when Piece B lands) paper all read only preserved
Iceberg. Coverage gaps degrade/flag gracefully — the engine never issues live
Kite or yfinance calls during evaluation.

---

## What's deferred

- **Piece B** — paper runtime two-clock (separate spec; reuses
  `ExecutionSimulator` and `intraday_coverage`).
- **Piece C** — transparency UI chip surfacing `execution_interval_sec` and
  `daily_fallback_tickers` in the frontend (separate spec).
- New historical 1m/5m backfill (separate offline job; out of band).
- Tick-level simulation (no tick data preserved).
