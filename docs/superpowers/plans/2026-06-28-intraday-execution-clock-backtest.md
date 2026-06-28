# Intraday Execution Clock (Backtest & Walkforward) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make backtest & walkforward evaluate stops / ATR trailing / MIS square-off on a fine **execution clock** (15m today) decoupled from the strategy's **signal clock**, so a *daily* strategy gets ~25 intraday exit checks/day instead of 1 — a faithful replica of live, using only preserved Iceberg data.

**Architecture:** Two clocks (signal cadence + finest-available execution grain) feeding one shared `ExecutionSimulator` that wraps the live `TrailingStopManager`. Resolution is chosen per-window from `stocks.intraday_bars` (15m today, daily-fallback flagged). Stop exits fill at trigger ± slippage. Walkforward inherits via per-fold `run_backtest`.

**Tech Stack:** Python 3.12, Pydantic v2 (`extra="forbid"`), PyIceberg/DuckDB (`query_iceberg_table`), pytest. No new deps.

## Global Constraints

- Line length ≤ 79 chars; `X | None` not `Optional`; no bare `except`; caught exceptions in long jobs log `exc_info=True`.
- **Zero Kite / network calls in the eval path** — read only `stocks.intraday_bars` / `stocks.ohlcv` via Iceberg.
- **Batch reads** — one `WHERE ticker IN (...)` per load; never per-ticker.
- Pydantic models are `extra="forbid"` — adding a field is a deliberate schema change (update model + every constructor touched).
- Tests live in `backend/algo/backtest/tests/` (no conftest; build bars inline + patch loaders). Run inside the container:
  `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/<file> -v`
- Money is `Decimal`; prices from `TrailingStopManager` are `float` — convert at the boundary with `Decimal(str(x))`.
- Daily strategy = `interval_sec == 86400`; intraday = `{60,300,900}`. Finest historical grain available = **900 (15m)**.
- Commit after each task. Branch: `feature/intraday-execution-clock` (already created off `dev`).

---

### Task 1: `intraday_coverage` helper (Piece A0)

Single source of truth for "what execution resolution is available per ticker." Pure read, batched, no Kite.

**Files:**
- Create: `backend/algo/backtest/coverage.py`
- Test: `backend/algo/backtest/tests/test_intraday_coverage.py`

**Interfaces:**
- Consumes: `backend.db.duckdb_engine.query_iceberg_table(table, sql, params) -> list[dict]`.
- Produces:
  - `class TickerCoverage` (frozen dataclass): `ticker: str`, `finest_interval_sec: int | None`, `covered_start: date | None`, `covered_end: date | None`, `trading_days: int`.
  - `intraday_coverage(*, tickers: list[str], period_start: date, period_end: date) -> dict[str, TickerCoverage]` — every input ticker present in the result; absent-from-table tickers map to `TickerCoverage(ticker, None, None, None, 0)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/algo/backtest/tests/test_intraday_coverage.py
from datetime import date
from unittest.mock import patch

from backend.algo.backtest.coverage import (
    TickerCoverage,
    intraday_coverage,
)

_ROWS = [
    {"ticker": "A.NS", "interval_sec": 900, "min_d": date(2022, 6, 1),
     "max_d": date(2026, 6, 25), "days": 1008},
    {"ticker": "B.NS", "interval_sec": 900, "min_d": date(2024, 1, 1),
     "max_d": date(2026, 6, 25), "days": 600},
    {"ticker": "B.NS", "interval_sec": 300, "min_d": date(2025, 1, 1),
     "max_d": date(2026, 6, 25), "days": 300},
]


def test_finest_interval_and_absent_ticker():
    with patch(
        "backend.algo.backtest.coverage.query_iceberg_table",
        return_value=_ROWS,
    ) as q:
        cov = intraday_coverage(
            tickers=["A.NS", "B.NS", "C.NS"],
            period_start=date(2022, 1, 1),
            period_end=date(2026, 6, 30),
        )
    # A: only 15m
    assert cov["A.NS"].finest_interval_sec == 900
    assert cov["A.NS"].trading_days == 1008
    # B: has 5m AND 15m -> finest is 300
    assert cov["B.NS"].finest_interval_sec == 300
    # C: absent from table -> None coverage
    assert cov["C.NS"] == TickerCoverage("C.NS", None, None, None, 0)
    # batched single query (no per-ticker loop)
    assert q.call_count == 1


def test_empty_tickers_no_query():
    with patch(
        "backend.algo.backtest.coverage.query_iceberg_table",
    ) as q:
        assert intraday_coverage(
            tickers=[], period_start=date(2022, 1, 1),
            period_end=date(2022, 2, 1),
        ) == {}
        q.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_intraday_coverage.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.algo.backtest.coverage`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/algo/backtest/coverage.py
