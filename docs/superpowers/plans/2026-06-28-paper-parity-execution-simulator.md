# Paper Parity via Shared ExecutionSimulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the paper runtime's trailing exits through Piece A's shared `ExecutionSimulator`, fill paper stop exits at trigger ± slippage, and book fees by the strategy's product — so paper and backtest differ only in bar resolution (paper 1m vs backtest 15m).

**Architecture:** Two tasks. Task 1 extends `PaperBroker` (trigger-price fill + product-aware fees) — pure, directly unit-tested. Task 2 swaps paper's duplicated `_trailing_managers` wrapper for `ExecutionSimulator`, extracting the inline trailing-exit block into a testable `_evaluate_trailing_exit` helper.

**Tech Stack:** Python 3.12, Pydantic v2, pytest. Reuses `ExecutionSimulator`/`ExitDecision`/`TrailingStopManager` from Piece A (already on `dev`). No new deps, no new env var.

## Global Constraints

- Line length ≤ 79 chars; `X | None` not `Optional`; no bare `except`; caught exceptions in the paper run loop log `exc_info=True`.
- Money is `Decimal`; `TrailingStopManager` prices are `float` — convert at the boundary (handled inside `ExecutionSimulator`).
- Reuse existing `ALGO_PAPER_SLIPPAGE_BPS` (BUY pays up / SELL receives less; `bps<=0` no-op). **No new env var.**
- Fee product: `CNC → "DELIVERY"`, `MIS → "INTRADAY"`. Fees computed on the **unslipped** base price.
- Backward compatibility: `PaperBroker.execute` without `trigger_price` and `PaperBroker(...)` without `product` behave exactly as today (defaults preserve entries + flat-stop path).
- Tests live in `backend/algo/paper/tests/`. Host has NO pytest — run in the container:
  `docker compose exec -T backend python -m pytest backend/algo/paper/tests/<file> -v`
- Branch: `feature/paper-parity-execution-sim` (already created off `dev`). Commit after each task.

---

### Task 1: `PaperBroker` — trigger-price fill + product-aware fees

**Files:**
- Modify: `backend/algo/paper/broker.py` (`__init__`, `execute`)
- Test: `backend/algo/paper/tests/test_paper_broker_trigger_fill.py`

**Interfaces:**
- Consumes: `Signal` (paper/types.py: `ticker`, `side: Literal["BUY","SELL"]`, `qty`, `reason: str | None`), `Fill` (backtest/types.py), `IndianFeeModel`, `Trade`, module-level `_slipped_price(price, side)`.
- Produces:
  - `PaperBroker.__init__(self, *, fee_as_of: date, product: str = "DELIVERY")`
  - `PaperBroker.execute(self, *, signal, last_price: Decimal, fill_date: date, trigger_price: Decimal | None = None) -> Fill` — when `trigger_price` is set, fills at `_slipped_price(trigger_price, side)`, fees on unslipped `trigger_price`, and `Fill.trigger_price` is populated. Product comes from `self._product`.

- [ ] **Step 1: Write the failing test**

```python
# backend/algo/paper/tests/test_paper_broker_trigger_fill.py
from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.paper.broker import PaperBroker
from backend.algo.paper.types import Signal


def _sell(qty=10):
    return Signal(
        strategy_id=uuid4(), user_id=uuid4(), ticker="X.NS",
        side="SELL", qty=qty, emitted_at_ns=0, reason="trail_stop",
    )


def test_trigger_fill_uses_trigger_not_last_price(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "0")
    b = PaperBroker(fee_as_of=date(2026, 6, 1), product="DELIVERY")
    fill = b.execute(
        signal=_sell(), last_price=Decimal("90.00"),
        fill_date=date(2026, 6, 1), trigger_price=Decimal("95.00"),
    )
    # fills AT trigger (95), not last_price (90); bps=0 → no slippage
    assert fill.fill_price == Decimal("95.00")
    assert fill.trigger_price == Decimal("95.00")


def test_trigger_fill_applies_sell_slippage(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "100")  # 1%
    b = PaperBroker(fee_as_of=date(2026, 6, 1), product="DELIVERY")
    fill = b.execute(
        signal=_sell(), last_price=Decimal("90.00"),
        fill_date=date(2026, 6, 1), trigger_price=Decimal("100.00"),
    )
    # SELL receives less: 100 * (1 - 0.01) = 99.00
    assert fill.fill_price == Decimal("99.00")


def test_no_trigger_is_unchanged_market_fill(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "0")
    b = PaperBroker(fee_as_of=date(2026, 6, 1))  # default product
    fill = b.execute(
        signal=_sell(), last_price=Decimal("90.00"),
        fill_date=date(2026, 6, 1),
    )
    assert fill.fill_price == Decimal("90.00")
    assert fill.trigger_price is None


def test_mis_books_intraday_fees_below_delivery():
    sig = _sell(qty=100)
    d = PaperBroker(fee_as_of=date(2026, 6, 1), product="DELIVERY")
    i = PaperBroker(fee_as_of=date(2026, 6, 1), product="INTRADAY")
    fd = date(2026, 6, 1)
    df = d.execute(signal=sig, last_price=Decimal("500"), fill_date=fd)
    ifl = i.execute(signal=sig, last_price=Decimal("500"), fill_date=fd)
    # delivery sell STT (~0.1%) > intraday sell STT (~0.025%)
    assert df.fees_inr > ifl.fees_inr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/paper/tests/test_paper_broker_trigger_fill.py -v`
