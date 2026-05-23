# Backtest Stop-Loss Enforcement Framework Fix — Design

| | |
|---|---|
| Date | 2026-05-23 |
| Author | Abhay Kumar Singh |
| Status | Draft (design only — no code) |
| Scope | Add per-bar `stop_loss_pct` enforcement to the backtest runner. Paper + live runtimes are out of scope — they delegate to Kite's bracket-order API. |
| Non-goals | Short-side stops, trailing stops, time-based / N-day timeouts, take-profit, Kite bracket-order emission, paper / live local enforcement |
| Predecessor | `docs/research/2026-05-23-rsi2-connors-stop3-final.md` — diagnosed the framework gap when the canonical Connors §6.4 G4 tune produced identical results before and after a `stop_loss_pct` template change |

## 1. Motivation

### 1.1 The framework gap

Today's `backend/algo/paper/risk_engine.py::RiskEngine.gate()` is the shared order-time gate used by backtest, paper, and live runners. It correctly enforces 5 of 6 risk fields declared on the AST:

| Field | Enforced today | How |
|---|:---:|---|
| `max_qty` | ✅ | gate() rejects oversized orders |
| `max_loss_pct` (daily) | ✅ | gate() daily kill switch |
| `max_open_positions` | ✅ | gate() blocks new entries past cap |
| `max_concentration_pct` | ✅ | gate() blocks single-name over X% |
| `max_exposure_pct` | ✅ | gate() may scale orders down |
| **`stop_loss_pct`** | **❌** | **NOT enforced anywhere** |

`stop_loss_pct` is declared in 11 existing strategy templates (v4 daily at 4%, MIS v1 at 2%, RSI(2) v1 at 3%, etc.) but no codepath consumes it. The field is documentation-only.

This was discovered during the RSI(2) Connors v1 backtest (`docs/research/2026-05-23-rsi2-connors-stop3-final.md`): applying spec §6.4's "tighten stop_loss_pct from 5% to 3%" G4-fail tune produced **byte-identical results** to the un-tuned run. Investigation traced the gap to `RiskEngine.gate()`, which is correctly scoped to order-time gating but has no per-bar position-monitor lifecycle.

### 1.2 Why this is a `gate()` design boundary, not a `gate()` bug

`gate()` sees **proposed signals** (new orders the strategy wants to place) and decides accept / reject / scale. A stop-loss is **not a gate** on a proposed order — it's a per-bar monitor that emits NEW exit signals when a held position crosses its loss threshold. Different lifecycle, different code path. Adding stop enforcement to `gate()` would muddle its responsibility; the correct fix is a new sibling module.

### 1.3 What this fix unlocks

Once stop-loss is enforced in backtest:

- **RSI(2) Connors v1 can pass G4** (the only failing gate after ex-DIACABS sanity probe) and move to paper trading per its own promotion spec
- **MIS v1's reported −18% return becomes diagnosable** — the rerun with stop_loss enforcement may show smaller losses, validating whether the strategy's underlying signal is dead vs whether it was just bleeding on unmanaged stops
- **v4 daily (53.6% baseline) gets honest drawdown numbers** — currently its reported DD is the upper bound; with stops active it should compress
- **Every future strategy template** stops being silently un-risk-managed in backtest

Paper and live runtimes already rely on Kite bracket orders for stop-loss enforcement, so no change there.

## 2. Scope decisions (locked during brainstorm)

| Question | Decision | Why |
|---|---|---|
| Field scope | `stop_loss_pct` only | Audit confirmed 5 of 6 fields work today. Only this one missing. |
| Runtime scope | Backtest only | Paper + live use Kite bracket-order API for stops. Documented convention; no local enforcement needed there. |
| Stop semantics | Next-bar-open exit | Conservative; uses only OHLC we already have; matches Backtrader / Zipline convention. Triggers on close-on-close loss vs avg_price, fills at next bar's open. |
| Long vs short | Long-only | AST framework is currently long-only (`SetTargetWeightNode.weight: Field(ge=0, le=1)`). Short-side stops queued for the same spec that adds short-side AST support. |
| Trailing stops | Out of scope | v2 enhancement; static `stop_loss_pct` first, trailing later if needed. |