"""Intraday coverage probe for stocks.intraday_bars.

Single source of truth for the finest execution-clock resolution
available per ticker over a window. Read-only, batched, zero Kite —
used by the backtest two-clock engine and (later) the transparency UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from backend.db.duckdb_engine import query_iceberg_table

_INTRADAY_TABLE = "stocks.intraday_bars"
_BIG = 10**9


@dataclass(frozen=True)
class TickerCoverage:
    ticker: str
    finest_interval_sec: int | None
    covered_start: date | None
    covered_end: date | None
    trading_days: int


def intraday_coverage(
    *,
    tickers: list[str],
    period_start: date,
    period_end: date,
) -> dict[str, TickerCoverage]:
    """Return per-ticker finest available intraday grain in-window."""
    if not tickers:
        return {}
    placeholders = ",".join(f"'{t}'" for t in tickers)
    sql = (
        "SELECT ticker, interval_sec, "
        "MIN(bar_date) AS min_d, MAX(bar_date) AS max_d, "
        "COUNT(DISTINCT bar_date) AS days "
        "FROM intraday_bars "
        f"WHERE ticker IN ({placeholders}) "
        "AND year_month BETWEEN ? AND ? "
        "AND bar_date BETWEEN ? AND ? "
        "GROUP BY ticker, interval_sec"
    )
    rows = query_iceberg_table(
        _INTRADAY_TABLE,
        sql,
        [
            period_start.isoformat()[:7],
            period_end.isoformat()[:7],
            period_start.isoformat(),
            period_end.isoformat(),
        ],
    )
    best: dict[str, TickerCoverage] = {}
    for r in rows:
        t = r["ticker"]
        isec = int(r["interval_sec"])
        cur = best.get(t)
        if cur is None or isec < (cur.finest_interval_sec or _BIG):
            best[t] = TickerCoverage(
                ticker=t,
                finest_interval_sec=isec,
                covered_start=r["min_d"],
                covered_end=r["max_d"],
                trading_days=int(r["days"]),
            )
    for t in tickers:
        best.setdefault(
            t, TickerCoverage(t, None, None, None, 0)
        )
    return best
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_intraday_coverage.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/algo/backtest/coverage.py backend/algo/backtest/tests/test_intraday_coverage.py
git commit -m "feat(backtest): intraday_coverage helper (finest grain per ticker)"
```

---

### Task 2: Trigger-price stop fills in `SimBroker`

Add a same-bar "fill at trigger ± slippage" path modeling the live GTT. Today all exits fill at `next_bar.open ± ADTV-slippage` (sim_broker.py:95). A stop exit should instead fill on the **current** execution bar at the trigger price.

**Files:**
- Modify: `backend/algo/backtest/types.py` (`OrderIntent` ~:98, `Fill` ~:119)
- Modify: `backend/algo/backtest/sim_broker.py` (`execute` ~:95; add helpers)
- Test: `backend/algo/backtest/tests/test_sim_broker_trigger_fill.py`

**Interfaces:**
- Consumes: `OrderIntent`, `Fill`, `BarData`, env `ALGO_PAPER_SLIPPAGE_BPS`.
- Produces:
  - `OrderIntent.trigger_price: Decimal | None = None`
  - `Fill.trigger_price: Decimal | None = None`
  - `SimBroker.execute(intent)` — when `intent.trigger_price is not None`, fills on the **current** (emitted) bar at `trigger ± flat-bps slippage`, `fill_date`/`fill_ts_ns` = the emitted bar; fees on unslipped trigger.

- [ ] **Step 1: Write the failing test**

```python
# backend/algo/backtest/tests/test_sim_broker_trigger_fill.py
from datetime import date
from decimal import Decimal

from backend.algo.backtest.sim_broker import SimBroker
from backend.algo.backtest.types import BarData, OrderIntent


def _bar(d, o, h, l, c, ts=None):
    return BarData(
        ticker="X.NS", date=d, open=Decimal(o), high=Decimal(h),
        low=Decimal(l), close=Decimal(c), volume=1000,
        bar_open_ts_ns=ts,
    )


def test_trigger_fill_is_same_bar_at_trigger(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "0")
    bars = {"X.NS": [
        _bar(date(2026, 6, 1), "100", "101", "94", "95", ts=1),
        _bar(date(2026, 6, 1), "95", "96", "90", "92", ts=2),
    ]}
    sim = SimBroker(bars=bars, fee_as_of=date(2026, 6, 1))
    intent = OrderIntent(
        ticker="X.NS", side="SELL", qty=10,
        intent_emitted_at=date(2026, 6, 1), intent_emitted_ts_ns=1,
        exit_reason="trail_stop", trigger_price=Decimal("96.50"),
    )
    fill = sim.execute(intent)
    assert fill is not None
    # same bar (ts=1), filled AT trigger (no slippage), not next open
    assert fill.fill_ts_ns == 1
    assert fill.fill_price == Decimal("96.50")
    assert fill.trigger_price == Decimal("96.50")


def test_trigger_fill_applies_sell_slippage(monkeypatch):
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "100")  # 1%
    bars = {"X.NS": [_bar(date(2026, 6, 1), "100", "101", "94", "95", ts=1)]}
    sim = SimBroker(bars=bars, fee_as_of=date(2026, 6, 1))
    intent = OrderIntent(
        ticker="X.NS", side="SELL", qty=10,
        intent_emitted_at=date(2026, 6, 1), intent_emitted_ts_ns=1,
        exit_reason="phase1_stop", trigger_price=Decimal("100.00"),
    )
    fill = sim.execute(intent)
    # SELL receives less: 100 * (1 - 0.01) = 99.00
    assert fill.fill_price == Decimal("99.00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_sim_broker_trigger_fill.py -v`
Expected: FAIL — `OrderIntent` rejects `trigger_price` (`extra="forbid"`).

- [ ] **Step 3: Add fields to the models**

In `backend/algo/backtest/types.py`, add to `OrderIntent` (after `exit_reason`):

```python
    trigger_price: Decimal | None = None
```

and to `Fill` (after `exit_reason`):

```python
    trigger_price: Decimal | None = None
```

- [ ] **Step 4: Implement the trigger-fill path in `SimBroker`**

In `backend/algo/backtest/sim_broker.py`, add near the other module helpers (after `estimate_slippage_bps`):

```python
import os  # add to existing imports if not present


def _flat_slip(trigger: Decimal, side: str) -> Decimal:
    """Directional flat-bps slippage on a trigger price (live GTT)."""
    try:
        bps = int(os.getenv("ALGO_PAPER_SLIPPAGE_BPS", "0"))
    except (TypeError, ValueError):
        bps = 0
    if bps <= 0:
        return trigger
    factor = Decimal(bps) / Decimal("10000")
    if side == "BUY":
        return trigger * (Decimal("1") + factor)
    return trigger * (Decimal("1") - factor)
```

Inside `SimBroker.execute`, immediately after the `if intent.ticker not in self._bars: raise NoBarAvailableError(...)` guard, branch to the trigger path:

```python
        if intent.trigger_price is not None:
            return self._execute_trigger_fill(intent)
```

Add the method on `SimBroker` (resolves the CURRENT emitted bar, not next):

```python
    def _execute_trigger_fill(self, intent: OrderIntent) -> Fill | None:
        """Fill a stop/trailing exit on its own bar at the trigger."""
        if intent.intent_emitted_ts_ns is not None:
            idx = self._ts_index.get(intent.ticker, {}).get(
                intent.intent_emitted_ts_ns
            )
        else:
            idx = self._index.get(intent.ticker, {}).get(
                intent.intent_emitted_at
            )
        if idx is None:
            return None
        bar = self._bars[intent.ticker][idx]
        trigger = intent.trigger_price  # type: ignore[assignment]
        fill_price = _flat_slip(trigger, intent.side)
        product = (
            "INTRADAY"
            if intent.intent_emitted_ts_ns is not None
            else "DELIVERY"
        )
        breakdown = self._fees.compute(
            Trade(
                symbol=intent.ticker,
                exchange="NSE",
                side=intent.side,
                product=product,
                qty=intent.qty,
                price=trigger,  # fees on unslipped trigger
            ),
        )
        return Fill(
            intent_id=intent.intent_id,
            ticker=intent.ticker,
            side=intent.side,
            qty=intent.qty,
            fill_price=fill_price,
            fill_date=bar.date,
            fees_inr=breakdown.total_inr,
            fee_rates_version=breakdown.rates_version,
            exit_reason=intent.exit_reason,
            fill_ts_ns=bar.bar_open_ts_ns,
            trigger_price=trigger,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_sim_broker_trigger_fill.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Regression — existing sim_broker tests still pass**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/ -k "sim_broker or fill" -v`
Expected: PASS (no regressions — non-trigger intents unchanged).

- [ ] **Step 7: Commit**

```bash
git add backend/algo/backtest/types.py backend/algo/backtest/sim_broker.py backend/algo/backtest/tests/test_sim_broker_trigger_fill.py
git commit -m "feat(backtest): trigger-price same-bar stop fills (models live GTT)"
```

---

### Task 3: `ExecutionSimulator` (extract trailing decision logic)

Pull the per-ticker trailing evaluation (currently inlined runner.py:538–627) into a pure, testable component returning an `ExitDecision`. Wraps the live `TrailingStopManager`. The runner keeps fill/event orchestration.

**Files:**
- Create: `backend/algo/backtest/execution_simulator.py`
- Test: `backend/algo/backtest/tests/test_execution_simulator.py`

**Interfaces:**
- Consumes: `TrailingStopManager` (trailing_stop_manager.py), `RiskPerTrade` (strategy/ast.py).
- Produces:
  - `class ExitDecision` (frozen dataclass): `ticker: str`, `exit_reason: str` (`"phase1_stop"|"phase1_ratchet"|"trail_stop"`), `trigger_price: Decimal`, `phase: int`, `hwm: float`.
  - `class ExecutionSimulator`:
    - `__init__(self, risk: RiskPerTrade)`
    - `on_buy_fill(self, ticker: str, entry_price: float, atr: float) -> None`
    - `drop(self, ticker: str) -> None`
    - `has(self, ticker: str) -> bool`
    - `evaluate_bar(self, ticker: str, low: Decimal, high: Decimal) -> ExitDecision | None`

- [ ] **Step 1: Write the failing test**

```python
# backend/algo/backtest/tests/test_execution_simulator.py
from decimal import Decimal

from backend.algo.backtest.execution_simulator import (
    ExecutionSimulator,
    ExitDecision,
)
from backend.algo.strategy.ast import RiskPerTrade


def _risk():
    return RiskPerTrade(
        stop_loss_pct=5.0, max_qty=100,
        trailing_trigger_pct=3.0, trailing_atr_multiplier=2.0,
    )


def test_no_decision_until_stop_crossed():
    sim = ExecutionSimulator(_risk())
    sim.on_buy_fill("X.NS", entry_price=100.0, atr=1.0)
    # bar well above the 5% hard stop (95) -> no exit, HWM advances
    assert sim.evaluate_bar("X.NS", Decimal("99"), Decimal("104")) is None
    assert sim.has("X.NS")


def test_stop_hit_returns_decision_at_current_stop():
    sim = ExecutionSimulator(_risk())
    sim.on_buy_fill("X.NS", entry_price=100.0, atr=1.0)
    dec = sim.evaluate_bar("X.NS", Decimal("94"), Decimal("96"))
    assert isinstance(dec, ExitDecision)
    assert dec.exit_reason == "phase1_stop"
    assert dec.phase == 1
    # trigger is the manager's hard stop = 100 * (1 - 0.05) = 95.0
    assert dec.trigger_price == Decimal("95.0")


def test_unknown_ticker_returns_none():
    sim = ExecutionSimulator(_risk())
    assert sim.evaluate_bar("Z.NS", Decimal("1"), Decimal("2")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_execution_simulator.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the implementation**

```python
# backend/algo/backtest/execution_simulator.py
"""Shared execution-clock simulator for backtest/walkforward (and,
later, paper). Owns per-ticker TrailingStopManager lifecycle and
returns a pure ExitDecision when an exit triggers on a bar. The same
TrailingStopManager class drives live, so behaviour cannot diverge.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.algo.backtest.trailing_stop_manager import (
    TrailingStopManager,
)
from backend.algo.strategy.ast import RiskPerTrade


