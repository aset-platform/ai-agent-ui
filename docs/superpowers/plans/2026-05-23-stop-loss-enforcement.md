# Stop-Loss Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `RiskPerTrade.stop_loss_pct` actually enforced in all three runtimes (backtest + paper + live). Declared-but-unconsumed today, will become a per-bar position monitor that emits `exit_reason="stop_loss"` SELL signals — fills at next-bar-open in backtest/paper and immediately at MARKET in live.

**Architecture:** New pure module `backend/algo/backtest/stop_loss_monitor.py` exporting one function `check_stop_loss_triggers(...)`. Each runtime calls it in `_on_bar_close` (or equivalent) before AST eval; stopped tickers skip AST evaluation and emit SELL orders tagged with a new `exit_reason` field that propagates through `Fill` → `PositionTracker._apply_sell` → closed `Position`.

**Tech Stack:** Python 3.12 · Pydantic v2 (OrderIntent / Fill / Position) · pytest · unittest.mock (for Kite). Spec: `docs/superpowers/specs/2026-05-23-stop-loss-enforcement-design.md`.

---

## File Structure

| Path | Action | Purpose | LOC |
|---|---|---|---|
| `backend/algo/backtest/stop_loss_monitor.py` | create | Pure `check_stop_loss_triggers()` shared by all 3 runtimes | ~70 |
| `backend/algo/backtest/tests/test_stop_loss_monitor.py` | create | 8 unit tests | ~140 |
| `backend/algo/backtest/types.py` | modify | Add `exit_reason: str = "signal"` to `OrderIntent` + `Fill` | +6 |
| `backend/algo/backtest/positions.py` | modify | `_apply_sell` stamps `fill.exit_reason` on closed `Position` | +3 |
| `backend/algo/backtest/tests/test_exit_reason_propagation.py` | create | 4 propagation tests | ~80 |
| `backend/algo/backtest/runner.py` | modify | Call monitor before AST eval per bar | +25 |
| `backend/algo/backtest/tests/test_stop_loss_integration.py` | create | 4 backtest integration tests | ~180 |
| `backend/algo/paper/runtime.py` | modify | Call monitor in `_on_bar_close` | +25 |
| `backend/algo/paper/tests/test_stop_loss_paper_integration.py` | create | 2-3 paper integration tests | ~120 |
| `backend/algo/live/runtime.py` | modify | Call monitor in `_on_bar_close`; emit MARKET SELL via Kite | +30 |
| `backend/algo/live/tests/test_stop_loss_live_integration.py` | create | 2-3 live integration tests (mocked Kite) | ~140 |
| `backend/algo/backtest/tests/test_existing_strategies_smoke.py` | create | 1 parametrized test over 11 templates | ~80 |
| `backend/algo/strategy/templates/README.md` | modify | Operator note: stops now enforced in all 3 runtimes | +6 |
| `docs/research/2026-05-23-stop-loss-enforcement-impact.md` | create | RSI(2) v1 + v4 daily before/after triage | ~100 |

Working branch: `framework/backtest-stop-loss-enforcement-spec` (spec already committed at `e6cede6` + revised at `bdba515`). All implementation tasks land additional commits; final PR squash-merges to `dev`.

---

## Task 1: Pure `stop_loss_monitor` module + unit tests (TDD)

**Files:**
- Create: `backend/algo/backtest/stop_loss_monitor.py`
- Create: `backend/algo/backtest/tests/test_stop_loss_monitor.py`

### Step 1: Write the failing tests

```python
"""Tests for the pure stop-loss trigger function."""

from decimal import Decimal

import pytest

from backend.algo.backtest.stop_loss_monitor import (
    StopLossTrigger,
    check_stop_loss_triggers,
)


def _open_position(qty: int = 100,
                   avg_price: float = 100.0) -> dict:
    return {"qty": qty, "avg_price": Decimal(str(avg_price))}


def test_trigger_when_loss_exceeds_threshold():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={"AAA.NS": Decimal("96")},
        stop_loss_pct=3.0,
    )
    assert len(triggers) == 1
    t = triggers[0]
    assert t.ticker == "AAA.NS"
    assert t.avg_price == Decimal("100")
    assert t.current_close == Decimal("96")
    assert t.loss_pct == Decimal("-4")
    assert t.stop_loss_pct == Decimal("3.0")


def test_no_trigger_when_loss_below_threshold():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={"AAA.NS": Decimal("98")},
        stop_loss_pct=3.0,
    )
    assert triggers == []


def test_trigger_at_exact_boundary_is_inclusive():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={"AAA.NS": Decimal("97")},
        stop_loss_pct=3.0,
    )
    assert len(triggers) == 1
    assert triggers[0].loss_pct == Decimal("-3")


def test_no_trigger_when_position_gains():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={"AAA.NS": Decimal("105")},
        stop_loss_pct=3.0,
    )
    assert triggers == []


def test_disabled_when_stop_loss_pct_zero():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={"AAA.NS": Decimal("50")},
        stop_loss_pct=0.0,
    )
    assert triggers == []


def test_skip_ticker_with_no_current_close():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=100)},
        current_closes={},
        stop_loss_pct=3.0,
    )
    assert triggers == []


def test_skip_position_with_zero_avg_price():
    triggers = check_stop_loss_triggers(
        open_positions={"AAA.NS": _open_position(avg_price=0)},
        current_closes={"AAA.NS": Decimal("50")},
        stop_loss_pct=3.0,
    )
    assert triggers == []


def test_multi_position_independence():
    triggers = check_stop_loss_triggers(
        open_positions={
            "AAA.NS": _open_position(avg_price=100),
            "BBB.NS": _open_position(avg_price=100),
            "CCC.NS": _open_position(avg_price=100),
        },
        current_closes={
            "AAA.NS": Decimal("96"),
            "BBB.NS": Decimal("99"),
            "CCC.NS": Decimal("105"),
        },
        stop_loss_pct=3.0,
    )
    assert len(triggers) == 1
    assert triggers[0].ticker == "AAA.NS"
```