Expected: FAIL — `execute()` got an unexpected keyword `trigger_price` / `__init__` got unexpected `product`.

- [ ] **Step 3: Implement in `backend/algo/paper/broker.py`**

Change `__init__` to accept `product`:

```python
    def __init__(self, *, fee_as_of: date, product: str = "DELIVERY") -> None:
        self._fees = IndianFeeModel(as_of=fee_as_of)
        self._product = product
```

Replace `execute` with the trigger-aware version (keep the docstring intent):

```python
    def execute(
        self,
        *,
        signal: Signal,
        last_price: Decimal,
        fill_date: date,
        trigger_price: Decimal | None = None,
    ) -> Fill:
        """Fill the signal with directional slippage.

        When ``trigger_price`` is set (stop/trailing exit, modelling the
        live GTT), the fill is based on the trigger; otherwise on
        ``last_price``. Fees are computed on the UNSLIPPED base price;
        ``fill_price`` includes the slippage penalty.
        """
        base = trigger_price if trigger_price is not None else last_price
        breakdown = self._fees.compute(
            Trade(
                symbol=signal.ticker,
                exchange="NSE",
                side=signal.side,
                product=self._product,
                qty=signal.qty,
                price=base,
            ),
        )
        fill_price = _slipped_price(base, signal.side)
        return Fill(
            intent_id=uuid4(),
            ticker=signal.ticker,
            side=signal.side,
            qty=signal.qty,
            fill_price=fill_price,
            fill_date=fill_date,
            fees_inr=breakdown.total_inr,
            fee_rates_version=breakdown.rates_version,
            exit_reason=signal.reason or "signal",
            trigger_price=trigger_price,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/paper/tests/test_paper_broker_trigger_fill.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Regression — existing paper suite green**

Run: `docker compose exec -T backend python -m pytest backend/algo/paper/tests/ -v`
Expected: PASS (the default `product="DELIVERY"` + no-`trigger_price` path is byte-identical to before; existing `test_paper_parity.py` slippage tests still pass).

- [ ] **Step 6: Commit**

```bash
git add backend/algo/paper/broker.py backend/algo/paper/tests/test_paper_broker_trigger_fill.py
git commit -m "feat(paper): PaperBroker trigger-price fill + product-aware fees"
```

---

### Task 2: `PaperRuntime` — adopt the shared `ExecutionSimulator`

Replace paper's duplicated `_trailing_managers` wrapper with `ExecutionSimulator`, wire the product into `PaperBroker`, and extract the trailing-exit block into a testable helper that fills at trigger.

**Files:**
- Modify: `backend/algo/paper/runtime.py` (`__init__` ~:194/:303-307, BUY-fill site ~:1129-1155, trailing block ~:685-770, the two `_trailing_managers.pop` sites ~:768/:920, imports)
- Test: `backend/algo/paper/tests/test_paper_parity.py` (append) — or a new `test_paper_exec_sim.py`

**Interfaces:**
- Consumes (from Piece A, on `dev`): `from backend.algo.backtest.execution_simulator import ExecutionSimulator, ExitDecision`. API: `ExecutionSimulator(risk: RiskPerTrade)`, `.on_buy_fill(ticker: str, entry_price: float, atr: float)`, `.drop(ticker: str)`, `.has(ticker: str) -> bool`, `.evaluate_bar(ticker, low: Decimal, high: Decimal) -> ExitDecision | None`. `ExitDecision(ticker, exit_reason: str, trigger_price: Decimal, phase: int, hwm: float)`. Plus Task 1's `PaperBroker(product=...)` + `execute(trigger_price=...)`.
- Produces: `PaperRuntime._evaluate_trailing_exit(self, *, bar, existing_pos, last_price: Decimal, bar_date_obj: date) -> Fill | None` — evaluates `exec_sim` for one bar; on an `ExitDecision`, fills the SELL at trigger via `PaperBroker`, applies the fill, emits budget lifecycle + `order_filled` event (payload `exit_reason`/`trailing_phase`/`trailing_hwm` from the decision), drops the manager, returns the `Fill` (or `None`).

- [ ] **Step 1: Write the failing test**

```python
# append to backend/algo/paper/tests/test_paper_parity.py
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from backend.algo.backtest.execution_simulator import ExecutionSimulator
from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill as _Fill
from backend.algo.paper.broker import PaperBroker
from backend.algo.paper.runtime import PaperRuntime
from backend.algo.strategy.ast import RiskPerTrade