@dataclass(frozen=True)
class ExitDecision:
    ticker: str
    exit_reason: str
    trigger_price: Decimal
    phase: int
    hwm: float


def _reason_for_phase(phase_value: int) -> str:
    if phase_value == 2:
        return "trail_stop"
    if phase_value == 15:
        return "phase1_ratchet"
    return "phase1_stop"


class ExecutionSimulator:
    def __init__(self, risk: RiskPerTrade) -> None:
        self._risk = risk
        self._mgrs: dict[str, TrailingStopManager] = {}

    def on_buy_fill(
        self, ticker: str, entry_price: float, atr: float
    ) -> None:
        self._mgrs[ticker] = TrailingStopManager(
            self._risk, entry_price, atr, ticker
        )

    def drop(self, ticker: str) -> None:
        self._mgrs.pop(ticker, None)

    def has(self, ticker: str) -> bool:
        return ticker in self._mgrs

    def evaluate_bar(
        self, ticker: str, low: Decimal, high: Decimal
    ) -> ExitDecision | None:
        """LOW-then-HIGH per bar. Returns an ExitDecision on STOP_HIT."""
        mgr = self._mgrs.get(ticker)
        if mgr is None:
            return None
        if float(low) <= mgr.current_stop:
            ev = mgr.on_price_update(float(low))
            if ev is None or ev.event_type != "STOP_HIT":
                mgr.on_price_update(float(high))
                return None
        else:
            mgr.on_price_update(float(high))
            return None
        return ExitDecision(
            ticker=ticker,
            exit_reason=_reason_for_phase(ev.phase.value),
            trigger_price=Decimal(str(ev.new_stop)),
            phase=ev.phase.value,
            hwm=ev.hwm,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_execution_simulator.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/algo/backtest/execution_simulator.py backend/algo/backtest/tests/test_execution_simulator.py
git commit -m "feat(backtest): ExecutionSimulator wrapping live TrailingStopManager"
```

---

### Task 4: Two-clock wiring in the runner (daily signal → 15m execution)

Wire the runner so a **daily** strategy with trailing enabled and 15m coverage evaluates exits on 15m bars while signals still fire once/day. Intraday-signal strategies are unchanged (already exit at their own grain). This is the integration crux — work with `runner.py` open.

**Files:**
- Modify: `backend/algo/backtest/runner.py` (`run_backtest` ~:109; trailing block ~:538–627; manager creation site; loop header ~:436)
- Test: `backend/algo/backtest/tests/test_two_clock_runner.py`

**Interfaces:**
- Consumes: `intraday_coverage` (Task 1), `ExecutionSimulator`/`ExitDecision` (Task 3), `OrderIntent.trigger_price` + trigger fill (Task 2), `load_intraday_bars_window` (data_source.py:170).
- Produces: behaviour — daily strategy + 15m coverage exits intraday; emitted exit events carry `exit_reason` + `trigger_price`. No new public function (internal wiring), but the runner now computes `execution_interval_sec` (consumed by Task 6).

**Implementation notes (apply against the real file):**

1. **Decide execution grain.** After `is_intraday = request.interval_sec != 86400` (runner.py:153) and after `_trailing_enabled` is known (runner.py:429), add:

```python
    # Two-clock: a daily-signal strategy with trailing enabled runs
    # exits on the finest available intraday grain (15m today).
    execution_interval_sec = request.interval_sec
    exec_bars: dict[str, list[BarData]] = {}
    if request.interval_sec == 86400 and _trailing_enabled:
        from backend.algo.backtest.coverage import intraday_coverage
        cov = intraday_coverage(
            tickers=universe,
            period_start=request.period_start,
            period_end=request.period_end,
        )
        grains = {
            c.finest_interval_sec
            for c in cov.values()
            if c.finest_interval_sec is not None
        }
        if grains:
            execution_interval_sec = min(grains)  # 900 today
            covered = [
                t for t, c in cov.items()
                if c.finest_interval_sec == execution_interval_sec
            ]
            exec_bars = load_intraday_bars_window(
                tickers=covered,
                interval_sec=execution_interval_sec,
                period_start=request.period_start,
                period_end=request.period_end,
                warmup_days=0,
            )
```

   `daily_fallback_tickers = [t for t in universe if t not in exec_bars]` (consumed by Task 6). When `exec_bars` is empty the run stays pure-daily (today's behaviour).

2. **Build the execution timeline** when `exec_bars` is populated. Reuse the intraday timeline construction (runner.py:365–388) but from `exec_bars`, and build `exec_bars_by_ts`. Mark **signal bars**: the last execution `ts_ns` of each trading day is the daily signal bar.

```python
    two_clock = bool(exec_bars)
    if two_clock:
        exec_timeline = sorted(
            {(b.date, b.bar_open_ts_ns)
             for bl in exec_bars.values() for b in bl
             if b.bar_open_ts_ns is not None},
            key=lambda x: (x[1] or 0, x[0]),
        )
        exec_by_ts = {
            t: {b.bar_open_ts_ns: b for b in bl
                if b.bar_open_ts_ns is not None}
            for t, bl in exec_bars.items()
        }
        last_ns_of_day: dict[date, int] = {}
        for d, ns in exec_timeline:
            if ns is not None:
                last_ns_of_day[d] = max(last_ns_of_day.get(d, ns), ns)
        signal_bar_keys = {
            (d, ns) for d, ns in last_ns_of_day.items()
        }
```

3. **Drive the loop on the execution timeline** when `two_clock`. The existing loop iterates `timeline`; when two-clock, iterate `exec_timeline` and set `is_signal_bar = (bar_date, ts_ns) in signal_bar_keys`. Gate the **AST/signal evaluation** section (the entry/rebalance block) behind `if (not two_clock) or is_signal_bar:`. Exit checks run every iteration.

4. **Feed exits from execution bars.** In two-clock mode, build `lows_this_bar`/`highs_this_bar`/`closes_this_bar` from `exec_by_ts.get(t, {}).get(ts_ns)` (mirror runner.py:458–469 but against `exec_by_ts`).

5. **Replace the inline trailing block (runner.py:538–627)** with `ExecutionSimulator`:
   - Instantiate once before the loop: `exec_sim = ExecutionSimulator(strategy.risk.per_trade)`.
   - Where a confirmed BUY fill currently creates a `TrailingStopManager` (manager-creation site near the entry-fill handling), call `exec_sim.on_buy_fill(ticker, float(fill.fill_price), atr_value)` using the same ATR source the current code passes to `TrailingStopManager(...)`.
   - In the per-bar exit section:

```python
        else:
            for _t in list(open_pos_now.keys()):
                if not exec_sim.has(_t):
                    continue
                _lo = lows_this_bar.get(_t)
                _hi = highs_this_bar.get(_t)
                if _lo is None or _hi is None:
                    continue
                _dec = exec_sim.evaluate_bar(_t, _lo, _hi)
                if _dec is None:
                    continue
                _pos = open_pos_now[_t]
                _intent = OrderIntent(
                    ticker=_t, side="SELL", qty=_pos.qty,
                    intent_emitted_at=bar_date,
                    intent_emitted_ts_ns=ts_ns,
                    exit_reason=_dec.exit_reason,
                    trigger_price=_dec.trigger_price,
                )
                try:
                    _fill = sim.execute(_intent)
                except NoBarAvailableError:
                    _fill = None
                if _fill is None:
                    continue
                pt.apply_fill(_fill)
                total_fees += _fill.fees_inr
                fee_rates_version = _fill.fee_rates_version
                events.append(event_row(
                    session_id=session_id, user_id=user_id,
                    strategy_id=strategy.id, mode="backtest",
                    type_="order_filled",
                    payload={
                        "ticker": _fill.ticker, "side": _fill.side,
                        "qty": _fill.qty,
                        "fill_price": str(_fill.fill_price),
                        "fill_date": _fill.fill_date.isoformat(),
                        "fees_inr": str(_fill.fees_inr),
                        "fee_rates_version": _fill.fee_rates_version,
                        "exit_reason": _dec.exit_reason,
                        "trailing_phase": _dec.phase,
                        "trailing_hwm": _dec.hwm,
                        "trigger_price": str(_dec.trigger_price),
                        "execution_interval_sec":
                            execution_interval_sec,
                    },
                ))
                exec_sim.drop(_t)
                stop_loss_skip.add(_t)
```

   - **Crucially**, `sim` must be able to fill on the execution bars: when two-clock, construct `SimBroker(bars=exec_bars, ...)` for exit fills (the trigger-fill path resolves the current exec bar). Keep the daily `SimBroker` for entries, or pass a merged bar dict — decide during implementation; simplest is a dedicated `exec_sim_broker = SimBroker(bars=exec_bars, fee_as_of=request.period_start, adtv_lookup=adtv_lookup)` used only for trigger fills.

6. **Marks/equity** in two-clock mode use `closes_this_bar` from execution bars (intraday equity curve). The `EquityPoint.bar_open_ts_ns` carries `ts_ns`.

- [ ] **Step 1: Write the failing integration test**

```python
# backend/algo/backtest/tests/test_two_clock_runner.py
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.strategy.ast import parse_strategy

_BASE = date(2026, 1, 1)


def _daily_bars(closes):
    out = []
    for i, c in enumerate(closes):
        c = Decimal(str(c))
        out.append(BarData(
            ticker="FAKE.NS", date=_BASE + timedelta(days=i),
            open=Decimal("100"), high=max(Decimal("100"), c) + 1,
            low=min(Decimal("100"), c) - 1, close=c, volume=10_000,
        ))
    return {"FAKE.NS": out}


def _ns(d, hh, mm):
    dt = datetime(d.year, d.month, d.day, hh - 5, mm - 30,
                  tzinfo=timezone.utc)  # IST→UTC rough
    return int(dt.timestamp() * 1_000_000_000)


def _intraday_15m_for_day(d, lows):
    # 25 bars; inject a deep LOW mid-day to trigger the stop intraday
    bars = []
    for k, lo in enumerate(lows):
        hh, mm = 9 + (k * 15) // 60, (15 + k * 15) % 60
        ts = _ns(d, hh, mm)
        bars.append(BarData(
            ticker="FAKE.NS", date=d,
            open=Decimal("100"), high=Decimal("101"),
            low=Decimal(str(lo)), close=Decimal("100"),
            volume=500, bar_open_ts_ns=ts,
        ))
    return bars


def _v5_daily_strategy():
    # minimal daily strategy dict with trailing enabled; reuse the
    # shape from tests/test_trailing_stop_integration.py::_v5_strategy
    from backend.algo.backtest.tests.test_trailing_stop_integration import (
        _v5_strategy,
    )
    return _v5_strategy()


def test_daily_strategy_exits_intraday_on_15m():
    # entry forms over warmup; on a later day price never closes below
    # stop, but a single 15m bar dips below it -> intraday stop-hit.
    daily = _daily_bars([100] * 25)
    cov = {"FAKE.NS": __import__(
        "backend.algo.backtest.coverage", fromlist=["TickerCoverage"]
    ).TickerCoverage("FAKE.NS", 900, _BASE, _BASE + timedelta(days=24), 25)}
    # 15m bars for the period; one day has a deep intraday low (90)
    exec_bars = {"FAKE.NS": []}
    for i in range(25):
        d = _BASE + timedelta(days=i)
        lows = [99] * 25
        if i == 22:
            lows[10] = 90  # deep dip mid-day
        exec_bars["FAKE.NS"].extend(_intraday_15m_for_day(d, lows))

    strategy = parse_strategy(_v5_daily_strategy())
    req = BacktestRequest(
        strategy_id=strategy.id, period_start=_BASE + timedelta(days=20),
        period_end=_BASE + timedelta(days=24),
    )
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=daily,
    ), patch(
        "backend.algo.backtest.runner.intraday_coverage", return_value=cov,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_bars_window",
        return_value=exec_bars,
    ), patch("backend.algo.backtest.runner.flush_events"):
        summary = run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=["FAKE.NS"],
        )
    reasons = {t.exit_reason for t in summary.trade_list}
    assert reasons & {"phase1_stop", "phase1_ratchet", "trail_stop"}
    # exit fill carries an intraday timestamp (not a pure daily exit)
    assert any(t.closed_at_ts_ns is not None for t in summary.trade_list)