### Step 2: Run tests to verify they fail

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/backtest/tests/test_stop_loss_monitor.py -v
```

Expected: ImportError (8 errors).

### Step 3: Write the module

```python
"""Stop-loss monitor — per-bar exit-trigger detector.

Pure function. Shared by backtest + paper + live runtimes. Each
runtime translates triggers into runtime-appropriate exit orders
(backtest + paper: next-bar-open via SimBroker; live: immediate
MARKET via Kite).

Long-only v1. Short-side positions are out of scope.
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
    loss_pct: Decimal
    stop_loss_pct: Decimal


def check_stop_loss_triggers(
    *,
    open_positions: dict[str, dict],
    current_closes: dict[str, Decimal],
    stop_loss_pct: float,
) -> list[StopLossTrigger]:
    """Return triggers for positions whose loss exceeds the threshold.

    For each open long position with ``avg_price > 0`` and a
    ``current_close`` available::

        loss_pct = (current_close - avg_price) / avg_price * 100
        trigger if loss_pct <= -stop_loss_pct

    Returns empty list if ``stop_loss_pct == 0`` (feature disabled)
    or no positions breach. Skips tickers with missing closes
    (data gap; don't fabricate).

    Args:
        open_positions: ``ticker → {"qty": int, "avg_price": Decimal}``
        current_closes: ``ticker → close at this bar``
        stop_loss_pct: From ``strategy.risk.per_trade.stop_loss_pct``

    Returns:
        Empty list when feature disabled or no breaches.
    """
    if stop_loss_pct <= 0:
        return []

    threshold = Decimal(str(stop_loss_pct))
    minus_threshold = -threshold

    triggers: list[StopLossTrigger] = []
    for ticker, pos in open_positions.items():
        avg_price = pos.get("avg_price")
        if avg_price is None or avg_price <= 0:
            continue
        current_close = current_closes.get(ticker)
        if current_close is None:
            continue
        loss_pct = (
            (Decimal(str(current_close)) - Decimal(str(avg_price)))
            / Decimal(str(avg_price))
            * Decimal("100")
        )
        if loss_pct <= minus_threshold:
            triggers.append(StopLossTrigger(
                ticker=ticker,
                avg_price=Decimal(str(avg_price)),
                current_close=Decimal(str(current_close)),
                loss_pct=loss_pct,
                stop_loss_pct=threshold,
            ))
    return triggers
```

### Step 4: Run tests to verify they pass

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/backtest/tests/test_stop_loss_monitor.py -v
```

Expected: 8 passed.

### Step 5: Commit

```bash
git add backend/algo/backtest/stop_loss_monitor.py \
        backend/algo/backtest/tests/test_stop_loss_monitor.py
git commit -m "feat(backtest): pure stop_loss_monitor module + 8 unit tests"
```

---

## Task 2: Add `exit_reason` field to `OrderIntent` + `Fill` + propagate to `Position`

**Files:**
- Modify: `backend/algo/backtest/types.py`
- Modify: `backend/algo/backtest/positions.py`
- Create: `backend/algo/backtest/tests/test_exit_reason_propagation.py`

### Step 1: Write the failing tests

```python
"""exit_reason threads OrderIntent → Fill → closed Position."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.backtest.types import Fill, OrderIntent
from backend.algo.backtest.positions import PositionTracker


def test_order_intent_defaults_exit_reason_to_signal():
    oi = OrderIntent(
        ticker="AAA.NS",
        side="BUY",
        qty=10,
        intent_emitted_at=date(2025, 1, 1),
    )
    assert oi.exit_reason == "signal"


def test_order_intent_accepts_custom_exit_reason():
    oi = OrderIntent(
        ticker="AAA.NS",
        side="SELL",
        qty=10,
        intent_emitted_at=date(2025, 1, 1),
        exit_reason="stop_loss",
    )
    assert oi.exit_reason == "stop_loss"


def test_fill_carries_exit_reason():
    f = Fill(
        intent_id=uuid4(),
        ticker="AAA.NS",
        side="SELL",
        qty=10,
        fill_price=Decimal("100"),
        fill_date=date(2025, 1, 2),
        fees_inr=Decimal("5"),
        fee_rates_version="v1",
        exit_reason="stop_loss",
    )
    assert f.exit_reason == "stop_loss"


def test_apply_sell_stamps_exit_reason_on_closed_position():
    pt = PositionTracker()
    pt.apply_fill(Fill(
        intent_id=uuid4(),
        ticker="AAA.NS",
        side="BUY",
        qty=10,
        fill_price=Decimal("100"),
        fill_date=date(2025, 1, 1),
        fees_inr=Decimal("5"),
        fee_rates_version="v1",
    ))
    pt.apply_fill(Fill(
        intent_id=uuid4(),
        ticker="AAA.NS",
        side="SELL",
        qty=10,
        fill_price=Decimal("95"),
        fill_date=date(2025, 1, 2),
        fees_inr=Decimal("4"),
        fee_rates_version="v1",
        exit_reason="stop_loss",
    ))
    closed = pt.closed_positions()
    assert len(closed) == 1
    assert closed[0].exit_reason == "stop_loss"
```

### Step 2: Run tests to verify they fail

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/backtest/tests/test_exit_reason_propagation.py -v
```

Expected: 4 failures — fields don't exist.

### Step 3: Add `exit_reason` to `OrderIntent` and `Fill`

Edit `backend/algo/backtest/types.py`.

In `class OrderIntent(BaseModel):`, before `intent_emitted_ts_ns`:

```python
    # exit_reason tag — propagates through Fill to Position.
    # "signal" (AST), "stop_loss", "mis_square_off", "period_end_mtm".
    # Default "signal" keeps existing AST-emit code backwards-compat.
    exit_reason: str = "signal"
```

In `class Fill(BaseModel):`, alongside `fees_inr`:

```python
    # Propagated from the originating OrderIntent.
    exit_reason: str = "signal"
```

Verify `Position` has an `exit_reason` field. If not, add to `class Position(BaseModel):`:

```python
    exit_reason: str | None = None
```

Confirm via:
```bash
docker compose exec -T backend python -c \
  "from backend.algo.backtest.types import Position; \
   p = Position(ticker='X', qty=1, avg_price=1, opened_at='2025-01-01'); \
   print('exit_reason' in p.model_fields)"
```

### Step 4: Wire `exit_reason` through `_apply_sell` in `positions.py`

Both closed-Position constructions (full close + partial close) get `exit_reason=fill.exit_reason`:

```python
        # Full close branch:
        if sell_qty == existing.qty:
            closed = existing.model_copy(update={
                "closed_at": fill.fill_date,
                "closed_at_ts_ns": fill.fill_ts_ns,
                "realised_pnl_inr": realised,
                "exit_reason": fill.exit_reason,    # NEW
            })
            ...

        # Partial close branch:
        else:
            self._closed.append(Position(
                ticker=existing.ticker,
                qty=sell_qty,
                avg_price=existing.avg_price,
                opened_at=existing.opened_at,
                opened_at_ts_ns=existing.opened_at_ts_ns,
                closed_at=fill.fill_date,
                closed_at_ts_ns=fill.fill_ts_ns,
                realised_pnl_inr=realised,
                exit_reason=fill.exit_reason,        # NEW
            ))
            ...
```

### Step 5: Run tests to verify pass

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/backtest/tests/test_exit_reason_propagation.py \
    backend/algo/backtest/tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all 4 new tests pass + no regressions in existing backtest tests.

### Step 6: Commit

```bash
git add backend/algo/backtest/types.py \
        backend/algo/backtest/positions.py \
        backend/algo/backtest/tests/test_exit_reason_propagation.py
git commit -m "feat(backtest): exit_reason field on OrderIntent + Fill + Position"
```

---

## Task 3: Backtest runner integration + integration tests

**Files:**
- Modify: `backend/algo/backtest/runner.py`
- Create: `backend/algo/backtest/tests/test_stop_loss_integration.py`

### Step 1: Locate per-bar loop in runner

```bash
grep -nE "for ticker in universe|risk.gate|RiskEngine|broker\." \
    backend/algo/backtest/runner.py | head -10
```

Read 30-50 lines around the per-ticker loop to identify:
- The variable holding closes-by-ticker for the current bar
- The PositionTracker variable name (probably `pt` or `positions`)
- The broker submit method
- The current bar date variable

### Step 2: Insert monitor call before the per-ticker AST eval

```python
# NEW: stop-loss monitor — emit SELL intents for breaching positions.
from backend.algo.backtest.stop_loss_monitor import (
    check_stop_loss_triggers,
)

open_pos = pt.open_positions()  # adjust variable name if needed
open_pos_dicts = {
    t: {"qty": p.qty, "avg_price": p.avg_price}
    for t, p in open_pos.items()
}
stop_triggers = check_stop_loss_triggers(
    open_positions=open_pos_dicts,
    current_closes=closes_by_ticker,  # adjust variable name if needed
    stop_loss_pct=float(strategy.risk.per_trade.stop_loss_pct),
)
stop_loss_skip: set[str] = set()
for trig in stop_triggers:
    intent = OrderIntent(
        ticker=trig.ticker,
        side="SELL",
        qty=open_pos[trig.ticker].qty,
        intent_emitted_at=current_date,
        intent_emitted_ts_ns=current_ts_ns,
        exit_reason="stop_loss",
    )
    broker.submit(intent)
    stop_loss_skip.add(trig.ticker)
    _logger.debug(
        "stop_loss trigger %s avg=%.4f close=%.4f "
        "loss=%.2f%% stop=%.2f%%",
        trig.ticker, float(trig.avg_price),
        float(trig.current_close), float(trig.loss_pct),
        float(trig.stop_loss_pct),
    )
```

In the existing per-ticker AST loop, add:

```python
for ticker in universe:
    if ticker in stop_loss_skip:
        continue
    ... existing AST eval flow ...
```

### Step 3: Write integration tests

```python
"""Backtest stop-loss integration tests."""

from datetime import date
from decimal import Decimal

import pytest

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BarData
from backend.algo.strategy.ast import parse_strategy


def _ramp_then_drop_bars(ticker: str) -> list[BarData]:
    """10 bars: flat 100 for 5 bars, drop -4% to 96 by bar 5."""
    bars = []
    for i, close in enumerate(
        [100, 100, 100, 100, 100, 96, 95, 94, 94, 94]
    ):
        bars.append(BarData(
            ticker=ticker,
            date=date(2025, 1, 1 + i),
            open=Decimal(str(close)),
            high=Decimal(str(close + 0.5)),
            low=Decimal(str(close - 0.5)),
            close=Decimal(str(close)),
            volume=10000,
        ))
    return bars


def _stop_loss_strategy(stop_pct: float):
    """Minimal: buy + hold; stop_loss_pct=stop_pct."""
    return parse_strategy({
        "id": "00000000-0000-0000-0000-000000000099",
        "name": "test-stop-loss",
        "universe": {
            "type": "scope", "scope": "discovery",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {"type": "bar_close", "interval": "1d",
                     "time": "15:25 IST"},
        "rebalance": {"type": "daily", "max_positions": 1},
        "product": "CNC",
        "root": {"type": "set_target_weight", "weight": 1.0},
        "risk": {
            "per_trade": {"stop_loss_pct": stop_pct, "max_qty": 1000000},
            "portfolio": {"max_exposure_pct": 100.0,
                          "max_concentration_pct": 100.0},
            "daily": {"max_loss_pct": 50.0, "max_open_positions": 1},
        },
    })


def _run_with_bars(strategy, bars):
    """Adapt to existing test pattern.

    Read backend/algo/backtest/tests/test_runner.py (or sibling)
    for the canonical hand-built-bars invocation. Copy that
    pattern's BacktestRequest construction here.
    """
    # IMPLEMENTER: pattern-match against existing tests
    ...


def test_stop_loss_emits_exit_at_breach_bar():
    """Position opens bar 1. Close drops -4% by bar 5 with stop=3.0
    → exit signal at bar 5, fills at bar 6 open."""
    strategy = _stop_loss_strategy(stop_pct=3.0)
    bars = _ramp_then_drop_bars("AAA.NS")
    summary = _run_with_bars(strategy, bars)

    closed = summary.trade_list
    stop_exits = [t for t in closed if t.exit_reason == "stop_loss"]
    assert len(stop_exits) == 1
    assert stop_exits[0].closed_at == date(2025, 1, 6)


def test_no_stop_loss_when_pct_zero():
    """stop_loss_pct=0 → no stop events even on a -10% bar."""
    strategy = _stop_loss_strategy(stop_pct=0.0)
    bars = _ramp_then_drop_bars("AAA.NS")
    summary = _run_with_bars(strategy, bars)

    stop_exits = [
        t for t in summary.trade_list
        if t.exit_reason == "stop_loss"
    ]
    assert stop_exits == []


def test_stop_loss_skips_ast_for_stopped_ticker():
    """Proxy: full-qty close, no partial reduction from AST
    emitting a different target weight on the same bar."""
    strategy = _stop_loss_strategy(stop_pct=3.0)
    bars = _ramp_then_drop_bars("AAA.NS")
    summary = _run_with_bars(strategy, bars)

    stop_exits = [
        t for t in summary.trade_list
        if t.exit_reason == "stop_loss"
    ]
    assert len(stop_exits) == 1
    assert stop_exits[0].qty > 0


def test_stop_loss_exit_reason_lands_in_trade_list():
    strategy = _stop_loss_strategy(stop_pct=3.0)
    bars = _ramp_then_drop_bars("AAA.NS")
    summary = _run_with_bars(strategy, bars)
    reasons = {t.exit_reason for t in summary.trade_list}
    assert "stop_loss" in reasons
```

### Step 4: Run tests

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/backtest/tests/test_stop_loss_integration.py -v
```

Expected: 4 passed.

If `_run_with_bars` doesn't work because the existing test pattern is different than assumed, downgrade to a closer-to-unit test that exercises just the new monitor-→-broker path with stubbed strategy + tracker. Document the downgrade in the test docstring.

### Step 5: Commit

```bash
git add backend/algo/backtest/runner.py \
        backend/algo/backtest/tests/test_stop_loss_integration.py
git commit -m "feat(backtest): integrate stop_loss_monitor + 4 integration tests"
```

---

## Task 4: Paper runtime integration + tests

**Files:**
- Modify: `backend/algo/paper/runtime.py`
- Create: `backend/algo/paper/tests/test_stop_loss_paper_integration.py`

### Step 1: Locate `_on_bar_close` in paper runtime

```bash
grep -nE "def _on_bar_close|self._positions|self._broker|self._strategy" \
    backend/algo/paper/runtime.py | head -15
```

Find the `_on_bar_close` (or similarly-named) per-bar entry point. Identify:
- `self._positions` (PositionTracker)
- `self._broker` (SimBroker)
- `self._strategy` (parsed Strategy)
- The closes-by-ticker variable passed to or built in `_on_bar_close`

### Step 2: Insert monitor call at the top of `_on_bar_close`

```python
def _on_bar_close(self, bar_date, closes_by_ticker, ...):
    # NEW: stop-loss monitor runs first.
    from backend.algo.backtest.stop_loss_monitor import (
        check_stop_loss_triggers,
    )
    from backend.algo.backtest.types import OrderIntent

    open_pos = self._positions.open_positions()
    open_pos_dicts = {
        t: {"qty": p.qty, "avg_price": p.avg_price}
        for t, p in open_pos.items()
    }
    triggers = check_stop_loss_triggers(
        open_positions=open_pos_dicts,
        current_closes=closes_by_ticker,
        stop_loss_pct=float(
            self._strategy.risk.per_trade.stop_loss_pct
        ),
    )
    stop_loss_skip: set[str] = set()
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
        stop_loss_skip.add(trig.ticker)

    # EXISTING: per-ticker AST eval skips stopped tickers.
    for ticker in ...:
        if ticker in stop_loss_skip:
            continue
        ... existing flow ...
```

The exact insertion point (top of `_on_bar_close`) and skip-set wiring depends on the paper runtime's existing structure — read 50 lines around the method to confirm.

### Step 3: Write paper integration tests

```python
"""Paper runtime stop-loss integration."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def test_paper_stop_loss_emits_sell_order_intent():
    """When monitor triggers in _on_bar_close, paper's broker
    receives a SELL OrderIntent tagged exit_reason='stop_loss'."""
    # IMPLEMENTER: pattern-match against existing paper tests
    # (find one in backend/algo/paper/tests/) for the runtime
    # setup. The shape is roughly:
    #   1. Construct paper runtime with mock broker
    #   2. Open a position via a buy fill
    #   3. Call _on_bar_close with closes_by_ticker that breaches
    #      stop_loss_pct
    #   4. Assert mock broker received SELL with exit_reason
    ...


def test_paper_stop_loss_fills_at_next_bar_open():
    """Paper preserves backtest semantics: SimBroker fills SELL
    at the NEXT bar's open."""
    # IMPLEMENTER: same pattern as above, but assert on fill date.
    ...


def test_paper_stop_loss_no_trigger_when_pct_zero():
    """stop_loss_pct=0 → no monitor triggers in paper either."""
    # IMPLEMENTER: same pattern, stop=0, assert no SELL emitted.
    ...
```

`_on_bar_close` test scaffolding requires reading at least one existing paper-runtime test to mirror the runtime-construction pattern. Document any pattern adaptations made.

### Step 4: Run tests

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/paper/tests/test_stop_loss_paper_integration.py -v
```

Expected: 3 passed.

### Step 5: Run the broader paper test suite to confirm no regressions

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/paper/tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all existing tests pass + the 3 new ones.

### Step 6: Commit

```bash
git add backend/algo/paper/runtime.py \
        backend/algo/paper/tests/test_stop_loss_paper_integration.py
git commit -m "feat(paper): integrate stop_loss_monitor in _on_bar_close"
```

---

## Task 5: Live runtime integration + tests

**Files:**
- Modify: `backend/algo/live/runtime.py`
- Create: `backend/algo/live/tests/test_stop_loss_live_integration.py`

### Step 1: Locate `_on_bar_close` in live runtime + Kite client interface

```bash
grep -nE "def _on_bar_close|self._kite|kite_client\.|place_order|self._positions" \
    backend/algo/live/runtime.py | head -15

grep -nE "def place_order|class.*Kite" backend/algo/broker/kite_client.py | head -10
```

Identify:
- `_on_bar_close` per-bar entry point in live runtime
- `self._kite_client` (or equivalent) — the Kite client instance
- The `place_order` method signature on the Kite client (params: ticker, side, qty, order_type, product, ...)

### Step 2: Insert monitor call at the top of `_on_bar_close`

```python
def _on_bar_close(self, bar_date, closes_by_ticker, ...):
    # NEW: stop-loss monitor runs first.
    from backend.algo.backtest.stop_loss_monitor import (
        check_stop_loss_triggers,
    )

    open_pos = self._positions.open_positions()
    open_pos_dicts = {
        t: {"qty": p.qty, "avg_price": p.avg_price}
        for t, p in open_pos.items()
    }
    triggers = check_stop_loss_triggers(
        open_positions=open_pos_dicts,
        current_closes=closes_by_ticker,
        stop_loss_pct=float(
            self._strategy.risk.per_trade.stop_loss_pct
        ),
    )
    stop_loss_skip: set[str] = set()
    for trig in triggers:
        # Live: submit MARKET SELL to Kite immediately.
        # No next-bar-open semantics — fills at current market.
        self._kite_client.place_order(
            ticker=trig.ticker,
            side="SELL",
            qty=open_pos[trig.ticker].qty,
            order_type="MARKET",
            product=self._strategy.product,  # CNC or MIS
            exit_reason="stop_loss",
            # ...other required Kite place_order args per
            # the actual signature
        )
        stop_loss_skip.add(trig.ticker)
        _logger.warning(
            "stop_loss MARKET SELL %s qty=%d loss=%.2f%% "
            "(threshold=%.2f%%)",
            trig.ticker, open_pos[trig.ticker].qty,
            float(trig.loss_pct), float(trig.stop_loss_pct),
        )

    # EXISTING: per-ticker AST eval skips stopped tickers.
    ...
```

**If Kite's `place_order` doesn't currently accept an `exit_reason` kwarg**: add it as an optional kwarg passed through to the order-record metadata, OR record the exit_reason in a separate per-ticker pending-stop dict that the postback handler reads when the fill confirmation arrives. Implementation plan: read `kite_client.py::place_order` to determine which approach fits.

### Step 3: Write live integration tests

```python
"""Live runtime stop-loss integration (Kite mocked)."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def test_live_stop_loss_calls_kite_market_sell():
    """When monitor triggers, live runtime calls
    kite_client.place_order with order_type='MARKET',
    side='SELL', correct qty."""
    # IMPLEMENTER: pattern-match against existing live runtime
    # tests (read backend/algo/live/tests/test_mis_e2e_smoke.py
    # or similar) for the runtime + kite-mock setup.
    ...


def test_live_stop_loss_propagates_exit_reason():
    """The Kite place_order call (or its metadata) carries
    exit_reason='stop_loss'."""
    ...


def test_live_stop_loss_skips_ast_for_stopped_ticker():
    """AST eval is skipped for the stopped ticker on the
    trigger bar."""
    ...
```

The exact mock structure depends on how existing live-runtime tests mock the Kite client. Read `backend/algo/live/tests/test_mis_e2e_smoke.py` (line 125 was the example) for the canonical setup pattern.

### Step 4: Run tests

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/live/tests/test_stop_loss_live_integration.py -v
```

Expected: 3 passed.

### Step 5: Run the broader live test suite to confirm no regressions

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/live/tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all existing tests pass + the 3 new ones.

### Step 6: Commit

```bash
git add backend/algo/live/runtime.py \
        backend/algo/live/tests/test_stop_loss_live_integration.py
git commit -m "feat(live): integrate stop_loss_monitor; MARKET SELL via Kite at trigger time"
```

---

## Task 6: Regression smoke across all 11 existing templates

**Files:**
- Create: `backend/algo/backtest/tests/test_existing_strategies_smoke.py`

### Step 1: Write the parametrized smoke test

```python
"""Regression smoke: all 11 existing templates still parse cleanly
after the OrderIntent.exit_reason / Fill.exit_reason field additions.

Does NOT assert metric values — those legitimately change when
stop-loss enforcement becomes active. Numerical impact captured
in docs/research/2026-05-23-stop-loss-enforcement-impact.md.
"""

import json
from pathlib import Path

import pytest

from backend.algo.strategy.ast import parse_strategy


_TEMPLATES = [
    "bull_momentum_daily_swing_v4",
    "rsi2_connors_daily_v1",
    "regime_sideways_meanrev_quality_v2",
    "regime_bull_momentum",
    "regime_bear_defensive_lowvol",
    "mis_intraday_meanrev_long_v1",
    "sector_rotation_monthly",
    "bull_momentum_15m_swing",
    "bull_momentum_daily_swing",
    "bull_momentum_daily_swing_v3",
    "regime_sideways_meanrev_quality",
]


@pytest.mark.parametrize("template_name", _TEMPLATES)
def test_template_parses_after_exit_reason_schema_change(
    template_name,
):
    template_path = (
        Path(__file__).parent.parent.parent / "strategy" / "templates"
        / f"{template_name}.json"
    )
    template_dict = json.loads(template_path.read_text())
    strategy = parse_strategy(template_dict)
    assert strategy.name
    assert strategy.risk.per_trade.stop_loss_pct >= 0
```

### Step 2: Run the test

```bash
docker compose exec -T backend python -m pytest \
    backend/algo/backtest/tests/test_existing_strategies_smoke.py -v
```

Expected: 11 passed.

### Step 3: Commit

```bash
git add backend/algo/backtest/tests/test_existing_strategies_smoke.py
git commit -m "test(backtest): regression smoke across all 11 existing templates"
```

---

## Task 7: Operator documentation

**Files:**
- Modify: `backend/algo/strategy/templates/README.md`

### Step 1: Append the convention note

Read the existing README (it was created in MIS v1 PR #230 — Task 6). Append a new section:

```markdown
## Stop-loss enforcement (2026-05-23 framework fix)

Strategies with `stop_loss_pct > 0` now have stops enforced in
backtest, paper, AND live runtimes. Local enforcement via per-bar
position monitor; broker-side bracket orders are deferred to a
future Kite client v3.

- **Backtest + paper**: stop triggers fire at bar close, fills
  land at the NEXT bar's open via SimBroker (same fill semantics
  as AST-emitted exits).
