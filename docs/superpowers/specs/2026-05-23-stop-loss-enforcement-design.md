# Stop-Loss Enforcement Framework Fix — Design

| | |
|---|---|
| Date | 2026-05-23 (revised after deeper audit) |
| Author | Abhay Kumar Singh |
| Status | Draft (design only — no code) |
| Scope | Add per-bar `stop_loss_pct` enforcement to ALL THREE runtimes (backtest + paper + live). Each runtime reuses the same pure `stop_loss_monitor` module; runtime-specific glue translates triggers into exit orders with cadence-appropriate fill semantics. |
| Non-goals | Short-side stops, trailing stops, time-based / N-day timeouts, take-profit, Kite bracket-order emission (deferred to v3 per `kite_client.py`) |
| Predecessor | `docs/research/2026-05-23-rsi2-connors-stop3-final.md` — diagnosed the framework gap when the canonical Connors §6.4 G4 tune produced identical results before and after a `stop_loss_pct` template change |

## Scope correction note

An initial draft of this spec scoped the fix to **backtest only** on the assumption that paper + live runtimes delegated stop-loss enforcement to Kite's bracket-order API. Subsequent audit found that assumption is **wrong**:

- `backend/algo/broker/kite_client.py` explicitly rejects bracket orders at the SDK boundary (`"SL/SLM/BO/CO are deferred to v3"`). Live orders carry no broker-side stop.
- `backend/algo/paper/runtime.py` has zero `stop_loss` / `bracket` / `monitor` references — no local enforcement either.
- Paper and live runtimes share the same `PositionTracker` and `_on_bar_close` lifecycle pattern as backtest — the integration shape is identical, just with different fill semantics.

The revised scope below covers all three runtimes.

## 1. Motivation

### 1.1 The framework gap

Today's `backend/algo/paper/risk_engine.py::RiskEngine.gate()` is the shared order-time gate used by all three runners. It correctly enforces 5 of 6 risk fields declared on the AST:

| Field | Enforced today | How |
|---|:---:|---|
| `max_qty` | ✅ | gate() rejects oversized orders |
| `max_loss_pct` (daily) | ✅ | gate() daily kill switch |
| `max_open_positions` | ✅ | gate() blocks new entries past cap |
| `max_concentration_pct` | ✅ | gate() blocks single-name over X% |
| `max_exposure_pct` | ✅ | gate() may scale orders down |
| **`stop_loss_pct`** | **❌** | **NOT enforced anywhere — in any runtime** |

`stop_loss_pct` is declared in 11 strategy templates (v4 daily at 4%, MIS v1 at 2%, RSI(2) v1 at 3%, etc.) but no codepath consumes it. The field is documentation-only across backtest, paper, and live.

Discovered during the RSI(2) Connors v1 backtest (`docs/research/2026-05-23-rsi2-connors-stop3-final.md`): applying spec §6.4's "tighten stop_loss_pct from 5% to 3%" G4-fail tune produced byte-identical results to the un-tuned run.

### 1.2 Why this is a `gate()` design boundary

`gate()` sees **proposed signals** and decides accept / reject / scale at order-time. A stop-loss is **not a gate** on a proposed order — it's a per-bar monitor that emits NEW exit signals when a held position crosses its loss threshold. Different lifecycle, different code path. Adding stop enforcement to `gate()` would muddle its responsibility; the correct fix is a new sibling module.

### 1.3 What this fix unlocks

Once stop-loss is enforced across all three runtimes:

- **RSI(2) Connors v1 can pass G4** (the only failing gate after ex-DIACABS sanity probe) and move to paper trading
- **MIS v1's −18% return becomes diagnosable** — re-run with stop enforcement may show smaller losses, distinguishing strategy-dead vs unmanaged-bleed
- **v4 daily (53.6% baseline) gets honest drawdown numbers**
- **Paper-stage validation (P1-P5 gates) measures the real strategy** — currently paper P&L could diverge from backtest because both lack stops, but discrepancies between simulators and real broker behavior would have hidden it
- **Live trading is materially safer** — strategy-defined stops are honored at the runtime layer until Kite bracket orders land in v3

## 2. Scope decisions (locked during brainstorm + audit)