## 3. Architecture

### 3.1 What changes

```
backend/algo/backtest/
├── stop_loss_monitor.py            ← NEW: per-bar exit-signal emitter
└── runner.py                        — modify per-bar loop: call monitor BEFORE AST eval

backend/algo/backtest/tests/
├── test_stop_loss_monitor.py        ← NEW (unit tests, ~8)
├── test_stop_loss_integration.py    ← NEW (runner integration, ~4)
└── test_existing_strategies_smoke.py ← NEW (parametrized over 11 templates)
```

Plus docs:

```
docs/superpowers/specs/
└── 2026-05-23-stop-loss-enforcement-design.md   ← this spec
```

### 3.2 What stays untouched

- **`RiskEngine.gate()`** in `backend/algo/paper/risk_engine.py` — already correctly handles the other 5 risk fields. Stop-loss is a different lifecycle.
- **AST schema** — `stop_loss_pct: float = Field(ge=0, le=50)` already exists on `RiskPerTrade`. No new field, no migration.
- **Existing strategy templates** — all 11 already declare `stop_loss_pct`. Their enforcement just becomes real.
- **Paper runtime** (`backend/algo/paper/runtime.py`) — Kite bracket orders handle stops at the broker.
- **Live runtime** (`backend/algo/live/runtime.py`) — same.
- **`algo.events.exit_reason` enum** — `"stop_loss"` already exists per `backend/algo/backtest/types.py:146`.
- **PositionTracker** (`backend/algo/backtest/positions.py`) — `avg_price` weighted-cost-basis field already tracked. The monitor reads it.
- **Promotion workflow** — no change.

### 3.3 Where the monitor lives at runtime

The runner calls the monitor **before AST evaluation** per bar:

```
for each bar in backtest:
    1. NEW: stop_loss_monitor.check_stop_loss_triggers(...)
       → for each triggered ticker, emit exit signal + add to skip_set
    2. EXISTING: for ticker not in skip_set:
         AST evaluator → propose signals → RiskEngine.gate() → fills
    3. EXISTING: bar closes; positions updated; metrics aggregated
```

Precedence rationale: a stop-out is a hard risk event. The AST shouldn't get a chance to re-enter or modify a position on the same bar where the stop fires.

## 4. Module interface

### 4.1 `backend/algo/backtest/stop_loss_monitor.py`

```python
"""Stop-loss monitor — per-bar exit-signal emitter.

Reads open positions from PositionTracker. At each bar, for each
open position whose current close has dropped more than
``stop_loss_pct`` below the position's average cost, emits an exit
signal that fires at the NEXT bar's open.

Long-only v1. Short-side positions are out of scope (the AST
framework is currently long-only).

Pure function. No I/O. Idempotent within a bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class StopLossTrigger:
    ticker: str
    avg_price: Decimal
    current_close: Decimal
    loss_pct: Decimal       # negative number, e.g. Decimal("-4.5")
    stop_loss_pct: Decimal  # the threshold that fired, e.g. Decimal("3.0")


def check_stop_loss_triggers(
    *,
    open_positions: dict[str, dict],   # ticker → {avg_price, qty, ...}
    current_closes: dict[str, Decimal], # ticker → close at this bar
    stop_loss_pct: float,              # from strategy.risk.per_trade.stop_loss_pct
) -> list[StopLossTrigger]:
    """Return triggers for positions whose loss exceeds the threshold.

    For each open long position:
        loss_pct = (close - avg_price) / avg_price * 100
        if loss_pct <= -stop_loss_pct  →  trigger

    Returns empty list if stop_loss_pct == 0 (feature disabled).
    Returns empty list for tickers with no current close (data gap;
    safer to skip the trigger than to fabricate one).
    """
```