- **Live**: stop triggers fire at bar close, MARKET SELL submitted
  to Kite immediately. Fills at the next available market price
  (typically within seconds).

All three runtimes record `exit_reason="stop_loss"` on the closed
position. Triage scripts already exclude stop-loss exits from
win-rate denominators.

Strategies with `stop_loss_pct: 0` get no enforcement (feature
disabled). Past backtests are unaffected — only future runs
include stops.
```

### Step 2: Commit

```bash
git add backend/algo/strategy/templates/README.md
git commit -m "docs(strategy): stop-loss enforcement convention note"
```

---

## Task 8: Re-run RSI(2) Connors v1 + v4 daily, capture before/after impact

**Files:**
- Create: `docs/research/2026-05-23-stop-loss-enforcement-impact.md`

### Step 1: Re-run RSI(2) Connors v1 with stop-loss now active

```bash
docker compose exec -T backend python /app/scripts/run_rsi2_connors_baseline.py \
    --exclude "DIACABS.NS" \
    --tag "rsi2-connors-daily-stop3-postfix" \
    2>&1 | tee /tmp/rsi2_stop3_postfix.log
```

Read `/tmp/rsi2-connors-daily-stop3-postfix_triage.json`. Compare against the pre-fix result (G4 was -19.89% before fix). **Headline question: does G4 now pass the ≤15% threshold?**

### Step 2: Re-run v4 daily with stop-loss now active

If no existing baseline runner targets v4, write an inline command (mirror the RSI(2) runner pattern at `scripts/run_rsi2_connors_baseline.py`):

```bash
docker compose exec -T backend python <<'PY' 2>&1 | tee /tmp/v4_postfix.log
import json
from datetime import date
from pathlib import Path