| Question | Decision | Why |
|---|---|---|
| Field scope | `stop_loss_pct` only | Audit confirmed 5 of 6 fields work today. Only this one missing. |
| Runtime scope | **All 3 runtimes** (backtest + paper + live) | Audit found paper has no local enforcement and live's Kite client rejects bracket orders — all three need the same fix. |
| Fill semantics — backtest | Next-bar-open | Conservative; uses OHLC only; matches Backtrader / Zipline; consistent with existing fill model |
| Fill semantics — paper | Next-bar-open | Same as backtest for parity; paper is the backtest fill model running on live data feed |
| Fill semantics — live | Immediate MARKET sell at trigger time | Real-time; can't wait for "next bar" — the broker will not be there |
| Long vs short | Long-only | AST framework is currently long-only |
| Trailing stops | Out of scope | Static stop first; trailing is a v2 |

## 3. Architecture

### 3.1 What changes

```
backend/algo/backtest/
├── stop_loss_monitor.py            ← NEW: pure trigger-detection function
│                                     (shared by all 3 runtimes)
├── runner.py                        — modify per-bar loop: call monitor BEFORE AST eval
├── types.py                         — add exit_reason: str = "signal" field
│                                     to OrderIntent + Fill + Position
└── positions.py                     — _apply_sell stamps fill.exit_reason on close

backend/algo/paper/
└── runtime.py                       — same per-bar integration as backtest;
                                       monitor in _on_bar_close before AST eval

backend/algo/live/
└── runtime.py                       — monitor in _on_bar_close; emit MARKET SELL
                                       OrderIntent at trigger time (not next-bar-open)

backend/algo/backtest/tests/
├── test_stop_loss_monitor.py        ← NEW: 8 unit tests on the pure function
├── test_stop_loss_integration.py    ← NEW: 4 backtest integration tests
└── test_existing_strategies_smoke.py ← NEW: 11-template parametrized smoke

backend/algo/paper/tests/
└── test_stop_loss_paper_integration.py ← NEW: 2-3 paper integration tests

backend/algo/live/tests/
└── test_stop_loss_live_integration.py  ← NEW: 2-3 live integration tests (mocked Kite)
```

Plus docs:

```
docs/superpowers/specs/
└── 2026-05-23-stop-loss-enforcement-design.md   ← this spec

docs/research/
└── 2026-05-23-stop-loss-enforcement-impact.md    ← before/after triage report
```

### 3.2 What stays untouched

- **`RiskEngine.gate()`** — already correctly handles the other 5 risk fields. Stop-loss is a different lifecycle.
- **AST schema** — `stop_loss_pct: float = Field(ge=0, le=50)` already exists. No new field, no migration.
- **Existing strategy templates** — all 11 already declare `stop_loss_pct`. Enforcement was the missing piece.
- **`algo.events.exit_reason` enum** — `"stop_loss"` already exists per `backend/algo/backtest/types.py:146`.
- **PositionTracker** — `avg_price` weighted-cost-basis field already tracked; monitor reads it.
- **Promotion workflow** — no change.

### 3.3 Per-runtime per-bar lifecycle

All three runtimes share the same conceptual sequence per bar:

```
1. Bar closes — new close values available
2. closes_by_ticker populated for this bar
3. NEW: stop_loss_monitor.check_stop_loss_triggers(...) runs HERE
4. For each trigger: emit SELL OrderIntent tagged exit_reason="stop_loss"
   - Backtest + Paper: fills at NEXT bar's open via SimBroker
   - Live: fills immediately at MARKET via Kite at trigger time
5. Stopped tickers skip AST evaluation on this bar
6. AST eval per remaining ticker → propose signals → RiskEngine.gate filters
7. Fills processed; PositionTracker.apply_fill records with correct exit_reason
```

Steps 1-3 + 5-7 are identical across runtimes. Step 4 differs in WHEN the fill lands (next-bar-open simulator vs immediate broker round-trip).

## 4. Module interface

### 4.1 `backend/algo/backtest/stop_loss_monitor.py`

```python
"""Stop-loss monitor — per-bar exit-trigger detector.

Pure function. Shared by backtest + paper + live runtimes. Each
runtime translates triggers into runtime-appropriate exit orders.

Long-only v1. Short-side positions are out of scope (the AST
framework is currently long-only).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class StopLossTrigger:
    """One stopped-out position. Runtimes translate to SELL orders."""

    ticker: str
    avg_price: Decimal
    current_close: Decimal
    loss_pct: Decimal       # negative, e.g. Decimal("-4.5")
    stop_loss_pct: Decimal  # threshold that fired


def check_stop_loss_triggers(
    *,
    open_positions: dict[str, dict],   # ticker → {"qty", "avg_price"}
    current_closes: dict[str, Decimal],
    stop_loss_pct: float,
) -> list[StopLossTrigger]:
    """Return triggers for positions whose loss exceeds the threshold.

    For each open long position with avg_price > 0 and a
    current_close available::

        loss_pct = (current_close - avg_price) / avg_price * 100
        trigger if loss_pct <= -stop_loss_pct

    Returns empty list if stop_loss_pct == 0 (feature disabled)
    or no positions breach. Skips tickers with missing closes
    (data gap; don't fabricate triggers).
    """
```