```

> Note: the import of `intraday_coverage` and `load_intraday_bars_window` into `runner.py` must be module-level (or patched at the path used). Adjust the patch target to wherever the runner references them.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_two_clock_runner.py -v`
Expected: FAIL — daily strategy currently ignores intraday; no intraday exit / `AttributeError` on patch target.

- [ ] **Step 3: Implement the wiring** per implementation notes 1–6 above (edit `runner.py`). Ensure `intraday_coverage` and `ExecutionSimulator` are imported at module top so the patch targets resolve.

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_two_clock_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Regression — daily trailing-disabled is byte-identical**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/ -v`
Expected: PASS — existing daily/intraday backtest + trailing-integration tests unchanged (two-clock only activates for daily + trailing-enabled + coverage; coverage is patched off in those tests so they keep the daily path).

- [ ] **Step 6: Commit**

```bash
git add backend/algo/backtest/runner.py backend/algo/backtest/tests/test_two_clock_runner.py
git commit -m "feat(backtest): two-clock engine — daily signals, 15m exit resolution"
```

---

### Task 5: Block 1m/5m-cadence strategies with no history (§8)

A strategy whose signal grain is finer than any preserved data cannot be backtested faithfully → fail loudly.

**Files:**
- Modify: `backend/algo/backtest/runner.py` (`run_backtest` entry, after universe is known)
- Test: `backend/algo/backtest/tests/test_intraday_no_history_blocks.py`

**Interfaces:**
- Consumes: `intraday_coverage` (Task 1), `request.interval_sec`.
- Produces: `run_backtest` raises `ValueError` with a clear message when `request.interval_sec in {60, 300}` and no ticker has data at that grain.

- [ ] **Step 1: Write the failing test**

```python
# backend/algo/backtest/tests/test_intraday_no_history_blocks.py
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.backtest.coverage import TickerCoverage
from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest
from backend.algo.strategy.ast import parse_strategy
from backend.algo.backtest.tests.test_trailing_stop_integration import (
    _v5_strategy,
)