from backend.algo.strategy.ast import parse_strategy
from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.universe import resolve_universe
import asyncio

template = json.loads(Path(
    "/app/backend/algo/strategy/templates/"
    "bull_momentum_daily_swing_v4.json"
).read_text())
strategy = parse_strategy(template)

class _UserStub:
    user_id = "system"

tickers = asyncio.run(resolve_universe(user=_UserStub(), strategy=strategy))

# IMPLEMENTER: pattern-match the BacktestRequest construction
# against scripts/run_rsi2_connors_baseline.py and invoke
# run_backtest() with start_date=2022-01-01, end_date=2026-05-21,
# starting_nav_inr=1_000_000, tag="v4-daily-postfix".
...
PY
```

### Step 3: Write the impact report

Create `docs/research/2026-05-23-stop-loss-enforcement-impact.md`:

```markdown
# Stop-Loss Enforcement Framework Fix — Impact Report

| | |
|---|---|
| Date | 2026-05-23 |
| Spec | docs/superpowers/specs/2026-05-23-stop-loss-enforcement-design.md |
| Predecessor | docs/research/2026-05-23-rsi2-connors-stop3-final.md (diagnosed the gap) |

## RSI(2) Connors v1 (ex-DIACABS) — Before vs After

| Gate | Threshold | Before (no stop enforcement) | After (3% stop active) |
|---|---|---|---|
| G1: Trade count | ≥ 200 | 988 | <fill> |
| G2: CAGR | ≥ 8% | 8.44% | <fill> |
| G3: Win rate (ex-stops) | ≥ 60% | 62.85% | <fill> |
| G4: Max drawdown | ≤ 15% | -19.89% | <fill — DECIDES PAPER PROMOTION> |
| G5: Concentration | ≤ 20% | 12.62% (APOLLO.NS) | <fill> |