def _risk():
    return RiskPerTrade(
        stop_loss_pct=5.0, max_qty=100,
        trailing_trigger_pct=4.0, trailing_atr_multiplier=1.5,
    )


def _seed_runtime(product="CNC"):
    r = object.__new__(PaperRuntime)
    r._trailing_enabled = True
    r._exec_sim = ExecutionSimulator(_risk())
    r._broker = PaperBroker(
        fee_as_of=date(2026, 6, 1),
        product="DELIVERY" if product == "CNC" else "INTRADAY",
    )
    r._positions = PositionTracker()
    r._strategy = SimpleNamespace(
        id=uuid4(), risk=SimpleNamespace(per_trade=_risk()),
        product=product,
    )
    r._user_id = uuid4()
    r._session_id = uuid4()
    r._events = []
    r._last_marks = {}
    return r


def _bar(ticker, low, high, ts):
    return SimpleNamespace(
        ticker=ticker, low=Decimal(str(low)), high=Decimal(str(high)),
        close=Decimal(str(high)), bar_open_ts_ns=ts,
    )


def test_paper_trailing_exit_fills_at_trigger(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "0")
    r = _seed_runtime(product="CNC")
    # open a 10-share position @100 via a BUY fill
    buy = _Fill(
        intent_id=uuid4(), ticker="X.NS", side="BUY", qty=10,
        fill_price=Decimal("100"), fill_date=date(2026, 6, 1),
        fees_inr=Decimal("0"), fee_rates_version="t",
    )
    r._positions.apply_fill(buy)
    r._exec_sim.on_buy_fill("X.NS", 100.0, atr=1.0)
    pos = r._positions.open_positions()["X.NS"]
    # bar dips below the 5% hard stop (95) -> STOP_HIT at 95
    fill = r._evaluate_trailing_exit(
        bar=_bar("X.NS", 94, 96, ts=1),
        existing_pos=pos, last_price=Decimal("94"),
        bar_date_obj=date(2026, 6, 1),
    )
    assert fill is not None
    assert fill.side == "SELL"
    assert fill.fill_price == Decimal("95.0")        # filled AT trigger
    assert fill.exit_reason == "phase1_stop"
    assert not r._exec_sim.has("X.NS")               # manager dropped
    assert any(
        e["type"] == "order_filled"
        and e["payload"]["exit_reason"] == "phase1_stop"
        for e in r._events
    )


def test_paper_trailing_decision_matches_backtest_exec_sim():
    # Same bar series through a standalone ExecutionSimulator (the
    # backtest reference) and the paper path -> identical decision.
    ref = ExecutionSimulator(_risk())
    ref.on_buy_fill("X.NS", 100.0, atr=1.0)
    ref_dec = ref.evaluate_bar("X.NS", Decimal("94"), Decimal("96"))

    r = _seed_runtime()
    r._positions.apply_fill(_Fill(
        intent_id=uuid4(), ticker="X.NS", side="BUY", qty=10,
        fill_price=Decimal("100"), fill_date=date(2026, 6, 1),
        fees_inr=Decimal("0"), fee_rates_version="t"))
    r._exec_sim.on_buy_fill("X.NS", 100.0, atr=1.0)
    fill = r._evaluate_trailing_exit(
        bar=_bar("X.NS", 94, 96, ts=1),
        existing_pos=r._positions.open_positions()["X.NS"],
        last_price=Decimal("94"), bar_date_obj=date(2026, 6, 1))
    assert ref_dec is not None
    assert fill.exit_reason == ref_dec.exit_reason
    assert fill.trigger_price == ref_dec.trigger_price