That's the full public surface — one dataclass, one pure function. ~70 LOC.

### 4.2 Trigger formula

For each open long position with `avg_price > 0` and `current_close` present:

```
loss_pct = (current_close - avg_price) / avg_price * 100
trigger if loss_pct <= -stop_loss_pct
```

Concrete: `stop_loss_pct=3.0`, `avg_price=100`, position triggers when `current_close <= 97.0`. Close-on-close loss (not intra-bar low) per the locked fill semantics for backtest/paper. Live uses the same trigger formula but acts immediately on the trigger.

Defensive guards:
- `avg_price == 0`: no trigger
- `stop_loss_pct == 0`: feature disabled
- ticker in `open_positions` but absent from `current_closes`: skip (no fabrication)

### 4.3 Backtest runner integration

In `backend/algo/backtest/runner.py`'s per-bar loop, insert BEFORE the per-ticker AST eval:

```python
open_pos = pt.open_positions()
open_pos_dicts = {
    t: {"qty": p.qty, "avg_price": p.avg_price}
    for t, p in open_pos.items()
}
stop_triggers = check_stop_loss_triggers(
    open_positions=open_pos_dicts,
    current_closes=closes_by_ticker,
    stop_loss_pct=float(strategy.risk.per_trade.stop_loss_pct),
)
skip_tickers: set[str] = set()
for trig in stop_triggers:
    intent = OrderIntent(
        ticker=trig.ticker,
        side="SELL",
        qty=open_pos[trig.ticker].qty,
        intent_emitted_at=current_date,
        intent_emitted_ts_ns=current_ts_ns,
        exit_reason="stop_loss",
    )
    broker.submit(intent)   # standard SimBroker fill at next-bar-open
    skip_tickers.add(trig.ticker)

# AST eval skips stopped tickers.
for ticker in tickers - skip_tickers:
    ... existing flow ...
```

### 4.4 Paper runtime integration

`backend/algo/paper/runtime.py::_on_bar_close()` already iterates per-ticker after each bar close. Insert the monitor call at the top of `_on_bar_close` (or equivalent — implementation plan grep-locates):

```python
def _on_bar_close(self, bar_date, closes_by_ticker, ...):
    # NEW: stop-loss monitor first
    open_pos = self._positions.open_positions()
    triggers = check_stop_loss_triggers(
        open_positions={t: {"qty": p.qty, "avg_price": p.avg_price}
                        for t, p in open_pos.items()},
        current_closes=closes_by_ticker,
        stop_loss_pct=float(
            self._strategy.risk.per_trade.stop_loss_pct
        ),
    )
    skip_tickers: set[str] = set()
    for trig in triggers:
        intent = OrderIntent(
            ticker=trig.ticker,
            side="SELL",
            qty=open_pos[trig.ticker].qty,
            intent_emitted_at=bar_date,
            exit_reason="stop_loss",
        )
        # Paper's SimBroker fills at next-bar-open same as backtest.
        self._broker.submit(intent)
        skip_tickers.add(trig.ticker)
    # Existing per-ticker eval skips stopped tickers.
    ...
```

Fill semantics: **next-bar-open**, identical to backtest. Paper is a simulator — preserves backtest's deterministic fill model.

### 4.5 Live runtime integration

`backend/algo/live/runtime.py::_on_bar_close()` is the same shape as paper's, but the exit must fill in real time. Insert at the top:

```python
def _on_bar_close(self, bar_date, closes_by_ticker, ...):
    # NEW: stop-loss monitor — emit MARKET SELL immediately
    open_pos = self._positions.open_positions()
    triggers = check_stop_loss_triggers(
        open_positions={t: {"qty": p.qty, "avg_price": p.avg_price}
                        for t, p in open_pos.items()},
        current_closes=closes_by_ticker,
        stop_loss_pct=float(
            self._strategy.risk.per_trade.stop_loss_pct
        ),
    )
    skip_tickers: set[str] = set()
    for trig in triggers:
        # Live: submit MARKET SELL to Kite immediately. The
        # broker fills at the next available market price.
        # No "next bar" — the live runtime acts in real time.
        self._kite_client.place_order(
            ticker=trig.ticker,
            side="SELL",
            qty=open_pos[trig.ticker].qty,
            order_type="MARKET",
            product=self._strategy.product,  # CNC or MIS
            exit_reason="stop_loss",  # propagated to algo.events
        )
        # Update position locally with the expected fill so the
        # rest of _on_bar_close sees an updated position state.
        # The actual fill confirmation comes back via the postback
        # webhook and PositionTracker.apply_fill is called at that
        # point (existing reconciliation flow).
        skip_tickers.add(trig.ticker)
    # Existing eval skips stopped tickers (no race: AST sees the
    # in-flight stop-out and stays out).
    ...
```