**Decision:** <one of:
  "ALL 5 GATES GREEN — promote RSI(2) v1 to paper trading"
  "G4 still failing despite stop enforcement — file v2 with tighter stops"
  "Other regression — investigate">

## v4 Daily (bull_momentum_daily_swing_v4) — Before vs After

| Metric | Before (no stop enforcement) | After (4% stop active) |
|---|---|---|
| Win rate | 53.6% | <fill> |
| Max DD | <fill from before-run> | <fill> |
| Trade count | <fill> | <fill> (likely higher) |
| CAGR | <fill> | <fill> |

**Commentary:** <2-3 sentences>

## Verdict on the framework fix

<one of:
  "Framework fix works as designed across all 3 runtimes."
  "Framework fix works mechanically but exposed strategy-level issues">
```

Fill in real numbers.

### Step 4: Commit the impact report

```bash
git add docs/research/2026-05-23-stop-loss-enforcement-impact.md
git commit -m "docs(research): stop-loss enforcement impact on RSI(2) v1 + v4 daily"
```

---

## Task 9: Push + PR

- [ ] **Step 1: Push**

```bash
git push -u origin framework/backtest-stop-loss-enforcement-spec
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev \
  --title "feat(framework): enforce stop_loss_pct in backtest + paper + live (all 3 runtimes)" \
  --body "$(cat <<'EOF'
