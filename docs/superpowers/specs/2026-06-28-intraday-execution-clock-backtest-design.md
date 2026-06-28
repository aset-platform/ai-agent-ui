# Intraday Execution Clock for Backtest & Walkforward — Design Spec

- **Date:** 2026-06-28
- **Status:** Draft (Piece A of a 3-piece program)
- **Scope (this spec):** Piece A0 (coverage helper) + Piece A
  (`ExecutionSimulator` + two-clock backtest & walkforward). **Paper is
  Piece B** (separate spec); **transparency UI is Piece C** (separate spec).
- **Branch:** `feature/project-skills` work is unrelated; this lands on a
  fresh `feature/intraday-execution-clock` off `dev`.
- **Related:** `.claude/rules/algo.md`, memory `algo-gtt-trailing-stop`,
  ASETPLTFRM-400 (intraday backtest slices).

---

## 1. Problem

The backtest/walkforward engines evaluate a strategy's stop-loss, ATR
trailing stop, and MIS square-off **at the same grain as the entry
signal**. A *daily* strategy therefore gets exactly **one** trailing/stop
check per day (runner.py:436 timeline loop; trailing eval runner.py:
538–627), using a conservative LOW-then-HIGH approximation of the daily
bar. This:

- **Discards intraday path order** — it cannot know whether the day's HIGH
  ratcheted the trailing stop up *before* the LOW would have hit it, so the
  ATR trail is effectively pessimistic/meaningless for daily strategies.
- **Diverges from live**, where the trailing stop is monitored
  continuously (broker GTT + WS ticks → resampled bars) *throughout the
  session*, independent of signal cadence.

Result: a user has no real assurance a trailing strategy behaves as
intended until it runs live. We want backtest/walkforward to be a faithful
replica of the live runtime, to the resolution the preserved data allows.

## 2. Data reality (investigated 2026-06-28)

`stocks.intraday_bars` (the only historical intraday source):

| Grain (`interval_sec`) | Coverage |
|---|---|
| **15m (900)** | ✅ 497 tickers · 2022-06-01 → 2026-06-25 · ~complete (median 25 bars/day, full NSE session) · median per-ticker depth = full 1008 trading days |
| **5m (300)** | ❌ none |
| **1m (60)** | ❌ none historical (`algo.intraday_bars` holds 6 scratch rows only) |

**Consequence:** for historical backtest/walkforward the finest available
execution grain is **15m**. 1m/5m exist in the schema and may accrue
forward (live capture), so the design selects resolution **from the data**,
not from a hardcoded constant — it auto-upgrades when finer history exists.

**Fidelity ceiling (intrinsic, must be stated honestly):**
backtest ≤ 15m (historical) · paper ≈ 1m (live tick feed) · live = tick.
Backtest cannot match paper/live exactly with current history; it can be
**15m-faithful and explicit about the resolution behind every result.**

## 3. Goals / Non-goals

**Goals**
- Decouple the **signal clock** (strategy cadence) from a faster
  **execution clock** (finest available intraday grain) that drives stops,
  ATR trailing, and MIS square-off.
- Route all three engines (this spec: backtest + walkforward) through **one
  shared `ExecutionSimulator`** that wraps the **same `TrailingStopManager`
  live uses**, so parity is structural, not coincidental.
- Choose execution resolution **per (ticker, window) from preserved data**;
  fall back to daily with a **loud flag** when no intraday is covered.
- Model stop fills at **trigger ± directional slippage** (the live GTT).
- **Zero Kite / network calls in the eval path** — deterministic, offline,
  reads only Iceberg.
- Tag every result with the **execution resolution + coverage** that backed
  it.

**Non-goals (this spec)**
- Paper runtime changes (Piece B).
- Transparency UI / chips (Piece C).
- New historical 1m/5m backfill (separate offline job; out of band).
- Tick-level simulation (no tick data preserved).

## 4. Architecture: two clocks + one simulator

```
                         ┌─────────────────────────────┐
  signal clock  ─────────▶  AST / signal evaluation     │  (entries, rebalances)
  (strategy cadence:      │  fires ONLY on signal bars   │
   daily / 15m / 5m / 1m) └─────────────┬───────────────┘
                                         │ OrderIntent
  execution clock ───────────────────────▼───────────────
  (finest available:      ┌─────────────────────────────┐
   15m today, per-window) │     ExecutionSimulator       │
   replays EVERY exec bar │  • TrailingStopManager (live)│
   between signal points  │  • hard stop / time stop     │
                          │  • MIS square-off            │
                          │  • fill model (trigger±slip) │
                          └─────────────┬───────────────┘
                                        │ Fill
                                        ▼  Portfolio.apply_fill
```