The exact `kite_client.place_order` signature may differ — implementation plan grep-confirms. Key requirements:
- Submit MARKET SELL (not LIMIT) — fills immediately at market
- Pass `exit_reason="stop_loss"` through to the order metadata so it lands in `algo.events`
- Don't wait for fill confirmation before continuing the bar — the postback handler updates `PositionTracker` async (same as today's exit signals)

### 4.6 Multi-lot positions

`PositionTracker` already uses weighted-average cost basis (`avg_price`). Stop-loss reads it directly. No special handling for scale-ins.

### 4.7 Exit-signal shape

All three runtimes emit `OrderIntent(side="SELL", exit_reason="stop_loss", ...)` (backtest + paper) or `kite_client.place_order(..., exit_reason="stop_loss")` (live). The exit_reason propagates to `Fill` → `PositionTracker._apply_sell` → closed `Position`, ending up in `algo.events.exit_reason`. Triage scripts already exclude `exit_reason == "stop_loss"` from win-rate denominators.

## 5. Test plan

### 5.1 Layered coverage across all 3 runtimes

| Layer | Tests | Catches |
|---|:---:|---|
| Unit — pure function | 8 | Trigger math; threshold boundary; missing data; disabled feature; multi-position independence |
| Backtest integration | 4 | Runner precedence; AST skip; next-bar-open fill; `exit_reason="stop_loss"` |
| **Paper integration** | 2-3 | Same as backtest but via paper runtime's `_on_bar_close` path |
| **Live integration** | 2-3 | Monitor → MARKET SELL via Kite (mocked); `exit_reason` plumbing; real-time semantics |
| Regression smoke | 1 parametrized × 11 | All existing templates parse cleanly with the new `exit_reason` field |
| Propagation | 4 | `exit_reason` threads through OrderIntent → Fill → Position |

Total: ~22 tests across 5 files. Runtime under 2 minutes combined.

### 5.2 Unit tests (`test_stop_loss_monitor.py`)

8 tests covering the pure function. See plan Task 1 for the verbatim test code.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_trigger_when_loss_exceeds_threshold` | avg=100, close=96, stop=3.0 → trigger, loss_pct=-4.0 |
| 2 | `test_no_trigger_when_loss_below_threshold` | avg=100, close=98, stop=3.0 → empty |
| 3 | `test_trigger_at_exact_boundary_is_inclusive` | avg=100, close=97, stop=3.0 → trigger (≤ semantics) |
| 4 | `test_no_trigger_when_position_gains` | avg=100, close=105, stop=3.0 → empty |
| 5 | `test_disabled_when_stop_loss_pct_zero` | stop=0.0 → empty list regardless |
| 6 | `test_skip_ticker_with_no_current_close` | data gap → skip, no fabrication |
| 7 | `test_skip_position_with_zero_avg_price` | defensive guard against div-by-zero |
| 8 | `test_multi_position_independence` | 3 tickers, only 1 breaches → exactly 1 trigger |

### 5.3 Backtest integration (`test_stop_loss_integration.py`)

4 tests against the runner with hand-built bar fixtures.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_stop_loss_emits_exit_at_breach_bar` | 10-bar fixture, drop -4% by bar 5 with stop=3.0 → exit signal at bar 5 close, fills at bar 6 open |
| 2 | `test_stop_loss_skips_ast_for_stopped_ticker` | AST not re-evaluated for stopped ticker same bar (proxy: full-qty close, no partial reduction) |
| 3 | `test_stop_loss_exit_reason_lands_in_events` | Recorded position has `exit_reason="stop_loss"` |
| 4 | `test_no_stop_loss_when_pct_zero` | Same fixture with stop=0 produces no stop events even on a -10% bar |

### 5.4 Paper integration (`test_stop_loss_paper_integration.py`)

2-3 tests against paper runtime's `_on_bar_close`:

| # | Test | Asserts |
|---|---|---|
| 1 | `test_paper_stop_loss_emits_sell_order_intent` | Open position via paper, drop close past threshold → SELL OrderIntent emitted with `exit_reason="stop_loss"` |
| 2 | `test_paper_stop_loss_fills_at_next_bar_open` | Same as backtest: fill date = bar after trigger |
| 3 | `test_paper_stop_loss_records_to_algo_events` (optional) | If algo.events writes are exercised in paper tests, confirm the exit_reason value lands |

### 5.5 Live integration (`test_stop_loss_live_integration.py`)

2-3 tests with mocked Kite client:

| # | Test | Asserts |
|---|---|---|
| 1 | `test_live_stop_loss_calls_kite_market_sell` | Mock `kite_client.place_order`; trigger fires; assert called with `order_type="MARKET"`, `side="SELL"`, correct qty |
| 2 | `test_live_stop_loss_propagates_exit_reason` | Mocked Kite call includes `exit_reason="stop_loss"` in the order metadata |
| 3 | `test_live_stop_loss_skips_ast_for_stopped_ticker` | Same precedence guarantee as backtest/paper |

### 5.6 Propagation tests (`test_exit_reason_propagation.py`)

4 tests covering the `exit_reason` field threading through OrderIntent → Fill → Position. See plan Task 2 for verbatim test code.

### 5.7 Regression smoke (`test_existing_strategies_smoke.py`)

One parametrized test over all 11 existing templates. Asserts each template still parses + has `stop_loss_pct >= 0` post-schema-change. Does NOT assert metric values (those legitimately change; impact captured separately in §6).

### 5.8 Expected metric impact (informational, NOT test assertions)

Post-implementation, the plan re-runs RSI(2) Connors v1 + v4 daily and captures before/after numbers in `docs/research/2026-05-23-stop-loss-enforcement-impact.md`:

**RSI(2) Connors v1 (ex-DIACABS, stop_loss_pct=3.0):**

| Gate | Before (no enforcement) | After (3% stop active) |
|---|---:|---:|
| G1: Trade count | 988 | TBD |
| G2: CAGR | 8.44% | TBD |
| G3: Win rate (ex-stops) | 62.85% | TBD |
| G4: Max DD | -19.89% | **TBD — must come under 15% to unblock paper promotion** |
| G5: Concentration | 12.62% | TBD |

**v4 daily (bull_momentum_daily_swing_v4, stop_loss_pct=4.0):**

| Metric | Before | After |
|---|---:|---:|
| Win rate | 53.6% | TBD |
| Max DD | TBD | TBD (likely lower) |
| Trade count | TBD | TBD (likely higher — stops add exits) |
| CAGR | TBD | TBD |

## 6. Promotion & rollout

### 6.1 Promotion path

No strategy-promotion lifecycle changes. Framework ships when the PR merges to `dev`. Existing strategies with `stop_loss_pct > 0` immediately get stops enforced in subsequent backtest, paper, and live sessions.

### 6.2 Backwards-compatibility

Fully backwards-compatible:

- Strategies with `stop_loss_pct: 0` get no enforcement.
- Strategies with `stop_loss_pct > 0` (all 11 templates) get stops enforced in all three runtimes.
- Stored `algo.runs` / `algo.events` rows from past backtests are unaffected.
- `OrderIntent.exit_reason: str = "signal"` default keeps existing AST-emit code unchanged.

### 6.3 Live-runtime safety note

Live trading currently uses MARKET / LIMIT orders only (Kite client blocks BO/SL/SLM/CO orders). Until this fix lands, live strategies are unprotected by stop-loss — they rely entirely on the strategy AST's own exit conditions or daily-kill triggers. **This is a meaningful safety gap in current live operations.** The fix closes it without waiting for Kite bracket-order support in v3.

### 6.4 Operator communication

A short note in `backend/algo/strategy/templates/README.md`:

> Strategies with `stop_loss_pct > 0` now have stops enforced in
> backtest, paper, AND live runtimes (after 2026-05-23). Local
> enforcement via per-bar position monitor; broker-side bracket
> orders are deferred to a future Kite client v3.

## 7. Non-goals (explicit out-of-scope)

- Short-side stop-loss (no shorts in framework yet)
- Trailing stops (static stop first)
- Time-based / N-day timeouts (separate AST-level concern)
- Take-profit enforcement (no `take_profit_pct` field)
- Kite bracket-order emission (deferred to v3 per kite_client.py)
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
- AST risk schema: `backend/algo/strategy/ast.py::RiskPerTrade`
- Kite client v2 SDK constraints: `backend/algo/broker/kite_client.py` (blocks BO/SL/SLM/CO)
- Project memory: `feedback_runtime_feature_three_runtimes` — applicable principle ("wire features through ALL runtimes") — this spec is the canonical case