## Summary

Fixes the framework gap discovered during RSI(2) Connors v1 backtest: \`stop_loss_pct\` declared in 11 strategy templates but consumed by NO runtime. After this PR, all three runtimes (backtest + paper + live) run a per-bar position monitor that emits \`exit_reason="stop_loss"\` SELL signals.

### Initial spec was wrong-scope — corrected during planning

Initial draft scoped backtest only on the assumption that paper + live delegated to Kite bracket orders. Audit found:
- \`kite_client.py\` explicitly rejects bracket orders ("deferred to v3")
- \`paper/runtime.py\` has zero stop_loss / bracket references
- Both paper and live had the SAME gap as backtest

This PR closes ALL THREE.

### What's in this PR

- **Spec** (\`docs/superpowers/specs/2026-05-23-stop-loss-enforcement-design.md\`) — initial commit + scope-correction commit
- **Plan** (\`docs/superpowers/plans/2026-05-23-stop-loss-enforcement.md\`) — 9 tasks
- **Pure module** (\`backend/algo/backtest/stop_loss_monitor.py\`): 1 dataclass + 1 pure function, ~70 LOC, shared across runtimes
- **Schema changes**: \`exit_reason: str = "signal"\` field added to \`OrderIntent\` + \`Fill\` + \`Position\`. Backwards-compatible.
- **Backtest runner**: monitor in per-bar loop; SELL OrderIntent with \`exit_reason="stop_loss"\`; fills at next-bar-open via SimBroker
- **Paper runtime**: monitor in \`_on_bar_close\`; same fill semantics as backtest
- **Live runtime**: monitor in \`_on_bar_close\`; MARKET SELL via \`kite_client.place_order\` at trigger time (no next-bar-open in real-time trading)
- **Tests** (~26 total across 5 files): unit + 3-runtime integration + propagation + regression smoke
- **Operator docs** (\`templates/README.md\`): convention note about stop-loss now being enforced everywhere
- **Impact report** (\`docs/research/2026-05-23-stop-loss-enforcement-impact.md\`): RSI(2) v1 + v4 daily before/after numbers

### Headline impact

- **RSI(2) Connors v1**: G4 was -19.89% before fix. After fix: <see impact report>. If passes ≤15% threshold → paper promotion unblocked.
- **v4 daily**: 53.6% win rate baseline gets honest DD numbers for the first time across all runtimes.
- **Live trading safety**: until this PR, live MARKET/LIMIT orders carried NO broker-side stops (Kite client blocks BO/SL/SLM/CO). This closes a meaningful safety gap.

### Backwards compatibility

- Strategies with \`stop_loss_pct: 0\`: no enforcement (feature disabled)
- Strategies with \`stop_loss_pct > 0\` (all 11 templates): stops enforced in all 3 runtimes
- \`OrderIntent.exit_reason: str = "signal"\` default keeps existing AST-emit code unchanged
- Past \`algo.runs\` / \`algo.events\` rows are unaffected — only future backtests include stops

## Test plan

- [x] All new tests pass (~26 tests across 5 files)
- [x] Pre-existing backtest / paper / live tests still pass
- [x] All 11 existing strategy templates still parse cleanly
- [x] RSI(2) v1 re-run captured in impact report
- [x] v4 daily re-run captured in impact report

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Address review, squash-merge per CLAUDE.md §4.4 #27**

---

## Spec-coverage self-review

| Spec section | Plan task |
|---|---|
| §1 Motivation | (background — no task) |
| §2 Scope decisions (all 3 runtimes) | Tasks 3, 4, 5 |
| §3.1 File structure | Tasks 1, 2, 3, 4, 5, 6, 7 |
| §3.2 What stays untouched | (constraint — no task) |
| §3.3 Per-runtime per-bar lifecycle | Tasks 3, 4, 5 (precedence + skip-set in each runtime) |
| §4.1 Module interface | Task 1 |
| §4.2 Trigger formula | Task 1 |
| §4.3 Backtest integration | Task 3 |
| §4.4 Paper integration | Task 4 |
| §4.5 Live integration | Task 5 |
| §4.6 Multi-lot positions | Task 1 test #8 |
| §4.7 Exit-signal shape | Task 2 (propagation) + Tasks 3/4/5 (emission) |
| §5 Test plan | Tasks 1, 2, 3, 4, 5, 6 |
| §5.8 Expected impact | Task 8 (impact report) |
| §6 Promotion & rollout | Task 7 (operator note) + Task 9 (PR announces) |
| §7 Non-goals | Honored (long-only, static-stop, no take-profit, no Kite brackets) |

**Placeholder scan:** Task 8's impact report contains `<fill>` and `<one of: ...>` markers — intentional template fields the implementer fills with actual numbers post-implementation. Consistent with MIS v1 + RSI(2) plan conventions.

**Type consistency check:**
- `StopLossTrigger.avg_price: Decimal` consistent module + tests (Task 1)
- `OrderIntent.exit_reason: str = "signal"` matches `Fill.exit_reason: str = "signal"` matches `Position.exit_reason: str | None` (Task 2)
- All three runtimes call `check_stop_loss_triggers` with the same `open_positions=dict, current_closes=dict, stop_loss_pct=float` keyword signature (Tasks 3, 4, 5)
- Backtest + paper emit `OrderIntent(side="SELL", exit_reason="stop_loss", ...)`; live calls `kite_client.place_order(side="SELL", order_type="MARKET", exit_reason="stop_loss", ...)` — same `exit_reason` value across all three for downstream consistency
- `_run_with_bars` (backtest test), paper test scaffolding, and live test mock setup are explicitly flagged as stand-ins to adapt from existing test patterns (Tasks 3, 4, 5 Step 1 of each)