def test_no_exit_when_bar_above_stop():
    r = _seed_runtime()
    r._exec_sim.on_buy_fill("X.NS", 100.0, atr=1.0)
    r._positions.apply_fill(_Fill(
        intent_id=uuid4(), ticker="X.NS", side="BUY", qty=10,
        fill_price=Decimal("100"), fill_date=date(2026, 6, 1),
        fees_inr=Decimal("0"), fee_rates_version="t"))
    fill = r._evaluate_trailing_exit(
        bar=_bar("X.NS", 99, 104, ts=1),
        existing_pos=r._positions.open_positions()["X.NS"],
        last_price=Decimal("99"), bar_date_obj=date(2026, 6, 1))
    assert fill is None
    assert r._exec_sim.has("X.NS")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/paper/tests/test_paper_parity.py -k "trigger or exec_sim or above_stop" -v`
Expected: FAIL — `PaperRuntime` has no `_exec_sim` / no `_evaluate_trailing_exit`.

- [ ] **Step 3: Wire `ExecutionSimulator` + product into `__init__`**

In `runtime.py`, add the import near the other backtest imports:

```python
from backend.algo.backtest.execution_simulator import (
    ExecutionSimulator,
)
```

At the broker construction (`~:194`):

```python
        self._broker = PaperBroker(
            fee_as_of=fee_as_of,
            product="DELIVERY" if strategy.product == "CNC" else "INTRADAY",
        )
```

Replace the `self._trailing_managers: dict[...] = {}` declaration (`~:307`) with:

```python
        self._exec_sim = ExecutionSimulator(strategy.risk.per_trade)
```

Remove the now-unused `TrailingStopManager` import if nothing else references it (grep `TrailingStopManager` in the file after editing; the only uses were the manager dict + creation site, both replaced).

- [ ] **Step 4: Migrate the BUY-fill manager creation (`~:1129-1155`)**

Replace the `TrailingStopManager(...)` construction block with the simulator call (keep the ATR computation verbatim):

```python
        if self._trailing_enabled and fill.side == "BUY":
            _bars_up = [b for b in history if b.date <= bar_date_obj]
            _atr_series = _wilder_atr(_bars_up, 14)
            _atr = float(
                _atr_series[-1]
                if _atr_series and _atr_series[-1] is not None
                else 0.0
            )
            if _atr > 0 and not self._exec_sim.has(fill.ticker):
                self._exec_sim.on_buy_fill(
                    fill.ticker, float(fill.fill_price), _atr
                )
            else:
                _logger.warning(
                    "paper trailing: missing atr_14 for %s — "
                    "no trailing manager created",
                    fill.ticker,
                )
```

- [ ] **Step 5: Extract `_evaluate_trailing_exit` and call it from `_on_bar_close`**

Add the helper method on `PaperRuntime`:

```python
    def _evaluate_trailing_exit(
        self,
        *,
        bar,  # noqa: ANN001 — resampler bar (duck-typed)
        existing_pos,  # noqa: ANN001
        last_price: Decimal,
        bar_date_obj: date,
    ) -> "Fill | None":
        """Evaluate the shared ExecutionSimulator for one bar; on an
        ExitDecision, fill the SELL at the trigger and emit events."""
        if not (self._trailing_enabled and self._exec_sim.has(bar.ticker)):
            return None
        dec = self._exec_sim.evaluate_bar(bar.ticker, bar.low, bar.high)
        if dec is None:
            return None
        sig = Signal(
            strategy_id=self._strategy.id,
            user_id=self._user_id,
            ticker=bar.ticker,
            side="SELL",
            qty=existing_pos.qty,
            emitted_at_ns=bar.bar_open_ts_ns,
            reason=dec.exit_reason,
        )
        try:
            fill = self._broker.execute(
                signal=sig,
                last_price=last_price,
                fill_date=bar_date_obj,
                trigger_price=dec.trigger_price,
            )
        except Exception as exc:
            _logger.error(
                "paper trailing fill failed for %s: %s",
                bar.ticker, exc, exc_info=True,
            )
            return None
        self._positions.apply_fill(fill)
        _emit_paper_budget_lifecycle(
            user_id=self._user_id,
            strategy_id=self._strategy.id,
            fill=fill,
        )
        self._events.append(
            event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="paper",
                type_="order_filled",
                payload={
                    "ticker": fill.ticker,
                    "side": fill.side,
                    "qty": fill.qty,
                    "fill_price": str(fill.fill_price),
                    "fill_date": fill.fill_date.isoformat(),
                    "fees_inr": str(fill.fees_inr),
                    "fee_rates_version": fill.fee_rates_version,
                    "exit_reason": dec.exit_reason,
                    "trailing_phase": dec.phase,
                    "trailing_hwm": dec.hwm,
                    "trigger_price": str(dec.trigger_price),
                },
            )
        )
        self._exec_sim.drop(bar.ticker)
        return fill