That's the full public surface — one dataclass, one pure function. ~50 lines including docstring + body.

### 4.2 Trigger formula

For each open long position with `avg_price > 0` and `current_close` present:

```
loss_pct = (current_close - avg_price) / avg_price * 100
trigger if loss_pct <= -stop_loss_pct
```

Concrete: with `stop_loss_pct=3.0`, `avg_price=100`, position triggers when `current_close <= 97.0`. Uses **close-on-close** loss measurement (not intra-bar low) per the locked "next-bar-open exit" semantics.

Defensive guards:
- `avg_price == 0`: no trigger (shouldn't happen for an open position)
- `stop_loss_pct == 0`: feature disabled, no triggers ever
- ticker in `open_positions` but absent from `current_closes`: skip (data gap; don't fabricate)

### 4.3 Integration in `runner.py`

Conceptual insertion in the per-bar loop:

```python
# NEW: before AST eval, emit stop-loss exits for breaching positions.
stop_triggers = check_stop_loss_triggers(
    open_positions=positions.snapshot(),
    current_closes={t: bar_close_for(t) for t in tickers},
    stop_loss_pct=strategy.risk.per_trade.stop_loss_pct,
)
skip_tickers: set[str] = set()
for trig in stop_triggers:
    exit_signals.append(make_exit_signal(
        ticker=trig.ticker,
        reason="stop_loss",
        fill_at_next_bar_open=True,
    ))
    skip_tickers.add(trig.ticker)

# EXISTING: per-ticker AST eval (skipping tickers in skip_tickers)
for ticker in tickers - skip_tickers:
    ... existing flow ...
```

Per-ticker precedence on bar N:
- **Stop triggered**: emit exit (reason="stop_loss"), AST skipped for this ticker on bar N
- **Bar N+1**: fill at open price, position closed
- **Bar N+2**: AST evaluates normally; ticker is flat so it checks entry conditions

### 4.4 Multi-lot positions

`PositionTracker` already maintains weighted-average cost basis (`avg_price` field on `PositionState`; verified by reading `positions.py`: `new_avg = (existing.avg_price * existing.qty + fill.fill_price * fill.qty) / total_qty`). The stop-loss monitor reads `avg_price` directly — no special handling for scale-ins.

### 4.5 Exit-signal shape

```python
ExitSignal(
    ticker=trig.ticker,
    exit_reason="stop_loss",   # already a documented exit_reason value
                                # (backend/algo/backtest/types.py:146)
    fill_at="next_bar_open",
    ...existing fill metadata...
)
```

`"stop_loss"` is already in the documented `exit_reason` enum. The triage script's win-rate formula (`scripts/run_rsi2_connors_baseline.py`, mirrored in `scripts/run_mis_mr_v1_baseline.py`) already excludes `exit_reason == "stop_loss"` from the denominator — exactly the right behavior, since stop-outs are risk events, not signal-driven exits.

## 5. Test plan

### 5.1 Three layers of coverage

| Layer | Tests | Catches |
|---|:---:|---|
| Unit — pure function | ~8 | Math correctness; threshold boundary; missing data; disabled feature; multi-lot avg-cost |
| Integration — runner per-bar | ~4 | Precedence; AST skip-list correctness; `exit_reason="stop_loss"` lands; fill at next-bar-open |
| Regression smoke — all templates | 1 parametrized over 11 templates | All existing templates still parse + produce a non-empty `BacktestSummary` with stop-loss now active |

Total ~13 tests. Runtime under 90s combined.

### 5.2 Unit tests (`test_stop_loss_monitor.py`)

| # | Test name | Asserts |
|---|---|---|
| 1 | `test_trigger_when_loss_exceeds_threshold` | avg=100, close=96, stop=3.0 → trigger emitted, loss_pct=-4.0 |
| 2 | `test_no_trigger_when_loss_below_threshold` | avg=100, close=98, stop=3.0 → empty list |
| 3 | `test_trigger_at_exact_boundary_is_inclusive` | avg=100, close=97, stop=3.0 → trigger (using `<=` semantics) |
| 4 | `test_no_trigger_when_position_gains` | avg=100, close=105, stop=3.0 → empty list |
| 5 | `test_disabled_when_stop_loss_pct_zero` | stop=0.0 → empty list regardless of position state |
| 6 | `test_skip_ticker_with_no_current_close` | open_positions has X but current_closes doesn't → empty (no fabrication) |
| 7 | `test_skip_position_with_zero_avg_price` | avg=0 (defensive guard) → empty |
| 8 | `test_multi_position_independence` | 3 tickers, only 1 breaches → exactly 1 trigger, correct ticker |

All tests use hand-built dicts. No fixtures, no I/O. Total runtime < 0.1s.

### 5.3 Integration tests (`test_stop_loss_integration.py`)

| # | Test name | Asserts |
|---|---|---|
| 1 | `test_stop_loss_emits_exit_signal_in_runner` | 10-bar fixture: position opens at bar 2 (price 100), price drops 4% by bar 5 with stop=3.0 → exit signal at bar 5 close, fills at bar 6 open |
| 2 | `test_stop_loss_skips_ast_for_stopped_ticker_same_bar` | Confirm AST is NOT evaluated for the stopped ticker on the stop bar (spy/counter on the AST evaluator) |
| 3 | `test_stop_loss_exit_lands_with_correct_exit_reason` | After fill: recorded event has `exit_reason="stop_loss"`, distinguishable from `"signal"` |
| 4 | `test_no_stop_loss_when_pct_zero` | Strategy with `stop_loss_pct=0`: same fixture produces NO stop events even on a -10% bar |

Use the existing `BacktestRequest` + `run_backtest()` plumbing with hand-built bar data — same pattern as existing intraday/daily backtest tests (e.g. `backend/algo/backtest/tests/test_runner.py` or sibling files).

### 5.4 Regression smoke (`test_existing_strategies_smoke.py`)

```python
@pytest.mark.parametrize("template", [
    "bull_momentum_daily_swing_v4",   # the 53.6% v4 baseline
    "rsi2_connors_daily_v1",          # this session's strategy
    "regime_sideways_meanrev_quality_v2",
    "regime_bull_momentum",
    "regime_bear_defensive_lowvol",
    "mis_intraday_meanrev_long_v1",   # MIS v1 from PR #230
    "sector_rotation_monthly",
    "bull_momentum_15m_swing",
    "bull_momentum_daily_swing",      # legacy v1
    "bull_momentum_daily_swing_v3",   # legacy v3
    "regime_sideways_meanrev_quality", # legacy
])
def test_template_runs_short_backtest_without_crashing(template):
    """All 11 templates parse, run a 30-day micro-backtest on 3
    tickers without crashing, and emit a BacktestSummary with at
    least the expected fields (final_nav_inr, trade_list).

    Regression guard — does NOT assert on metric values.
    """
```

Single parametrized test, 11 cases. Each runs a tiny 30-day × 3-ticker backtest (RELIANCE.NS, HDFCBANK.NS, INFY.NS) — completes in < 60s total. Asserts only that the runner doesn't crash and produces a populated `BacktestSummary`. Numerical changes are EXPECTED and documented in the PR description, not gated as test assertions.

### 5.5 Expected metric changes on existing strategies (informational, NOT test assertions)

The implementation plan should re-run two strategies after the framework lands and capture before/after triage tables. This is documentation, not test gating:

**v4 daily (`bull_momentum_daily_swing_v4`, stop_loss_pct=4.0):**

| | Before (no enforcement) | After (4% stop active) |
|---|---:|---:|
| Win rate | 53.6% | TBD by re-run |
| Max DD | TBD | TBD (likely lower) |
| Trade count | TBD | TBD (likely higher — stops add exits) |
| CAGR | TBD | TBD |

**RSI(2) Connors v1 ex-DIACABS (stop_loss_pct=3.0):**

| | Before (no enforcement, ex-DIACABS) | After (3% stop active, ex-DIACABS) |
|---|---:|---:|
| G3 win rate | 62.85% | TBD |
| G4 max DD | -19.89% | **TBD — must come under 15% to unblock paper promotion** |
| G2 CAGR | 8.44% | TBD |

These re-runs go in the PR description. If the RSI(2) post-fix run lands G4 under 15%, the strategy graduates to paper per its own promotion spec — closing the loop on the user-facing motivation for this framework fix.

### 5.6 What we deliberately DON'T test

- Specific metric values for any pre-existing strategy (their numbers will change; that's the bugfix)
- Live runtime / Kite bracket-order interaction (out of scope per Section 2's runtime decision)
- Stop-loss with `take_profit_pct` (no such field in the AST schema)
- Performance regression (overhead is one dict-lookup + one Decimal divide per open position per bar; imperceptible)

## 6. Promotion & rollout

### 6.1 Promotion path

No strategy-promotion lifecycle changes. This is a framework fix; the framework ships when the PR merges to `dev`. Existing strategies that have `stop_loss_pct > 0` immediately start having stops enforced in subsequent backtests.

### 6.2 Backwards-compatibility

Fully backwards-compatible:

- Strategies with `stop_loss_pct: 0` (or not set, since `Field(ge=0)` allows zero) get no enforcement (`check_stop_loss_triggers` returns `[]`).
- Strategies with `stop_loss_pct > 0` (all 11 existing templates) get stops enforced. Their next backtest produces different (more honest) metrics.
- Stored `algo.runs` / `algo.events` rows from past backtests are unaffected — they don't get re-run.
- The PR description should explicitly list the 11 affected templates and recommend a one-time re-baseline of any that are gated for promotion (today: RSI(2) v1; future: any new strategy).

### 6.3 Communication to operators (if applicable)

A short note in the strategy templates README:

> Backtests after 2026-05-XX include per-bar `stop_loss_pct` enforcement at next-bar-open semantics. Re-baseline any strategy whose backtest result drove a promotion decision before that date.

## 7. Non-goals (explicit out-of-scope)

- Short-side stop-loss (no shorts in framework yet)
- Trailing stops (static stop first)
- Time-based / N-day timeouts (separate AST-level concern)
- Take-profit enforcement (no `take_profit_pct` field)
- Bracket-order emission to Kite (paper + live trust broker brackets)
- Paper / live runtime local enforcement (broker handles it)
- Refactoring `RiskEngine.gate()` (correctly scoped to order-time gating)
- Adding new `exit_reason` values (`"stop_loss"` already exists)
- AST schema changes (`stop_loss_pct` already declared)

## 8. References

- RSI(2) Connors v1 final-tune triage: `docs/research/2026-05-23-rsi2-connors-stop3-final.md` (the report that diagnosed the framework gap)
- RSI(2) Connors v1 spec + plan: `docs/superpowers/specs/2026-05-22-rsi2-connors-daily-design.md`, `docs/superpowers/plans/2026-05-22-rsi2-connors-daily.md`
- MIS v1 spec + PR #230 (will also benefit from honest stop-loss in re-runs)
- v4 daily baseline template: `backend/algo/strategy/templates/bull_momentum_daily_swing_v4.json`
- Existing risk engine: `backend/algo/paper/risk_engine.py::RiskEngine.gate()`
- Position tracker: `backend/algo/backtest/positions.py::PositionTracker` (provides `avg_price`)
- AST risk schema: `backend/algo/strategy/ast.py::RiskPerTrade` (where `stop_loss_pct` lives)
- Project memory: `feedback_runtime_feature_three_runtimes` — applicable principle ("wire features through ALL runtimes"); here only backtest needs the fix because paper/live use broker-side stops