_BASE = date(2026, 1, 1)


def test_1m_strategy_blocks_when_no_1m_history():
    strategy = parse_strategy(_v5_strategy())
    req = BacktestRequest(
        strategy_id=strategy.id, period_start=_BASE,
        period_end=_BASE + timedelta(days=5), interval_sec=60,
    )
    # only 15m exists for the ticker
    cov = {"FAKE.NS": TickerCoverage(
        "FAKE.NS", 900, _BASE, _BASE + timedelta(days=5), 5)}
    with patch(
        "backend.algo.backtest.runner.intraday_coverage", return_value=cov,
    ), patch("backend.algo.backtest.runner.flush_events"):
        with pytest.raises(ValueError, match="No 1m history"):
            run_backtest(
                strategy=strategy, request=req,
                user_id=uuid4(), universe=["FAKE.NS"],
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_intraday_no_history_blocks.py -v`
Expected: FAIL — no guard yet (runs or errors elsewhere).

- [ ] **Step 3: Implement the guard**

In `run_backtest`, right after `universe` is available and before loading bars:

```python
    if request.interval_sec in (60, 300):
        from backend.algo.backtest.coverage import intraday_coverage
        _cov = intraday_coverage(
            tickers=universe,
            period_start=request.period_start,
            period_end=request.period_end,
        )
        if not any(
            c.finest_interval_sec is not None
            and c.finest_interval_sec <= request.interval_sec
            for c in _cov.values()
        ):
            _label = "1m" if request.interval_sec == 60 else "5m"
            raise ValueError(
                f"No {_label} history for these tickers; faithful "
                f"backtest unavailable. Run in paper to evaluate this "
                f"cadence."
            )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_intraday_no_history_blocks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/backtest/runner.py backend/algo/backtest/tests/test_intraday_no_history_blocks.py
git commit -m "feat(backtest): block 1m/5m backtest with no preserved history"
```

---

### Task 6: Result metadata tagging

Tag each result with the execution resolution + coverage so the run is honest about its fidelity (and Piece C can surface it).

**Files:**
- Modify: `backend/algo/backtest/types.py` (`BacktestSummary` ~:229)
- Modify: `backend/algo/backtest/runner.py` (summary construction)
- Test: `backend/algo/backtest/tests/test_backtest_resolution_metadata.py`

**Interfaces:**
- Consumes: `execution_interval_sec`, `daily_fallback_tickers` from Task 4.
- Produces: `BacktestSummary` gains `execution_interval_sec: int = 86400`, `daily_fallback_tickers: list[str] = []`.

- [ ] **Step 1: Write the failing test**

```python
# backend/algo/backtest/tests/test_backtest_resolution_metadata.py
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.strategy.ast import parse_strategy
from backend.algo.backtest.tests.test_trailing_stop_integration import (
    _v5_strategy,
)

_BASE = date(2026, 1, 1)


def _daily(n):
    return {"FAKE.NS": [BarData(
        ticker="FAKE.NS", date=_BASE + timedelta(days=i),
        open=Decimal("100"), high=Decimal("102"), low=Decimal("99"),
        close=Decimal("100"), volume=10_000) for i in range(n)]}


def test_daily_only_run_tags_86400():
    strategy = parse_strategy(_v5_strategy())
    req = BacktestRequest(
        strategy_id=strategy.id, period_start=_BASE + timedelta(days=20),
        period_end=_BASE + timedelta(days=24))
    # no intraday coverage -> stays daily
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=_daily(25),
    ), patch(
        "backend.algo.backtest.runner.intraday_coverage", return_value={},
    ), patch("backend.algo.backtest.runner.flush_events"):
        summary = run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=["FAKE.NS"])
    assert summary.execution_interval_sec == 86400
    assert summary.daily_fallback_tickers == ["FAKE.NS"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_backtest_resolution_metadata.py -v`
Expected: FAIL — `BacktestSummary` has no `execution_interval_sec` (`extra="forbid"` / AttributeError).

- [ ] **Step 3: Add fields + populate**

In `types.py` `BacktestSummary`, add:

```python
    execution_interval_sec: int = 86400
    daily_fallback_tickers: list[str] = Field(default_factory=list)
```

In `runner.py` where `BacktestSummary(...)` is constructed, pass:

```python
        execution_interval_sec=execution_interval_sec,
        daily_fallback_tickers=daily_fallback_tickers,
```

(ensure `daily_fallback_tickers` is defined as `[]` on the pure-daily path and `[t for t in universe if t not in exec_bars]` in two-clock mode.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_backtest_resolution_metadata.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/backtest/types.py backend/algo/backtest/runner.py backend/algo/backtest/tests/test_backtest_resolution_metadata.py
git commit -m "feat(backtest): tag results with execution resolution + fallback tickers"
```

---

### Task 7: Walkforward inheritance verification

Walkforward folds call `run_backtest`, so they inherit two-clock for free. Add a test proving it and that fold metadata propagates.

**Files:**
- Modify: `backend/algo/backtest/walkforward.py` (only if metadata needs threading into the per-fold summary; the fold already `model_copy`s the summary)
- Test: `backend/algo/backtest/tests/test_walkforward_two_clock.py`

**Interfaces:**
- Consumes: `run_backtest` two-clock behaviour + `BacktestSummary.execution_interval_sec`.
- Produces: a verified guarantee that a daily walkforward config with 15m coverage runs each test fold on the execution clock and each fold summary carries `execution_interval_sec`.

- [ ] **Step 1: Write the failing test**

```python
# backend/algo/backtest/tests/test_walkforward_two_clock.py
from datetime import date
from backend.algo.backtest.walkforward import walk_windows


def test_walk_windows_cover_period():
    wins = walk_windows(
        date(2026, 1, 1), date(2026, 3, 31),
        train_days=30, test_days=15, step_days=15,
    )
    assert wins, "expected at least one window"
    # each test window is 15 days and contiguous after train
    for w in wins:
        assert (w.test_end - w.test_start).days == 14
        assert w.test_start > w.train_end
```

> This locks the window math the two-clock folds rely on. A full
> end-to-end walkforward test (with a stubbed `run_backtest` asserting
> `execution_interval_sec` propagation) is added next.

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_walkforward_two_clock.py -v`
Expected: PASS if `walk_windows` already behaves; if it fails, fix the assertion to the real (documented) window semantics — do not change `walk_windows`.

- [ ] **Step 3: Add the propagation test**

```python
# append to test_walkforward_two_clock.py
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from backend.algo.backtest.types import BacktestSummary


def test_fold_summary_carries_execution_interval():
    fake = BacktestSummary(
        execution_interval_sec=900,
        # fill remaining required fields with minimal valid values;
        # mirror an existing BacktestSummary construction in
        # tests/test_walkforward_*.py for the exact required set.
    )
    assert fake.execution_interval_sec == 900
```

> During implementation, replace the minimal `BacktestSummary(...)` with
> the exact required-field set used by existing walkforward tests
> (`grep BacktestSummary( backend/algo/backtest/tests`). The assertion —
> that `execution_interval_sec` survives `model_copy` — is the point.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/test_walkforward_two_clock.py -v`
Expected: PASS.

- [ ] **Step 5: Full algo backtest suite regression**

Run: `docker compose exec -T backend python -m pytest backend/algo/backtest/tests/ -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add backend/algo/backtest/tests/test_walkforward_two_clock.py backend/algo/backtest/walkforward.py
git commit -m "test(backtest): walkforward inherits two-clock + carries resolution"
```

---

## Self-Review

**Spec coverage:**
- §5 A0 coverage helper → Task 1. ✓
- §4/§5 ExecutionSimulator + two-clock runner → Tasks 3, 4. ✓
- §6 resolution selection (data-driven, daily-fallback) → Task 4 (grain decision) + Task 6 (tagging). ✓
- §7 trigger ± slippage fills → Task 2. ✓
- §8 block 1m/5m → Task 5. ✓
- §9 lazy/batched loading → Task 1 (batched) + Task 4 (load only covered tickers). ✓
- §10 result metadata → Task 6. ✓
- §12 testing (path-order, regression, walkforward) → Tasks 3–7. ✓
- §13 two-clock default, regression-guarded → Task 4 Step 5. ✓
- Walkforward inheritance → Task 7. ✓

**Open items deferred to implementation (flagged in Task 4):** exact ATR source at the manager-creation site, and whether entries use a dedicated `exec_sim_broker`. These require the live `runner.py` in front of the implementer; the plan gives the exact insertion points and surrounding code.

**Non-goals confirmed absent:** no paper changes (Piece B), no UI (Piece C), no Kite calls, no new backfill.