```

In `_on_bar_close`, replace the old inline trailing block (`~:685-770`) with a call. Preserve the surrounding flow (e.g. the cooldown-hydration call that ran after a trailing exit):

```python
            _ts_fill = self._evaluate_trailing_exit(
                bar=bar, existing_pos=existing_pos,
                last_price=last_price, bar_date_obj=bar_date_obj,
            )
            if _ts_fill is not None:
                fills += 1
                # (keep the existing cooldown-hydration call that
                #  followed the old block, unchanged)
```

- [ ] **Step 6: Migrate the flat-stop `_trailing_managers.pop` site (`~:920`)**

In the flat %-stop path, replace `self._trailing_managers.pop(trig.ticker, None)` with:

```python
                if self._trailing_enabled:
                    self._exec_sim.drop(trig.ticker)
```

(The pop inside the old trailing block at `~:768` is now handled by `_evaluate_trailing_exit`’s `self._exec_sim.drop(...)`.)

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `docker compose exec -T backend python -m pytest backend/algo/paper/tests/test_paper_parity.py -k "trigger or exec_sim or above_stop" -v`
Expected: PASS (3 new tests).

- [ ] **Step 8: Full paper-suite regression**

Run: `docker compose exec -T backend python -m pytest backend/algo/paper/tests/ -v`
Expected: PASS — trailing-disabled paper (flat-stop path) and entries unchanged; no `_trailing_managers` references remain (grep to confirm: `grep -n _trailing_managers backend/algo/paper/runtime.py` → no output).

- [ ] **Step 9: Commit**

```bash
git add backend/algo/paper/runtime.py backend/algo/paper/tests/test_paper_parity.py
git commit -m "feat(paper): adopt shared ExecutionSimulator; trigger-fill trailing exits"
```

---

## Self-Review

**Spec coverage:**
- §4.1 paper uses ExecutionSimulator (drop `_trailing_managers`) → Task 2 Steps 3–6. ✓
- §4.1 on_buy_fill with same ATR → Task 2 Step 4. ✓
- §4.1 per-bar evaluate_bar → SELL at trigger → Task 2 Step 5. ✓
- §4.1 preserve flat-stop / other exits → Task 2 Step 6 + regression Step 8. ✓
- §4.2 PaperBroker trigger fill + product → Task 1. ✓
- §4.3 decision→fill mapping, phase mapping lives in ExecutionSimulator → Task 2 (no phase mapping in paper). ✓
- §5 parity test (same bars → identical decision) → Task 2 Step 1 `test_paper_trailing_decision_matches_backtest_exec_sim`. ✓
- §5 trigger fill + fee product tests → Task 1. ✓
- §6 backward compat (defaults) → Task 1 `test_no_trigger_is_unchanged_market_fill` + regression. ✓

**Placeholder scan:** none — all steps carry real code/commands.

**Type consistency:** `ExecutionSimulator`/`ExitDecision` API matches Piece A; `PaperBroker.execute(trigger_price=...)` (Task 1) is consumed by `_evaluate_trailing_exit` (Task 2); `Signal` fields match paper/types.py; `Fill.trigger_price` exists (Piece A Task 2).

**Implementation-time note (Task 2):** the exact `existing_pos`/`history`/`last_price` variable names in `_on_bar_close` must be confirmed against the live file when extracting the helper; the BUY-fill site already exposes `history` and `bar_date_obj`. If a name differs, adapt the call site (don't rename the helper's params).