- **Signal clock** = the strategy's declared cadence. Entries and
  rebalances fire **only on signal bars** (e.g. the daily signal bar, or
  each 15m bar for a 15m strategy). Matches live acting on bar close.
- **Execution clock** = the finest available intraday grain for the
  covered window. The runner timeline becomes the **execution** timeline;
  exits/trailing/square-off are evaluated on **every** execution bar.
- For a **daily** strategy with 15m data: 1 signal/day, **25 exit checks/
  day**. For a **15m** strategy: signal and execution both 15m (execution
  can't go finer than the data). For **1m/5m** strategies: see §8.

### Signal vs execution bar mapping
- ATR and all indicators are computed at **signal grain** (matches live's
  factor cache). The execution clock does **not** recompute indicators.
- The signal bar for a daily strategy is represented by the day's **last
  execution bar** (the close decision); entries fill on the next available
  execution bar's open, preserving "act on close, fill next" live behavior.
  (Exact entry-fill bar selection finalized in the plan; default: same
  pattern the daily engine uses today, mapped onto execution bars.)

## 5. Components

### A0 — `intraday_coverage` helper (foundational, build first)
Single source of truth for "what resolution can we evaluate at," used by
the engine now and by Piece C's UI later.

```
def intraday_coverage(
    tickers: list[str],
    period_start: date,
    period_end: date,
) -> dict[str, TickerCoverage]
```
`TickerCoverage` = `{ finest_interval_sec: int | None,
covered_start: date | None, covered_end: date | None,
missing_days: int }`. Implementation: **one batched** query against
`stocks.intraday_bars` grouped by `ticker, interval_sec` over the window
(§4.1 batch reads — never per-ticker). `finest_interval_sec` = smallest
`interval_sec` present for that ticker in-window (today always 900 or
None). Returns `None` finest when no intraday rows exist → daily fallback.
No Kite.

### A — `ExecutionSimulator`
Extracts the exit/trailing logic currently inlined in runner.py:538–627
into one component that backtest, walkforward (free, via runner), and later
paper all drive.

Responsibilities:
- Own per-ticker `TrailingStopManager` lifecycle (create on confirmed BUY
  fill; drop on exit) — **the same class live uses** (`backend/algo/
  backtest/trailing_stop_manager.py`).
- On each execution bar: feed LOW-then-HIGH to the trailing manager;
  evaluate hard %-stop, time stop, and MIS square-off.
- Emit a typed `ExitDecision { exit_reason, trigger_price, phase, hwm }`
  rather than directly building events (keeps it pure/testable).
- Fill via the existing executor (`sim.execute`) using the **trigger fill
  model** (§7).

The runner translates `ExitDecision` → `OrderIntent` → `Fill` → events,
exactly as today, but the *decision* logic lives in one place. Live's
runtime can adopt the same component in a later refactor (out of scope but
the interface is designed to allow it).

### Runner refactor (backtest)
1. Determine execution resolution via `intraday_coverage` (§6).
2. Load **execution bars** at that grain (`load_intraday_bars_window`,
   data_source.py:170) for covered tickers; load **signal bars** at signal
   grain (daily via `load_ohlcv_window`, or same intraday series).
3. Build the timeline from **execution** bars (reuse the existing
   `is_intraday` timeline path, runner.py:365–388).
4. In the loop: gate AST/signal eval to **signal bars only**; drive
   `ExecutionSimulator` on **every** execution bar.
5. Lazy-load execution bars only for `(ticker, date-range)` while a
   position can be open, to bound 15m volume (§9).

### Walkforward
No structural change — `walk_windows` + per-fold `run_backtest`
(walkforward.py) inherit the two-clock engine automatically since each fold
calls the refactored runner. Each fold records its execution resolution.

## 6. Resolution selection policy (data-driven)

Per (ticker, window), pick `finest_interval_sec` from `intraday_coverage`:
- **15m present** → execution clock = 15m.
- **finer present** (future) → use it automatically.
- **none / partial gap** → **daily fallback for the uncovered span**,
  flagged `resolution="daily_fallback"` with low-assurance marker.

Execution grain is always **≤ signal grain**; if a strategy's signal grain
is finer than any available execution data, see §8. No Kite gap-fill ever.

## 7. Fill model

Stop / trailing / square-off exits fill at **trigger price ± directional
slippage**, reusing `ALGO_PAPER_SLIPPAGE_BPS` (consistent with the shipped
paper slippage model): a sell-stop fills at `current_stop × (1 −
slip_bps/10_000)`, fees computed on the unslipped trigger. This models the
live GTT (trigger → order) rather than the pessimistic bar-low fill used
today. Entry fills are unchanged. A `pessimistic_fill` override is **out of
scope** (deferred; can be added later).

## 8. 1m / 5m-cadence strategies (no historical data)

A strategy whose **signal grain is finer than any available execution
data** (e.g. a 1m strategy, with only 15m history) cannot be backtested
faithfully. Policy:
- **Block** the backtest with a clear error: *"No 1m/5m history for these
  tickers; faithful backtest unavailable. Run in paper (Piece B) to
  evaluate this cadence."* — rather than silently running at the wrong
  grain.
- (Alternative considered and rejected: run at 15m with a flag — too easy
  to misread as a real result. Blocking is the honest default; revisit if
  users need a coarse approximation.)

## 9. Performance

- 15m = ~25 bars/day/ticker; a multi-year backtest over a few tickers is
  well within reach, but **lazy load** execution bars per `(ticker, open
  window)` rather than the whole period × universe (§4.1 batch reads, §4.1
  no full scans). One batched `WHERE ticker IN (...) AND interval_sec=900
  AND year_month BETWEEN ...` per load.
- Reuse the existing `bars_by_ts` O(1) lookup (runner.py:376).
- No new Iceberg table; reads only. No maintenance-enrollment changes.

## 10. Result metadata (tagging)

Each `algo.runs` result (and each walkforward fold) records:
`execution_interval_sec`, `coverage_pct` (covered bars / expected),
`daily_fallback_tickers: list[str]`. Stored in the run payload so Piece C
can surface it. No schema migration if stored in the existing JSON result
blob; confirm in plan.

## 11. Error handling

- Missing execution bars for an open position on a given exec bar → skip
  that bar's exit check for that ticker (carry stop state forward), same as
  today's `None`-bar guard (runner.py:548). Log at DEBUG.
- `intraday_coverage` empty for all tickers → whole run is daily-fallback,
  flagged; do not error.
- Iceberg read errors propagate (no silencing — §4.3).

## 12. Testing

- **A0**: unit tests for `intraday_coverage` against a seeded fixture
  (covered ticker, uncovered ticker, partial-gap ticker).
- **ExecutionSimulator**: pure unit tests — given a synthetic 15m bar
  series and an open position, assert trailing ratchet + stop-hit + fill
  price (trigger±slip) at the right bar; assert path-order correctness
  (HIGH-ratchet-before-LOW case that the daily engine gets wrong).
- **Runner two-clock**: daily strategy + 15m execution → assert exits fire
  intraday, not at daily close; assert signal still fires once/day.
- **Regression**: existing daily backtests with trailing disabled produce
  identical results (execution clock = signal clock when no intraday/no
  trailing).
- **Walkforward**: a fold over a covered window runs at 15m; a fold over an
  uncovered window flags daily-fallback.
- **Parity anchor**: same TrailingStopManager fed the same 15m series
  yields the same exits the live manager would (shared class).
- Happy path + ≥1 error path each (§26).

## 13. Rollout / backward compatibility

- **Two-clock is the default** — no feature flag. The execution clock is
  always the finest available grain for the covered window.
- Behavior is preserved where it should be: a daily strategy **with
  trailing disabled** runs exactly as today because the execution clock
  collapses to the signal clock (no intraday exits to evaluate) — so there
  is nothing to gate. The two-clock path simply adds intraday exit
  resolution when trailing/square-off/intraday exits are in play and
  coverage exists.
- Safety comes from the regression tests (§12), not a flag: trailing-
  disabled daily runs must be byte-identical pre/post.

## 14. Open questions (resolve in plan)

1. Exact entry-fill bar mapping for a daily signal on a 15m execution
   timeline (last-bar-of-day close → next-day-first-bar open?).
2. Whether result metadata fits the existing run JSON blob or needs a
   column (prefer blob; no migration).
3. MIS square-off already has intraday logic (runner.py:390–426) — confirm
   it composes cleanly with the extracted simulator vs the daily path.

## 15. Sequence after this spec

A0 → A (backtest) → walkforward verification → **then Piece B (paper)** as
its own spec, reusing `ExecutionSimulator` and `intraday_coverage`.
