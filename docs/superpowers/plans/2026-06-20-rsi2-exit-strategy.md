# RSI(2) v5 — Three-Phase GTT Trailing Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a three-phase GTT trailing stop exit strategy for RSI(2) Connors v5, covering backtest (daily OHLC), paper/dry-run (15m bars), and live runtime (Kite GTT API).

**Architecture:** A new pure `TrailingStopManager` module drives all three runtimes; backtest replaces the flat stop-loss path; paper evaluates on 15m bars without real GTT calls; live wires into BUY fill postback + a new 15-min `asyncio.Task` to ratchet GTTs.

**Tech Stack:** Python 3.12, Kite Connect SDK (`kiteconnect`), Redis (sync via `cache` helper), FastAPI/asyncio, PyArrow/Iceberg (`algo.events`).

## Global Constraints

- Worktree: `/Users/abhay/Documents/projects/ai-agent-ui-rsi2-exit` — ALL commands run from here
- Branch: `feature/rsi2-exit-strategy` — stay on this branch; **NO PR to `dev` until user explicitly asks**
- Line length: 79 chars (black/flake8)
- `X | None` not `Optional[X]` (PEP 604)
- No bare `except:` — use `except Exception`
- All new Iceberg event rows via `event_row()` from `backend.algo.backtest.event_writer`
- Caught exceptions in long-running tasks MUST log with `exc_info=True`
- Test command: `python -m pytest tests/ -v` from worktree root (`PYTHONPATH=.`)
- Co-Authored-By: `Abhay Kumar Singh <asequitytrading@gmail.com>`

---

## File Map

| File | Action |
|---|---|
| `backend/algo/strategy/ast.py` | Modify — 4 new optional `RiskPerTrade` fields |
| `backend/algo/backtest/trailing_stop_manager.py` | **Create** — pure state machine |
| `backend/algo/backtest/tests/test_trailing_stop_manager.py` | **Create** — unit tests |
| `backend/algo/backtest/runner.py` | Modify — bypass flat stop, use `TrailingStopManager` |
| `backend/algo/backtest/tests/test_exit_reason_propagation.py` | Modify — extend for v5 exit reasons |
| `backend/algo/paper/runtime.py` | Modify — 15m bar trailing evaluation |
| `backend/algo/broker/kite_client.py` | Modify — `place_gtt`, `delete_gtt`, `get_gtts` |
| `backend/algo/live/runtime.py` | Modify — 15-min timer task + recovery + bar-close ratchet |
| `backend/algo/webhooks/kite_postback.py` | Modify — BUY fill → init manager + place GTT |
| `backend/algo/strategy/templates/rsi2_connors_daily_v5.json` | **Create** — v5 template |
| `backend/algo/strategy/tests/test_template_rsi2_connors_daily_v5.py` | **Create** — template tests |
| `backend/algo/live/tests/test_gtt_trailing_integration.py` | **Create** — live integration test |

---

## Phase 1 — Foundation: AST Fields + TrailingStopManager

*Deliverable:* Pure state machine with full unit tests. No runtime changes yet. Can be merged/verified standalone.

---

### Task 1: Add 4 optional fields to `RiskPerTrade`

**Files:**
- Modify: `backend/algo/strategy/ast.py:291-311`

**Interfaces:**
- Produces: `RiskPerTrade.phase1_ratchet_trigger_pct: float | None`, `RiskPerTrade.phase1_ratchet_new_stop_pct: float | None`, `RiskPerTrade.trailing_trigger_pct: float | None`, `RiskPerTrade.trailing_atr_multiplier: float | None`

- [ ] **Step 1: Write the failing test**

```python
# backend/algo/strategy/tests/test_ast_v5_fields.py
import pytest
from backend.algo.strategy.ast import parse_strategy

_BASE = {
    "id": "00000000-0000-0000-0000-000000000099",
    "name": "test",
    "universe": {"type": "scope", "scope": "discovery",
                 "filter": {"ticker_type": ["stock"], "market": "india"}},
    "schedule": {"type": "bar_close", "interval": "1d", "time": "15:25 IST"},
    "rebalance": {"type": "daily", "max_positions": 5},
    "product": "CNC",
    "root": {"type": "hold"},
    "risk": {
        "per_trade": {"stop_loss_pct": 5.0, "max_qty": 10000},
        "portfolio": {"max_exposure_pct": 100.0, "max_concentration_pct": 25.0},
        "daily": {"max_loss_pct": 5.0, "max_open_positions": 5},
    },
}


def _with_trailing(extra: dict) -> dict:
    import copy
    d = copy.deepcopy(_BASE)
    d["risk"]["per_trade"].update(extra)
    return d


def test_v5_fields_all_none_by_default():
    s = parse_strategy(_BASE)
    assert s.risk.per_trade.phase1_ratchet_trigger_pct is None
    assert s.risk.per_trade.phase1_ratchet_new_stop_pct is None
    assert s.risk.per_trade.trailing_trigger_pct is None
    assert s.risk.per_trade.trailing_atr_multiplier is None


def test_v5_fields_parse_correctly():
    d = _with_trailing({
        "phase1_ratchet_trigger_pct": 2.0,
        "phase1_ratchet_new_stop_pct": 3.0,
        "trailing_trigger_pct": 5.0,
        "trailing_atr_multiplier": 1.5,
    })
    s = parse_strategy(d)
    assert s.risk.per_trade.phase1_ratchet_trigger_pct == 2.0
    assert s.risk.per_trade.phase1_ratchet_new_stop_pct == 3.0
    assert s.risk.per_trade.trailing_trigger_pct == 5.0
    assert s.risk.per_trade.trailing_atr_multiplier == 1.5


def test_v3_template_still_parses_cleanly():
    import json
    from pathlib import Path
    p = (Path(__file__).parent.parent / "templates"
         / "rsi2_connors_daily_v3.json")
    s = parse_strategy(json.loads(p.read_text()))
    assert s.risk.per_trade.trailing_trigger_pct is None
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui-rsi2-exit
python -m pytest backend/algo/strategy/tests/test_ast_v5_fields.py -v
```
Expected: `FAILED` — `phase1_ratchet_trigger_pct` is not a field on `RiskPerTrade`.

- [ ] **Step 3: Add the four fields to `RiskPerTrade`**

Open `backend/algo/strategy/ast.py`. After line 311 (end of `cooldown_after_failed_exit_days` field, before `class RiskPortfolio`), add:

```python
    # v5 trailing stop — all None = disabled (v1/v2/v3 unchanged)
    phase1_ratchet_trigger_pct: float | None = Field(
        default=None, ge=0, le=50,
    )
    phase1_ratchet_new_stop_pct: float | None = Field(
        default=None, ge=0, le=50,
    )
    trailing_trigger_pct: float | None = Field(
        default=None, ge=0, le=100,
    )
    trailing_atr_multiplier: float | None = Field(
        default=None, ge=0.1, le=10.0,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest backend/algo/strategy/tests/test_ast_v5_fields.py -v
```
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/strategy/ast.py backend/algo/strategy/tests/test_ast_v5_fields.py
git commit -m "$(cat <<'EOF'
feat(ast): add 4 optional v5 trailing-stop fields to RiskPerTrade

phase1_ratchet_trigger_pct, phase1_ratchet_new_stop_pct,
trailing_trigger_pct, trailing_atr_multiplier — all default to None
so v1/v2/v3 templates parse unchanged.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 2: Create `TrailingStopManager` + unit tests

**Files:**
- Create: `backend/algo/backtest/trailing_stop_manager.py`
- Create: `backend/algo/backtest/tests/test_trailing_stop_manager.py`

**Interfaces:**
- Consumes: `RiskPerTrade` (from Task 1)
- Produces:
  - `TrailingPhase` enum (1 = HARD_STOP, 15 = RATCHETED, 2 = ATR_TRAIL)
  - `TrailingEvent` dataclass with `.event_type: str` ("STOP_UPDATED" | "STOP_HIT"), `.new_stop: float`, `.phase: int`, `.hwm: float`
  - `TrailingStopManager(risk, entry_price, atr, ticker="")` — init
  - `.on_price_update(price: float) -> TrailingEvent | None`
  - `.current_stop: float` property
  - `.is_trailing_enabled() -> bool`
  - `.to_dict() -> dict` — Redis-serializable
  - `TrailingStopManager.from_dict(data: dict, risk: RiskPerTrade) -> TrailingStopManager`

- [ ] **Step 1: Write failing tests first**

```python
# backend/algo/backtest/tests/test_trailing_stop_manager.py
"""Unit tests for TrailingStopManager pure state machine."""
import pytest
from backend.algo.strategy.ast import RiskPerTrade
from backend.algo.backtest.trailing_stop_manager import (
    TrailingPhase,
    TrailingStopManager,
)

_RISK_V5 = RiskPerTrade(
    stop_loss_pct=5.0,
    max_qty=10000,
    phase1_ratchet_trigger_pct=2.0,
    phase1_ratchet_new_stop_pct=3.0,
    trailing_trigger_pct=5.0,
    trailing_atr_multiplier=1.5,
)

_RISK_V3 = RiskPerTrade(stop_loss_pct=5.0, max_qty=10000)


def _mgr(entry: float = 5000.0, atr: float = 125.0) -> TrailingStopManager:
    return TrailingStopManager(_RISK_V5, entry_price=entry, atr=atr)


# ── Phase 1: hard stop ──────────────────────────────────────────────────────

class TestPhase1HardStop:
    def test_initial_stop_is_entry_minus_stop_pct(self):
        mgr = _mgr(entry=5000.0)
        assert abs(mgr.current_stop - 4750.0) < 0.01

    def test_no_event_below_ratchet_trigger(self):
        mgr = _mgr(entry=5000.0)
        # +1% — below the +2% ratchet trigger
        ev = mgr.on_price_update(5050.0)
        assert ev is None
        assert abs(mgr.current_stop - 4750.0) < 0.01

    def test_stop_hit_below_entry(self):
        mgr = _mgr(entry=5000.0)
        ev = mgr.on_price_update(4730.0)   # below 4750 stop
        assert ev is not None
        assert ev.event_type == "STOP_HIT"
        assert ev.phase == TrailingPhase.HARD_STOP

    def test_stop_hit_exactly_at_stop(self):
        mgr = _mgr(entry=5000.0)
        ev = mgr.on_price_update(4750.0)
        assert ev is not None
        assert ev.event_type == "STOP_HIT"


# ── Phase 1.5: one-time ratchet ─────────────────────────────────────────────

class TestPhase1Ratchet:
    def test_ratchet_fires_at_trigger(self):
        mgr = _mgr(entry=5000.0)
        # +2% = 5100 triggers ratchet
        ev = mgr.on_price_update(5100.0)
        assert ev is not None
        assert ev.event_type == "STOP_UPDATED"
        assert ev.phase == TrailingPhase.RATCHETED
        # new stop = entry * (1 - 3/100) = 4850
        assert abs(ev.new_stop - 4850.0) < 0.01
        assert abs(mgr.current_stop - 4850.0) < 0.01

    def test_ratchet_fires_only_once(self):
        mgr = _mgr(entry=5000.0)
        mgr.on_price_update(5100.0)   # fires ratchet
        ev2 = mgr.on_price_update(5150.0)   # still below trailing trigger
        # No further STOP_UPDATED from ratchet (not yet in phase 2)
        assert ev2 is None or ev2.event_type != "STOP_UPDATED"

    def test_ratchet_stop_is_higher_than_phase1_stop(self):
        mgr = _mgr(entry=5000.0)
        mgr.on_price_update(5100.0)
        assert mgr.current_stop > 4750.0   # 4850 > 4750


# ── Phase 2: ATR trailing ────────────────────────────────────────────────────

class TestPhase2AttrTrailing:
    def test_phase2_kicks_in_at_trailing_trigger(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        # +5% = 5250 triggers ATR trail (trail_width = 125 * 1.5 = 187.5)
        ev = mgr.on_price_update(5250.0)
        assert ev is not None
        assert ev.phase == TrailingPhase.ATR_TRAIL
        # new_stop = max(4850, 5250 - 187.5) = max(4850, 5062.5) = 5062.5
        assert abs(mgr.current_stop - 5062.5) < 0.01

    def test_phase2_ratchets_up_with_hwm(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5250.0)    # enter phase 2, stop=5062.5
        ev = mgr.on_price_update(5400.0)   # new HWM
        assert ev is not None
        assert ev.event_type == "STOP_UPDATED"
        # new_stop = 5400 - 187.5 = 5212.5
        assert abs(mgr.current_stop - 5212.5) < 0.01

    def test_phase2_stop_never_decreases(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5250.0)   # phase 2, stop=5062.5
        mgr.on_price_update(5400.0)   # stop=5212.5
        ev = mgr.on_price_update(5300.0)   # lower than HWM — stop stays
        # HWM didn't advance, so no STOP_UPDATED
        assert ev is None
        assert abs(mgr.current_stop - 5212.5) < 0.01

    def test_phase2_stop_hit(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5250.0)   # stop=5062.5
        ev = mgr.on_price_update(5050.0)   # below 5062.5
        assert ev is not None
        assert ev.event_type == "STOP_HIT"
        assert ev.phase == TrailingPhase.ATR_TRAIL


# ── Disabled for v3 strategies ────────────────────────────────────────────────

class TestTrailingDisabled:
    def test_is_trailing_enabled_false_for_v3(self):
        mgr = TrailingStopManager(_RISK_V3, entry_price=5000.0, atr=125.0)
        assert not mgr.is_trailing_enabled()

    def test_is_trailing_enabled_true_for_v5(self):
        mgr = _mgr()
        assert mgr.is_trailing_enabled()


# ── Redis serialisation ───────────────────────────────────────────────────────

class TestSerialization:
    def test_roundtrip_phase1(self):
        mgr = _mgr(entry=5000.0, atr=125.0, ticker="RELIANCE.NS")
        d = mgr.to_dict()
        mgr2 = TrailingStopManager.from_dict(d, _RISK_V5)
        assert abs(mgr2.current_stop - mgr.current_stop) < 0.001
        assert mgr2.state.phase == mgr.state.phase
        assert mgr2.state.hwm == mgr.state.hwm

    def test_roundtrip_phase2(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5100.0)   # ratchet
        mgr.on_price_update(5250.0)   # phase 2
        mgr.on_price_update(5400.0)   # ratchet up
        d = mgr.to_dict()
        mgr2 = TrailingStopManager.from_dict(d, _RISK_V5)
        assert mgr2.state.phase == TrailingPhase.ATR_TRAIL
        assert abs(mgr2.current_stop - 5212.5) < 0.01

    def test_roundtrip_preserves_phase1_ratcheted_flag(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5100.0)  # ratchet fires
        d = mgr.to_dict()
        mgr2 = TrailingStopManager.from_dict(d, _RISK_V5)
        # After restore, second price update at +2% must NOT fire ratchet again
        ev = mgr2.on_price_update(5110.0)
        assert ev is None or ev.event_type != "STOP_UPDATED" or ev.phase != 15


def _mgr(entry: float = 5000.0, atr: float = 125.0,
         ticker: str = "") -> TrailingStopManager:
    return TrailingStopManager(_RISK_V5, entry_price=entry, atr=atr,
                               ticker=ticker)
```

- [ ] **Step 2: Run to verify all fail**

```bash
python -m pytest backend/algo/backtest/tests/test_trailing_stop_manager.py -v
```
Expected: `ImportError` — module doesn't exist yet.

- [ ] **Step 3: Implement `trailing_stop_manager.py`**

Create `backend/algo/backtest/trailing_stop_manager.py`:

```python
"""Three-phase GTT trailing stop state machine.

Pure module — no I/O, no side effects. Shared by backtest,
paper, and live runtimes.

Phase numbering
---------------
1   Hard stop — flat % below entry (same as v3 stop_loss_pct).
15  Ratcheted (Phase 1.5) — one-time stop raise after first
    profitable threshold. Reduces max loss.
2   ATR trail — stop ratchets up with the HWM once position is
    sufficiently profitable.

All four threshold fields in ``RiskPerTrade`` default to None,
which keeps v1/v2/v3 behaviour completely unchanged.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from backend.algo.strategy.ast import RiskPerTrade


class TrailingPhase(int, enum.Enum):
    HARD_STOP = 1
    RATCHETED = 15
    ATR_TRAIL = 2


@dataclass
class TrailingEvent:
    event_type: str    # "STOP_UPDATED" | "STOP_HIT"
    new_stop: float
    phase: TrailingPhase
    hwm: float


@dataclass
class TrailingStopState:
    ticker: str
    entry_price: float
    phase: TrailingPhase
    current_stop: float
    hwm: float
    phase1_ratcheted: bool
    atr: float


class TrailingStopManager:
    """Per-position trailing stop state machine.

    Usage::

        mgr = TrailingStopManager(risk, fill_price, atr_14)
        # on each subsequent bar / tick:
        event = mgr.on_price_update(price)
        if event and event.event_type == "STOP_HIT":
            # close position at event.new_stop (or bar.low if gapped)
        elif event and event.event_type == "STOP_UPDATED":
            # ratchet GTT: new trigger = event.new_stop
    """

    def __init__(
        self,
        risk: RiskPerTrade,
        entry_price: float,
        atr: float,
        ticker: str = "",
    ) -> None:
        self._risk = risk
        initial_stop = entry_price * (
            1.0 - risk.stop_loss_pct / 100.0
        )
        self._state = TrailingStopState(
            ticker=ticker,
            entry_price=entry_price,
            phase=TrailingPhase.HARD_STOP,
            current_stop=initial_stop,
            hwm=entry_price,
            phase1_ratcheted=False,
            atr=atr,
        )

    # ── public API ─────────────────────────────────────────────

    @property
    def state(self) -> TrailingStopState:
        return self._state

    @property
    def current_stop(self) -> float:
        return self._state.current_stop

    def is_trailing_enabled(self) -> bool:
        r = self._risk
        return (
            r.trailing_trigger_pct is not None
            and r.trailing_atr_multiplier is not None
        )

    def on_price_update(self, price: float) -> TrailingEvent | None:
        """Process a price tick. Returns an event if stop changed or hit.

        Call with bar.low first (stop-hit check), then bar.high
        (HWM update) in backtest. In live/paper call with current LTP.
        """
        s = self._state
        r = self._risk

        old_stop = s.current_stop
        old_phase = s.phase

        # Update HWM
        old_hwm = s.hwm
        if price > s.hwm:
            s.hwm = price

        unrealised_pct = (
            (price - s.entry_price) / s.entry_price * 100.0
        )

        # Phase 1 → 1.5: one-time ratchet
        if (
            s.phase == TrailingPhase.HARD_STOP
            and not s.phase1_ratcheted
            and r.phase1_ratchet_trigger_pct is not None
            and r.phase1_ratchet_new_stop_pct is not None
            and unrealised_pct >= r.phase1_ratchet_trigger_pct
        ):
            s.phase = TrailingPhase.RATCHETED
            s.phase1_ratcheted = True
            s.current_stop = s.entry_price * (
                1.0 - r.phase1_ratchet_new_stop_pct / 100.0
            )

        # Phase 1 or 1.5 → 2: ATR trail
        if (
            s.phase in (TrailingPhase.HARD_STOP, TrailingPhase.RATCHETED)
            and r.trailing_trigger_pct is not None
            and r.trailing_atr_multiplier is not None
            and unrealised_pct >= r.trailing_trigger_pct
        ):
            s.phase = TrailingPhase.ATR_TRAIL
            trail_width = s.atr * r.trailing_atr_multiplier
            new_stop = s.hwm - trail_width
            s.current_stop = max(s.current_stop, new_stop)

        # Phase 2: ratchet up when HWM advances
        elif (
            s.phase == TrailingPhase.ATR_TRAIL
            and s.hwm > old_hwm
            and r.trailing_atr_multiplier is not None
        ):
            trail_width = s.atr * r.trailing_atr_multiplier
            new_stop = s.hwm - trail_width
            if new_stop > s.current_stop:
                s.current_stop = new_stop

        # Check stop hit AFTER all ratchet logic
        if price <= s.current_stop:
            return TrailingEvent(
                event_type="STOP_HIT",
                new_stop=s.current_stop,
                phase=s.phase,
                hwm=s.hwm,
            )

        # Return STOP_UPDATED if anything changed
        if s.current_stop != old_stop or s.phase != old_phase:
            return TrailingEvent(
                event_type="STOP_UPDATED",
                new_stop=s.current_stop,
                phase=s.phase,
                hwm=s.hwm,
            )

        return None

    # ── Redis serialisation ─────────────────────────────────────

    def to_dict(self) -> dict:
        s = self._state
        return {
            "ticker": s.ticker,
            "entry_price": s.entry_price,
            "phase": s.phase.value,
            "current_stop": s.current_stop,
            "hwm": s.hwm,
            "phase1_ratcheted": s.phase1_ratcheted,
            "atr": s.atr,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict,
        risk: RiskPerTrade,
    ) -> "TrailingStopManager":
        mgr: TrailingStopManager = cls.__new__(cls)
        mgr._risk = risk
        mgr._state = TrailingStopState(
            ticker=data.get("ticker", ""),
            entry_price=float(data["entry_price"]),
            phase=TrailingPhase(int(data["phase"])),
            current_stop=float(data["current_stop"]),
            hwm=float(data["hwm"]),
            phase1_ratcheted=bool(data["phase1_ratcheted"]),
            atr=float(data["atr"]),
        )
        return mgr
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest backend/algo/backtest/tests/test_trailing_stop_manager.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/backtest/trailing_stop_manager.py \
        backend/algo/backtest/tests/test_trailing_stop_manager.py
git commit -m "$(cat <<'EOF'
feat(algo): add TrailingStopManager pure state machine for v5 exit

Three-phase logic (hard stop → one-time ratchet → ATR trail).
Redis-serializable. Zero behaviour change for v1/v2/v3 strategies
(trailing_trigger_pct=None skips all phase transitions).
15 unit tests covering all phase transitions, ratchet idempotency,
stop-hit detection, and serialisation roundtrip.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Phase 2 — Backtest Integration

*Deliverable:* Backtest uses `TrailingStopManager` for trailing-enabled strategies; flat stop-loss path skipped. v5 template created and first backtest runnable.

---

### Task 3: Modify backtest runner to use `TrailingStopManager`

**Files:**
- Modify: `backend/algo/backtest/runner.py`

**Context:**  The relevant section of `runner.py` is the per-bar loop (~line 440). The existing `check_stop_loss_triggers()` fires for flat-stop strategies. For v5 (`trailing_trigger_pct is not None`), we bypass it and use `TrailingStopManager` instead.

ATR source: `ticker_features` dict (assembled at signal-evaluation time) contains `atr_14` from the daily feature engine. We save it to `pending_atr: dict[str, float]` when a BUY signal is emitted, then create the manager when the fill is confirmed on the next bar.

Conservative daily OHLC ordering: check `bar.low <= manager.current_stop` first (stop-hit), then call `manager.on_price_update(bar.high)` for HWM update.

**Interfaces:**
- Consumes: `TrailingStopManager`, `TrailingPhase` (Task 2), `RiskPerTrade` new fields (Task 1)
- Exit reasons produced: `"phase1_stop"`, `"phase1_ratchet"`, `"trail_stop"` (in addition to existing `"time_stop"`)

- [ ] **Step 1: Understand the per-bar loop structure**

Read `runner.py` lines 440–620 to locate:
1. `check_stop_loss_triggers` call
2. BUY fill confirmation (where `pt.apply_fill(fill)` is called for BUY side)
3. Time-stop section

```bash
grep -n "check_stop_loss_triggers\|pt\.apply_fill\|side.*BUY\|\"BUY\"\|time_stop\|TimeStop" \
    backend/algo/backtest/runner.py | head -30
```

- [ ] **Step 2: Identify exact line numbers**

Record the line numbers for:
- A: `check_stop_loss_triggers(` call
- B: BUY fill apply — `pt.apply_fill(sl_fill)` line INSIDE the BUY intent section
- C: Time-stop section start

Note these for the following edits.

- [ ] **Step 3: Add trailing state tracking variables before the bar loop**

Inside `run_backtest()`, after the existing per-ticker data structures are initialised (before the main `for ts_ns, bars_by_ts in ...` loop), add:

```python
    # v5 trailing stop — keyed by ticker.
    # trailing_enabled: True when strategy has trailing_trigger_pct set.
    _trailing_enabled = (
        strategy.risk.per_trade.trailing_trigger_pct is not None
        and strategy.risk.per_trade.trailing_atr_multiplier is not None
    )
    # Per-position trailing managers (created on BUY fill confirmation).
    _trailing_managers: dict[str, Any] = {}
    # ATR saved at signal-emission bar; consumed on next bar's fill.
    _pending_atr: dict[str, float] = {}
```

Add `from typing import Any` to imports if not already present.

- [ ] **Step 4: Save ATR at signal-emission time**

After the `evaluator.eval_node(...)` call produces an action, and when the action is `set_target_weight` / BUY side, save the ATR. Find the section where BUY `OrderIntent` is constructed (search for `side="BUY"` in runner.py). Just before or after emitting the intent, add:

```python
    if _trailing_enabled:
        _pending_atr[ticker] = float(
            ticker_features.get("atr_14", 0.0)
        )
```

- [ ] **Step 5: Create manager on BUY fill confirmation**

Find the section in the runner where BUY fills are applied via `pt.apply_fill(fill)` and `fill.side == "BUY"`. After `pt.apply_fill(fill)`, add:

```python
    if _trailing_enabled and fill.side == "BUY":
        _entry_atr = _pending_atr.pop(fill.ticker, 0.0)
        if _entry_atr > 0:
            _trailing_managers[fill.ticker] = TrailingStopManager(
                strategy.risk.per_trade,
                entry_price=float(fill.fill_price),
                atr=_entry_atr,
                ticker=fill.ticker,
            )
        else:
            _logger.warning(
                "trailing stop: missing atr_14 for %s at entry — "
                "falling back to flat stop",
                fill.ticker,
            )
```

- [ ] **Step 6: Replace flat stop-loss path for trailing-enabled strategies**

Find the `stop_triggers = check_stop_loss_triggers(...)` block. Wrap it so it only runs for non-trailing strategies:

```python
    if not _trailing_enabled:
        stop_triggers = check_stop_loss_triggers(
            open_positions={
                t: {"qty": p.qty, "avg_price": p.avg_price}
                for t, p in open_pos_now.items()
            },
            current_closes=closes_this_bar,
            stop_loss_pct=float(
                strategy.risk.per_trade.stop_loss_pct
            ),
        )
        # ... (existing trigger-to-SELL code unchanged)
    else:
        stop_loss_skip: set[str] = set()
```

- [ ] **Step 7: Add trailing stop evaluation per bar (for trailing-enabled strategies)**

After the existing stop-loss block (but before AST eval), add the trailing stop bar evaluation:

```python
    if _trailing_enabled:
        for _t, _mgr in list(_trailing_managers.items()):
            _pos = open_pos_now.get(_t)
            if _pos is None or _pos.qty <= 0:
                del _trailing_managers[_t]
                continue
            # Get this bar's low and high for the ticker
            if is_intraday:
                _cur_bar = bars_by_ts.get(_t, {}).get(ts_ns)
            else:
                _cur_bar = next(
                    (b for b in bars.get(_t, [])
                     if b.date == bar_date),
                    None,
                )
            if _cur_bar is None:
                continue

            _bar_low = float(_cur_bar.low)
            _bar_high = float(_cur_bar.high)

            # Conservative: check LOW first (stop hit)
            _stop_event = None
            if _bar_low <= _mgr.current_stop:
                _stop_event = _mgr.on_price_update(_bar_low)
            else:
                # No hit — update HWM with HIGH
                _hwm_event = _mgr.on_price_update(_bar_high)

            if _stop_event and _stop_event.event_type == "STOP_HIT":
                # Exit at current_stop (or bar.low if gapped through)
                _exit_price_raw = max(
                    _mgr.current_stop, _bar_low
                )
                _exit_reason = (
                    "phase1_stop"
                    if _stop_event.phase.value == 1
                    else "phase1_ratchet"
                    if _stop_event.phase.value == 15
                    else "trail_stop"
                )
                _ts_intent = OrderIntent(
                    ticker=_t,
                    side="SELL",
                    qty=_pos.qty,
                    intent_emitted_at=bar_date,
                    intent_emitted_ts_ns=ts_ns,
                    exit_reason=_exit_reason,
                )
                try:
                    _ts_fill = sim.execute(_ts_intent)
                except NoBarAvailableError:
                    _ts_fill = None
                if _ts_fill is None:
                    continue
                pt.apply_fill(_ts_fill)
                total_fees += _ts_fill.fees_inr
                fee_rates_version = _ts_fill.fee_rates_version
                events.append(
                    event_row(
                        session_id=session_id,
                        user_id=user_id,
                        strategy_id=strategy.id,
                        mode="backtest",
                        type_="order_filled",
                        payload={
                            "ticker": _ts_fill.ticker,
                            "side": _ts_fill.side,
                            "qty": _ts_fill.qty,
                            "fill_price": str(_ts_fill.fill_price),
                            "fill_date": (
                                _ts_fill.fill_date.isoformat()
                            ),
                            "fees_inr": str(_ts_fill.fees_inr),
                            "fee_rates_version": (
                                _ts_fill.fee_rates_version
                            ),
                            "exit_reason": _exit_reason,
                            "trailing_phase": (
                                _stop_event.phase.value
                            ),
                            "trailing_hwm": _stop_event.hwm,
                        },
                    )
                )
                del _trailing_managers[_t]
                stop_loss_skip.add(_t)
```

Add imports at the top of `runner.py` (after existing imports):

```python
from backend.algo.backtest.trailing_stop_manager import (
    TrailingStopManager,
)
```

- [ ] **Step 8: Clean up manager on position close from other causes**

Find the section where AST-driven exits are applied (SELL fills from signal). After `pt.apply_fill(fill)` for SELL side, add:

```python
    if _trailing_enabled and fill.side == "SELL":
        _trailing_managers.pop(fill.ticker, None)
```

- [ ] **Step 9: Add `TrailingStopManager` import and run existing tests**

```bash
python -m pytest backend/algo/backtest/tests/ -v -x
```
Expected: all existing tests pass.

- [ ] **Step 10: Commit**

```bash
git add backend/algo/backtest/runner.py
git commit -m "$(cat <<'EOF'
feat(backtest): use TrailingStopManager for trailing-enabled strategies

For strategies with trailing_trigger_pct set (v5), bypass flat
check_stop_loss_triggers and evaluate TrailingStopManager per bar
using conservative daily OHLC ordering (LOW→HIGH). Exit reasons:
phase1_stop / phase1_ratchet / trail_stop. v1/v2/v3 unchanged.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 4: Create v5 template and run first backtest

**Files:**
- Create: `backend/algo/strategy/templates/rsi2_connors_daily_v5.json`
- Create: `backend/algo/strategy/tests/test_template_rsi2_connors_daily_v5.py`

**Interfaces:**
- Consumes: All 4 v5 fields in `RiskPerTrade` (Task 1)
- Produces: A runnable template confirming the new `else: hold` branch

- [ ] **Step 1: Write template parse tests**

```python
# backend/algo/strategy/tests/test_template_rsi2_connors_daily_v5.py
"""Sanity tests for rsi2_connors_daily_v5.json."""
import json
from pathlib import Path

import pytest

from backend.algo.strategy.ast import parse_strategy

_TEMPLATE_PATH = (
    Path(__file__).parent.parent / "templates"
    / "rsi2_connors_daily_v5.json"
)


@pytest.fixture
def td() -> dict:
    return json.loads(_TEMPLATE_PATH.read_text())


def test_template_parses(td):
    s = parse_strategy(td)
    assert s.product == "CNC"
    assert s.schedule.interval == "1d"


def test_v5_trailing_fields_present(td):
    s = parse_strategy(td)
    assert s.risk.per_trade.phase1_ratchet_trigger_pct == 2.0
    assert s.risk.per_trade.phase1_ratchet_new_stop_pct == 3.0
    assert s.risk.per_trade.trailing_trigger_pct == 5.0
    assert s.risk.per_trade.trailing_atr_multiplier == 1.5


def test_else_branch_is_hold(td):
    """v5 removes SMA5 exit; GTT owns exits."""
    else_branch = td["root"]["else"]
    assert else_branch["type"] == "hold"


def test_entry_conditions_identical_to_v3(td):
    entry = td["root"]["cond"]["operands"]
    features = {op["left"]["feature"] for op in entry}
    assert features == {
        "rsi_2", "distance_from_sma200", "stress_prob",
        "nifty_above_sma200", "nifty_30d_return_pct",
    }


def test_risk_fields(td):
    s = parse_strategy(td)
    assert s.risk.per_trade.stop_loss_pct == 5.0
    assert s.risk.per_trade.max_holding_days == 5
    assert s.risk.per_trade.cooldown_after_failed_exit_days == 7
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest backend/algo/strategy/tests/test_template_rsi2_connors_daily_v5.py -v
```
Expected: `FileNotFoundError` — template doesn't exist yet.

- [ ] **Step 3: Create the v5 template**

Create `backend/algo/strategy/templates/rsi2_connors_daily_v5.json`:

```json
{
  "id": "00000000-0000-0000-0000-000000000051",
  "name": "RSI(2) Connors Daily v5 — three-phase GTT trailing stop",
  "universe": {
    "type": "scope",
    "scope": "discovery",
    "filter": {
      "ticker_type": ["stock"],
      "market": "india",
      "min_adtv_inr": 50000000
    }
  },
  "schedule": {
    "type": "bar_close",
    "interval": "1d",
    "time": "15:25 IST"
  },
  "rebalance": {
    "type": "daily",
    "max_positions": 5
  },
  "product": "CNC",
  "root": {
    "type": "if",
    "cond": {
      "type": "and",
      "operands": [
        {
          "type": "compare",
          "left": {"feature": "rsi_2"},
          "op": "<=",
          "right": {"literal": 5}
        },
        {
          "type": "compare",
          "left": {"feature": "distance_from_sma200"},
          "op": ">",
          "right": {"literal": 0.0}
        },
        {
          "type": "compare",
          "left": {"feature": "stress_prob"},
          "op": "<",
          "right": {"literal": 0.5}
        },
        {
          "type": "compare",
          "left": {"feature": "nifty_above_sma200"},
          "op": ">=",
          "right": {"literal": 1}
        },
        {
          "type": "compare",
          "left": {"feature": "nifty_30d_return_pct"},
          "op": ">",
          "right": {"literal": -5.0}
        }
      ]
    },
    "then": {
      "type": "set_target_weight",
      "weight": 0.2
    },
    "else": {
      "type": "hold"
    }
  },
  "risk": {
    "per_trade": {
      "stop_loss_pct": 5.0,
      "max_qty": 10000,
      "max_holding_days": 5,
      "cooldown_after_failed_exit_days": 7,
      "phase1_ratchet_trigger_pct": 2.0,
      "phase1_ratchet_new_stop_pct": 3.0,
      "trailing_trigger_pct": 5.0,
      "trailing_atr_multiplier": 1.5
    },
    "portfolio": {
      "max_exposure_pct": 100.0,
      "max_concentration_pct": 25.0
    },
    "daily": {
      "max_loss_pct": 5.0,
      "max_open_positions": 5
    }
  }
}
```

- [ ] **Step 4: Run template tests**

```bash
python -m pytest backend/algo/strategy/tests/test_template_rsi2_connors_daily_v5.py -v
```
Expected: 5 tests PASS.

- [ ] **Step 5: Run a quick dry-run backtest** (validates runner integration)

```bash
PYTHONPATH=. python -m backend.pipeline.runner backtest \
  --strategy-file backend/algo/strategy/templates/rsi2_connors_daily_v5.json \
  --start 2024-01-01 --end 2024-06-30 \
  --nav 100000 --user-id 1
```
Expected: completes without traceback; prints summary with trades count.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/strategy/templates/rsi2_connors_daily_v5.json \
        backend/algo/strategy/tests/test_template_rsi2_connors_daily_v5.py
git commit -m "$(cat <<'EOF'
feat(strategy): add rsi2_connors_daily_v5 template with GTT trailing stop

Entry conditions identical to v3. else branch changed to hold —
GTT owns all exits. Four new per_trade fields wire in the three-phase
trailing stop: ratchet at +2%, trail from +5% with ATR×1.5.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Phase 3 — Paper + Dry-run Integration

*Deliverable:* Paper runtime evaluates `TrailingStopManager` on real 15m bars. Dry-run mode logs GTT intents to `algo.events` without placing real orders.

---

### Task 5: Add trailing stop evaluation to paper runtime

**Files:**
- Modify: `backend/algo/paper/runtime.py`

**Context:** `PaperRuntime` has a `_factor_cache` dict that maps `(ticker, date) → {feature_key: Decimal}`. The `atr_14` key is populated from `stocks.daily_features` (same source as backtest `ticker_features`). The paper runtime processes 15m bars. The BUY fill path calls `self._broker.execute(signal=..., last_price=..., fill_date=...)`.

**Interfaces:**
- Consumes: `TrailingStopManager`, `TrailingPhase` (Task 2), `RiskPerTrade` fields (Task 1)
- Produces: per-fill trailing manager state; SELL signals emitted on stop hit

- [ ] **Step 1: Read the paper runtime BUY-fill and 15m bar processing sections**

```bash
grep -n "apply_fill\|_broker\.\|on_bar\|intraday_bar\|15m\|trailing\|side.*BUY" \
    backend/algo/paper/runtime.py | head -30
```

Locate:
- A: where BUY fills are applied
- B: where per-15m-bar processing happens

- [ ] **Step 2: Add trailing state variables to `PaperRuntime.__init__`**

Find `__init__` in `PaperRuntime`. After `self._factor_cache = {}` (~line 272), add:

```python
        _trailing_enabled = (
            strategy.risk.per_trade.trailing_trigger_pct is not None
            and strategy.risk.per_trade.trailing_atr_multiplier is not None
        )
        self._trailing_enabled = _trailing_enabled
        # ticker → TrailingStopManager
        self._trailing_managers: dict[str, TrailingStopManager] = {}
```

Add import at top of file:

```python
from backend.algo.backtest.trailing_stop_manager import (
    TrailingStopManager,
)
```

- [ ] **Step 3: Create manager on BUY fill in paper runtime**

Find the section where BUY fills are applied (search for `fill.side == "BUY"` or `signal.side == "BUY"`). After the fill is applied, add:

```python
        if self._trailing_enabled and fill.side == "BUY":
            _atr_raw = self._factor_cache.get(
                (fill.ticker, fill.fill_date)
            ) or self._factor_cache.get(
                (fill.ticker, fill.fill_date - timedelta(days=1))
            ) or {}
            _atr = float(_atr_raw.get("atr_14", 0.0))
            if _atr > 0:
                self._trailing_managers[fill.ticker] = (
                    TrailingStopManager(
                        self._strategy.risk.per_trade,
                        entry_price=float(fill.fill_price),
                        atr=_atr,
                        ticker=fill.ticker,
                    )
                )
            else:
                _logger.warning(
                    "paper trailing: missing atr_14 for %s — "
                    "no trailing manager created",
                    fill.ticker,
                )
```

- [ ] **Step 4: Evaluate trailing manager on each 15m bar**

Find the per-15m-bar processing loop. After the existing logic, add the trailing stop check for open positions with managers:

```python
        if self._trailing_enabled:
            for _ticker, _mgr in list(
                self._trailing_managers.items()
            ):
                _pos = self._positions.open_positions().get(_ticker)
                if _pos is None or _pos.qty <= 0:
                    del self._trailing_managers[_ticker]
                    continue
                # bar.high = HWM update; bar.low = stop-hit check
                _bar_high = float(bar.high) if hasattr(bar, "high") else float(bar.close)
                _bar_low = float(bar.low) if hasattr(bar, "low") else float(bar.close)

                _stop_event = None
                if _bar_low <= _mgr.current_stop:
                    _stop_event = _mgr.on_price_update(_bar_low)
                else:
                    _mgr.on_price_update(_bar_high)

                if _stop_event and _stop_event.event_type == "STOP_HIT":
                    _exit_reason = (
                        "phase1_stop"
                        if _stop_event.phase.value == 1
                        else "phase1_ratchet"
                        if _stop_event.phase.value == 15
                        else "trail_stop"
                    )
                    _logger.info(
                        "paper trailing stop hit: %s "
                        "phase=%d stop=%.4f",
                        _ticker,
                        _stop_event.phase.value,
                        _stop_event.new_stop,
                    )
                    # Dry-run: log intent; paper: emit synthetic SELL
                    _is_dry = getattr(
                        self, "_dry_run", False
                    )
                    if _is_dry:
                        self._emit_event(
                            type_="gtt_placed",
                            payload={
                                "dry_run": True,
                                "intent": "gtt_fire",
                                "ticker": _ticker,
                                "stop_price": _stop_event.new_stop,
                                "phase": _stop_event.phase.value,
                                "exit_reason": _exit_reason,
                            },
                        )
                    else:
                        from backend.algo.paper.types import Signal
                        _exit_sig = Signal(
                            ticker=_ticker,
                            side="SELL",
                            qty=_pos.qty,
                            reason=_exit_reason,
                        )
                        _exit_fill = self._broker.execute(
                            signal=_exit_sig,
                            last_price=bar.close,
                            fill_date=bar.date,
                        )
                        self._positions.apply_fill(_exit_fill)
                        self._emit_event(
                            type_="order_filled",
                            payload={
                                "ticker": _exit_fill.ticker,
                                "side": _exit_fill.side,
                                "qty": _exit_fill.qty,
                                "fill_price": str(
                                    _exit_fill.fill_price
                                ),
                                "exit_reason": _exit_reason,
                                "trailing_phase": (
                                    _stop_event.phase.value
                                ),
                            },
                        )
                    del self._trailing_managers[_ticker]
```

- [ ] **Step 5: Clean up manager on AST-driven SELL**

Find where AST-driven SELL fills are applied in paper runtime. After `self._positions.apply_fill(fill)` for SELL side, add:

```python
        if self._trailing_enabled and fill.side == "SELL":
            self._trailing_managers.pop(fill.ticker, None)
```

- [ ] **Step 6: Run all paper tests**

```bash
python -m pytest backend/algo/paper/tests/ -v
```
Expected: all pass (trailing logic is additive — no existing test breaks).

- [ ] **Step 7: Commit**

```bash
git add backend/algo/paper/runtime.py
git commit -m "$(cat <<'EOF'
feat(paper): TrailingStopManager evaluation on 15m bars

Creates manager on BUY fill using atr_14 from factor_cache.
Evaluates LOW (stop hit) then HIGH (HWM) per 15m bar. Paper mode:
emits synthetic SELL on stop hit. Dry-run mode: logs gtt_placed
intent event without any order. AST-driven exits clean up manager.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Phase 4 — GTT Client Methods

*Deliverable:* `KiteClient` exposes `place_gtt`, `delete_gtt`, `get_gtts` with full unit tests using a mocked Kite SDK.

---

### Task 6: Add GTT methods to `KiteClient`

**Files:**
- Modify: `backend/algo/broker/kite_client.py`
- Create: `backend/algo/broker/tests/test_kite_gtt.py`

**Context:** The Kite Connect Python SDK exposes:
- `kc.place_gtt(trigger_type, tradingsymbol, exchange, trigger_values, last_price, orders)` → `{"trigger_id": int}`
- `kc.delete_gtt(trigger_id)` → `{"trigger_id": int}`  
- `kc.get_gtts()` → `[{"id": int, "type": "single", "condition": {...}, "orders": [...], "status": "active"}, ...]`

`KiteConnect.GTT_TYPE_SINGLE = "single"`.

Ticker format: `"RELIANCE.NS"` → `tradingsymbol="RELIANCE"`, `exchange="NSE"`. The existing `_resolve_tradingsymbol(ticker)` (or similar) in `KiteClient` does this conversion — grep for `tradingsymbol` in the file to find the pattern.

**Interfaces:**
- Produces:
  - `place_gtt(ticker, trigger_price, limit_price, qty, transaction_type="SELL") -> int` (gtt_id)
  - `delete_gtt(gtt_id: int) -> None` (no-op if already triggered)
  - `get_gtts() -> list[dict]`

- [ ] **Step 1: Find existing tradingsymbol conversion pattern**

```bash
grep -n "tradingsymbol\|\.NS\|split.*NS\|replace.*NS" \
    backend/algo/broker/kite_client.py | head -10
```

Locate how the existing `place_order` call extracts `tradingsymbol` and `exchange` from a ticker like `"RELIANCE.NS"`.

- [ ] **Step 2: Write failing GTT tests**

```python
# backend/algo/broker/tests/test_kite_gtt.py
"""Unit tests for KiteClient GTT methods."""
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.kite_client import KiteClient


@pytest.fixture
def client(monkeypatch) -> KiteClient:
    """KiteClient with mocked KiteConnect internals."""
    with patch(
        "backend.algo.broker.kite_client.KiteConnect"
    ) as MockKC:
        kc = KiteClient.__new__(KiteClient)
        kc._kc = MockKC.return_value
        kc._kc.GTT_TYPE_SINGLE = "single"
        yield kc


class TestPlaceGtt:
    def test_returns_gtt_id(self, client):
        client._kc.place_gtt.return_value = {"trigger_id": 12345}
        gtt_id = client.place_gtt(
            ticker="RELIANCE.NS",
            trigger_price=4750.0,
            limit_price=4702.5,
            qty=10,
        )
        assert gtt_id == 12345

    def test_uses_single_trigger_type(self, client):
        client._kc.place_gtt.return_value = {"trigger_id": 1}
        client.place_gtt(
            ticker="RELIANCE.NS",
            trigger_price=4750.0,
            limit_price=4702.5,
            qty=10,
        )
        call_kwargs = client._kc.place_gtt.call_args
        assert call_kwargs[1]["trigger_type"] == "single"

    def test_strips_ns_suffix(self, client):
        client._kc.place_gtt.return_value = {"trigger_id": 1}
        client.place_gtt(
            ticker="INFY.NS",
            trigger_price=1000.0,
            limit_price=990.0,
            qty=5,
        )
        call_kwargs = client._kc.place_gtt.call_args[1]
        assert call_kwargs["tradingsymbol"] == "INFY"
        assert call_kwargs["exchange"] == "NSE"

    def test_order_is_limit_sell(self, client):
        client._kc.place_gtt.return_value = {"trigger_id": 1}
        client.place_gtt(
            ticker="TCS.NS",
            trigger_price=3000.0,
            limit_price=2970.0,
            qty=2,
        )
        orders = client._kc.place_gtt.call_args[1]["orders"]
        assert len(orders) == 1
        assert orders[0]["transaction_type"] == "SELL"
        assert orders[0]["order_type"] == "LIMIT"
        assert orders[0]["price"] == 2970.0


class TestDeleteGtt:
    def test_calls_delete(self, client):
        client._kc.delete_gtt.return_value = {"trigger_id": 99}
        client.delete_gtt(99)
        client._kc.delete_gtt.assert_called_once_with(trigger_id=99)

    def test_noop_on_kite_exception(self, client):
        from kiteconnect.exceptions import InputException
        client._kc.delete_gtt.side_effect = InputException(
            "GTT already triggered"
        )
        # Should not raise
        client.delete_gtt(999)


class TestGetGtts:
    def test_returns_list(self, client):
        client._kc.get_gtts.return_value = [
            {"id": 1, "status": "active"},
            {"id": 2, "status": "triggered"},
        ]
        result = client.get_gtts()
        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_returns_empty_list_on_error(self, client):
        from kiteconnect.exceptions import NetworkException
        client._kc.get_gtts.side_effect = NetworkException("timeout")
        result = client.get_gtts()
        assert result == []
```

- [ ] **Step 3: Run to verify they fail**

```bash
python -m pytest backend/algo/broker/tests/test_kite_gtt.py -v
```
Expected: `AttributeError` — `place_gtt` not on `KiteClient` yet.

- [ ] **Step 4: Add GTT methods to `KiteClient`**

Find the end of the `KiteClient` class in `backend/algo/broker/kite_client.py`. After the existing `cancel_order` / `modify_order` methods, add:

```python
    # ── GTT (Good Till Triggered) ─────────────────────────────────────────

    def place_gtt(
        self,
        ticker: str,
        trigger_price: float,
        limit_price: float,
        qty: int,
        transaction_type: str = "SELL",
    ) -> int:
        """Place a single-leg GTT stop order. Returns gtt_id.

        Args:
            ticker: Internal ticker (e.g. ``"RELIANCE.NS"``).
            trigger_price: Price at which GTT fires.
            limit_price: Limit price of the triggered order
                (``trigger_price * 0.99`` covers normal gaps).
            qty: Quantity to sell.
            transaction_type: "SELL" (default) or "BUY".

        Returns:
            Integer GTT trigger ID from Kite.
        """
        tradingsymbol = ticker.split(".")[0]
        exchange = "NSE"
        resp = self._kc.place_gtt(
            trigger_type=self._kc.GTT_TYPE_SINGLE,
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            trigger_values=[trigger_price],
            last_price=trigger_price,
            orders=[{
                "exchange": exchange,
                "tradingsymbol": tradingsymbol,
                "transaction_type": transaction_type,
                "quantity": qty,
                "product": "CNC",
                "order_type": "LIMIT",
                "price": limit_price,
            }],
        )
        gtt_id: int = (
            resp.get("trigger_id", 0)
            if isinstance(resp, dict)
            else int(resp)
        )
        _logger.info(
            "place_gtt: %s trigger=%.4f limit=%.4f qty=%d "
            "gtt_id=%d",
            ticker, trigger_price, limit_price, qty, gtt_id,
        )
        return gtt_id

    def delete_gtt(self, gtt_id: int) -> None:
        """Cancel a GTT. Silently no-ops if already triggered."""
        try:
            self._kc.delete_gtt(trigger_id=gtt_id)
            _logger.info("delete_gtt: gtt_id=%d", gtt_id)
        except Exception as exc:
            _logger.warning(
                "delete_gtt %d failed (may be already triggered): %s",
                gtt_id, exc,
            )

    def get_gtts(self) -> list[dict]:
        """List all active GTTs. Returns empty list on error."""
        try:
            return self._kc.get_gtts()
        except Exception as exc:
            _logger.warning(
                "get_gtts failed: %s", exc, exc_info=True
            )
            return []
```

- [ ] **Step 5: Run GTT tests**

```bash
python -m pytest backend/algo/broker/tests/test_kite_gtt.py -v
```
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/broker/kite_client.py \
        backend/algo/broker/tests/test_kite_gtt.py
git commit -m "$(cat <<'EOF'
feat(kite): add place_gtt, delete_gtt, get_gtts to KiteClient

place_gtt: single-leg GTT stop using GTT_TYPE_SINGLE; strips .NS
to get tradingsymbol. delete_gtt: silently no-ops on already-triggered
GTTs (InputException). get_gtts: returns [] on error so callers
don't need to handle network failures.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Phase 5 — Live Runtime Wiring

*Deliverable:* Live runtime places GTTs on BUY fill, ratchets every 15 minutes, recovers state from Redis on restart, and handles time-stop cleanup.

---

### Task 7: Wire BUY fill postback → init manager + place GTT

**Files:**
- Modify: `backend/algo/routes/webhooks.py` (the `update_order_fill_live` helper)
- Modify: `backend/algo/live/runtime.py` (GTT state holder + recovery)

**Context:** When Kite sends a COMPLETE postback for a BUY order, `update_order_fill_live()` in `webhooks.py` emits an `order_filled_live` event. After this, the live runtime needs to:
1. Initialise a `TrailingStopManager` for the ticker
2. Place a GTT via `kite_client.place_gtt()`
3. Persist state to Redis under `trailing:{user_id}:{strategy_id}:{ticker}`
4. Emit a `gtt_placed` event to `algo.events`

The live runtime holds the `KiteClient` instance. The postback handler doesn't have direct access to it — the cleanest pattern is to have the live runtime subscribe to `order_filled_live` events from a shared in-memory queue (already established in the codebase) OR expose a method that the postback route can call. Check which pattern the codebase uses.

**Interfaces:**
- Consumes: `TrailingStopManager` (Task 2), `KiteClient.place_gtt` (Task 6)
- Produces: Redis key `trailing:{uid}:{sid}:{ticker}` → JSON dict from `mgr.to_dict() | {"gtt_id": int}`

- [ ] **Step 1: Understand how postback reaches the live runtime**

```bash
grep -n "order_filled_live\|LiveRuntime\|_runtime\|notify\|queue\|emit" \
    backend/algo/routes/webhooks.py | head -20
grep -n "order_filled_live\|postback\|_on_fill\|_handle_fill" \
    backend/algo/live/runtime.py | head -20
```

Identify the call path from postback → live runtime fill notification.

- [ ] **Step 2: Add `_trailing_enabled` and GTT state dict to `LiveRuntime.__init__`**

In `backend/algo/live/runtime.py`, inside `LiveRuntime.__init__`, after `self._ticker_locked = set()`, add:

```python
        self._trailing_enabled = (
            strategy.risk.per_trade.trailing_trigger_pct is not None
            and strategy.risk.per_trade.trailing_atr_multiplier is not None
        )
        # ticker → TrailingStopManager (in-memory; Redis is durable source)
        self._trailing_managers: dict[str, TrailingStopManager] = {}
        # ticker → int gtt_id
        self._gtt_ids: dict[str, int] = {}
        # WS HWM per ticker (lightweight; updated by tick callback)
        self._ws_hwm: dict[str, float] = {}
```

Add import at top:

```python
from backend.algo.backtest.trailing_stop_manager import (
    TrailingStopManager,
)
```

- [ ] **Step 3: Add `_on_buy_fill_trailing()` method to `LiveRuntime`**

Add a new private method that the postback handler can call after a confirmed BUY fill:

```python
    def on_buy_fill_trailing(
        self,
        ticker: str,
        fill_price: float,
        qty: int,
    ) -> None:
        """Initialise TrailingStopManager + place GTT after BUY fill.

        Called by the postback route when a BUY COMPLETE is received
        for a strategy with trailing enabled.
        """
        if not self._trailing_enabled:
            return
        # Get ATR from factor cache
        from datetime import date as _date
        today = _date.today()
        _atr_row = (
            self._factor_cache.get((ticker, today))
            or self._factor_cache.get(
                (ticker, today - __import__("datetime").timedelta(days=1))
            )
            or {}
        )
        atr = float(_atr_row.get("atr_14", 0.0))
        if atr <= 0:
            _logger.warning(
                "trailing: atr_14 missing for %s — GTT not placed",
                ticker,
            )
            return

        mgr = TrailingStopManager(
            self._strategy.risk.per_trade,
            entry_price=fill_price,
            atr=atr,
            ticker=ticker,
        )
        stop = mgr.current_stop
        limit = stop * (1.0 - _GTT_LIMIT_HEADROOM_PCT)
        try:
            gtt_id = self._kite.place_gtt(
                ticker=ticker,
                trigger_price=stop,
                limit_price=limit,
                qty=qty,
            )
        except Exception as exc:
            _logger.error(
                "trailing: place_gtt failed for %s: %s",
                ticker, exc, exc_info=True,
            )
            return

        self._trailing_managers[ticker] = mgr
        self._gtt_ids[ticker] = gtt_id
        self._ws_hwm[ticker] = fill_price

        # Persist to Redis
        self._save_trailing_state(ticker, mgr, gtt_id)

        self._events.append(
            event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="live",
                type_="gtt_placed",
                payload={
                    "ticker": ticker,
                    "phase": 1,
                    "entry_price": fill_price,
                    "stop_price": stop,
                    "limit_price": limit,
                    "gtt_id": gtt_id,
                    "atr": atr,
                },
            )
        )
        _logger.info(
            "trailing: placed GTT %d for %s at stop=%.4f",
            gtt_id, ticker, stop,
        )
```

Add the constant near the top of `runtime.py` (after imports):

```python
# GTT trailing — limit headroom below trigger to absorb fast moves.
_GTT_LIMIT_HEADROOM_PCT = 0.01
```

- [ ] **Step 4: Add `_save_trailing_state` and `_load_trailing_state` helpers**

```python
    def _save_trailing_state(
        self, ticker: str, mgr: TrailingStopManager, gtt_id: int
    ) -> None:
        """Persist trailing manager state to Redis. TTL = 2 trading days."""
        try:
            from backend.cache import cache
            key = (
                f"trailing:{self._user_id}:"
                f"{self._strategy.id}:{ticker}"
            )
            data = mgr.to_dict()
            data["gtt_id"] = gtt_id
            import json
            cache.set(key, json.dumps(data), ttl=172800)  # 48h
        except Exception as exc:
            _logger.warning(
                "trailing: Redis save failed for %s: %s",
                ticker, exc, exc_info=True,
            )

    def _load_trailing_state_from_redis(self) -> None:
        """On restart: reload all trailing managers from Redis."""
        if not self._trailing_enabled:
            return
        try:
            from backend.cache import cache
            import json
            prefix = (
                f"trailing:{self._user_id}:{self._strategy.id}:"
            )
            # Enumerate open positions and try to load state for each
            for ticker in self._positions.open_positions():
                key = f"{prefix}{ticker}"
                raw = cache.get(key)
                if raw is None:
                    continue
                data = json.loads(raw)
                gtt_id = data.pop("gtt_id", None)
                if gtt_id is None:
                    continue
                mgr = TrailingStopManager.from_dict(
                    data, self._strategy.risk.per_trade
                )
                self._trailing_managers[ticker] = mgr
                self._gtt_ids[ticker] = gtt_id
                self._ws_hwm[ticker] = mgr.state.hwm
                _logger.info(
                    "trailing: restored state for %s from Redis "
                    "(phase=%d stop=%.4f gtt_id=%d)",
                    ticker, mgr.state.phase.value,
                    mgr.current_stop, gtt_id,
                )
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="live",
                        type_="trailing_stop_recovered",
                        payload={
                            "ticker": ticker,
                            "phase": mgr.state.phase.value,
                            "hwm_recovered": mgr.state.hwm,
                            "current_stop": mgr.current_stop,
                            "gtt_id": gtt_id,
                            "gtt_verified": False,
                        },
                    )
                )
        except Exception as exc:
            _logger.warning(
                "trailing: Redis restore failed: %s",
                exc, exc_info=True,
            )
```

- [ ] **Step 5: Call `_load_trailing_state_from_redis` at runtime startup**

In `LiveRuntime.run()`, after `self._ticker_locked.update(...)` (post-restart state restore block), add:

```python
        self._load_trailing_state_from_redis()
```

- [ ] **Step 6: Wire postback to `on_buy_fill_trailing`**

In `backend/algo/routes/webhooks.py`, inside `update_order_fill_live()`, after the `order_filled_live` event is appended and when `side == "BUY"` and status == "COMPLETE", add a call to the live runtime. Find how the live runtime is accessed from the route (grep for `_live_runtime` or similar registry).

```python
    # Trailing stop: init manager + place GTT on BUY fill
    if status == "COMPLETE" and side_from_order == "BUY":
        from backend.algo.live.registry import get_live_runtime
        rt = get_live_runtime(user_id=user_id, run_id=matched_run_id)
        if rt is not None:
            rt.on_buy_fill_trailing(
                ticker=ticker_from_order,
                fill_price=avg_price,
                qty=qty_from_order,
            )
```

(Adapt the runtime lookup to whatever pattern the codebase uses — check `get_live_runtime` or `_runtime_registry` with a grep.)

- [ ] **Step 7: Run live tests**

```bash
python -m pytest backend/algo/live/tests/ -v -x
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/algo/live/runtime.py backend/algo/routes/webhooks.py
git commit -m "$(cat <<'EOF'
feat(live): place GTT on BUY fill + restore trailing state on restart

on_buy_fill_trailing: creates TrailingStopManager from atr_14 factor
cache, calls place_gtt, persists to Redis (TTL 48h).
_load_trailing_state_from_redis: restores all open-position managers
on startup. Emits gtt_placed / trailing_stop_recovered events.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 8: 15-minute GTT ratchet timer

**Files:**
- Modify: `backend/algo/live/runtime.py`

**Context:** The live runtime already has several background asyncio tasks (`_timeout_watcher_task`, `_square_off_task`, `_event_flush_task`). Add a new `_trailing_ratchet_task` that runs every 15 minutes aligned to 15m bar boundaries (09:15, 09:30, 09:45 … 15:25 IST).

The WS tick callback updates `self._ws_hwm[ticker]` (lightweight — no GTT logic there).

**Interfaces:**
- Consumes: `self._trailing_managers`, `self._gtt_ids`, `self._ws_hwm`, `KiteClient.delete_gtt`, `KiteClient.place_gtt`
- Produces: updated `self._gtt_ids`, ratcheted Redis state, `gtt_ratcheted` events

- [ ] **Step 1: Add WS tick callback to update HWM**

Find the Kite WS tick callback (search for `on_ticks` or `def _on_tick` in `runtime.py`). Inside the callback, after existing per-tick logic, add:

```python
        # Trailing stop: lightweight HWM update (no GTT logic here)
        if self._trailing_enabled:
            for tick in ticks:
                _t = tick.get("trading_symbol", "")
                _ltp = tick.get("last_price", 0.0)
                if _t and _ltp > 0 and _t in self._trailing_managers:
                    _full = f"{_t}.NS"
                    _cur = self._ws_hwm.get(_full, 0.0)
                    if _ltp > _cur:
                        self._ws_hwm[_full] = _ltp
```

- [ ] **Step 2: Implement `_trailing_ratchet_loop()`**

Add this method to `LiveRuntime`:

```python
    async def _trailing_ratchet_loop(self) -> None:
        """Every 15 min during market hours: ratchet GTTs for open positions.

        Aligned to 15m bar boundaries starting 09:15 IST. Stops at 15:25 IST.
        """
        import asyncio
        from datetime import datetime, timezone, timedelta

        _IST = timezone(timedelta(hours=5, minutes=30))
        _MARKET_OPEN_H, _MARKET_OPEN_M = 9, 15
        _MARKET_CLOSE_H, _MARKET_CLOSE_M = 15, 25
        _INTERVAL_MIN = 15

        while True:
            try:
                now_ist = datetime.now(_IST)
                h, m = now_ist.hour, now_ist.minute

                # Outside market hours — sleep 60s and check again
                after_open = (h, m) >= (_MARKET_OPEN_H, _MARKET_OPEN_M)
                before_close = (h, m) < (_MARKET_CLOSE_H, _MARKET_CLOSE_M)
                if not (after_open and before_close):
                    await asyncio.sleep(60)
                    continue

                # Sleep until next 15m boundary
                minutes_past = (m - _MARKET_OPEN_M) % _INTERVAL_MIN
                wait_s = (_INTERVAL_MIN - minutes_past) * 60 - now_ist.second
                if wait_s > 0:
                    await asyncio.sleep(wait_s)

                if not self._trailing_enabled:
                    continue

                await asyncio.to_thread(self._ratchet_all_gtts)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                _logger.error(
                    "trailing ratchet loop error: %s",
                    exc, exc_info=True,
                )
                await asyncio.sleep(30)

    def _ratchet_all_gtts(self) -> None:
        """Sync: evaluate all trailing managers against current WS HWM."""
        for ticker, mgr in list(self._trailing_managers.items()):
            pos = self._positions.open_positions().get(ticker)
            if pos is None or pos.qty <= 0:
                self._trailing_managers.pop(ticker, None)
                continue

            hwm_price = self._ws_hwm.get(ticker, 0.0)
            if hwm_price <= 0:
                continue

            old_stop = mgr.current_stop
            event = mgr.on_price_update(hwm_price)

            if event and event.event_type == "STOP_UPDATED":
                old_gtt_id = self._gtt_ids.get(ticker)
                stop = mgr.current_stop
                limit = stop * (1.0 - _GTT_LIMIT_HEADROOM_PCT)
                try:
                    if old_gtt_id:
                        self._kite.delete_gtt(old_gtt_id)
                    new_id = self._kite.place_gtt(
                        ticker=ticker,
                        trigger_price=stop,
                        limit_price=limit,
                        qty=pos.qty,
                    )
                    self._gtt_ids[ticker] = new_id
                    self._save_trailing_state(ticker, mgr, new_id)
                    self._events.append(
                        event_row(
                            session_id=self._session_id,
                            user_id=self._user_id,
                            strategy_id=self._strategy.id,
                            mode="live",
                            type_="gtt_ratcheted",
                            payload={
                                "ticker": ticker,
                                "phase": event.phase.value,
                                "old_stop": old_stop,
                                "new_stop": stop,
                                "hwm": event.hwm,
                                "gtt_id_old": old_gtt_id,
                                "gtt_id_new": new_id,
                            },
                        )
                    )
                    _logger.info(
                        "trailing: ratcheted GTT for %s "
                        "%.4f → %.4f (phase %d)",
                        ticker, old_stop, stop,
                        event.phase.value,
                    )
                except Exception as exc:
                    _logger.error(
                        "trailing: ratchet GTT failed for %s: %s",
                        ticker, exc, exc_info=True,
                    )

            elif event and event.event_type == "STOP_HIT":
                # WS was down while GTT should have fired.
                # Cancel GTT and place an emergency limit sell.
                _logger.warning(
                    "trailing: STOP_HIT detected via WS for %s "
                    "— GTT may not have fired; placing emergency sell",
                    ticker,
                )
                old_gtt_id = self._gtt_ids.pop(ticker, None)
                if old_gtt_id:
                    self._kite.delete_gtt(old_gtt_id)
                try:
                    self._kite.place_order(
                        ticker=ticker,
                        side="SELL",
                        qty=pos.qty,
                        price=mgr.current_stop,
                        order_type="LIMIT",
                    )
                except Exception as exc:
                    _logger.error(
                        "trailing: emergency sell failed for %s: %s",
                        ticker, exc, exc_info=True,
                    )
                self._trailing_managers.pop(ticker, None)
```

- [ ] **Step 3: Start the ratchet task in `LiveRuntime.run()`**

In `run()`, alongside the other task starts, add:

```python
        # Trailing stop GTT ratchet — 15-min aligned loop
        self._trailing_ratchet_task: asyncio.Task | None = None
        if self._trailing_enabled:
            self._trailing_ratchet_task = asyncio.create_task(
                self._trailing_ratchet_loop(),
                name=f"trailing_ratchet_{self._run_id}",
            )
```

In the `finally:` block of `run()`, add cancellation:

```python
        if self._trailing_ratchet_task is not None:
            self._trailing_ratchet_task.cancel()
            try:
                await self._trailing_ratchet_task
            except (asyncio.CancelledError, Exception):
                pass
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/algo/live/tests/ -v -x
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/live/runtime.py
git commit -m "$(cat <<'EOF'
feat(live): 15-min GTT ratchet loop for trailing stop

_trailing_ratchet_loop: asyncio task aligned to 15m bar boundaries
(09:15–15:25 IST). Calls _ratchet_all_gtts which evaluates WS HWM
against TrailingStopManager and cancel+replaces GTT when stop
ratchets up. Phase transitions emit trailing_phase_transition events.
Emergency limit sell path for STOP_HIT detected via WS.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 9: Time-stop GTT cleanup + integration test

**Files:**
- Modify: `backend/algo/live/runtime.py` (bar-close time-stop path)
- Create: `backend/algo/live/tests/test_gtt_trailing_integration.py`

**Context:** At 15:25 IST bar-close, when `holding_days >= max_holding_days`, the live runtime currently places a SELL. For trailing-enabled strategies it must first cancel the outstanding GTT before placing the SELL.

**Interfaces:**
- Consumes: `self._gtt_ids`, `KiteClient.delete_gtt`, `KiteClient.place_order`
- Produces: `gtt_cancelled_for_time_stop` event; LIMIT SELL order placed

- [ ] **Step 1: Modify bar-close time-stop section**

Find the existing time-stop exit code in `LiveRuntime` (search for `time_stop` or `max_holding_days`). Before placing the SELL order, add GTT cancellation:

```python
        # Cancel trailing GTT before placing SELL (avoid double exit)
        if self._trailing_enabled:
            _gtt_id = self._gtt_ids.pop(ticker, None)
            if _gtt_id is not None:
                self._kite.delete_gtt(_gtt_id)
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="live",
                        type_="gtt_cancelled_for_time_stop",
                        payload={
                            "ticker": ticker,
                            "holding_days": holding_days,
                            "gtt_id": _gtt_id,
                        },
                    )
                )
            self._trailing_managers.pop(ticker, None)
            # Clean up Redis
            try:
                from backend.cache import cache
                cache.invalidate_exact(
                    f"trailing:{self._user_id}:"
                    f"{self._strategy.id}:{ticker}"
                )
            except Exception:
                pass
```

- [ ] **Step 2: Handle GTT fill postback (close trailing state)**

Find in the postback handler where `order_filled_live` events are processed for SELL fills. After the position is marked closed, add:

```python
        # GTT fill: close trailing state
        if status == "COMPLETE" and side_from_order == "SELL":
            from backend.algo.live.registry import get_live_runtime
            rt = get_live_runtime(user_id=user_id, run_id=matched_run_id)
            if rt is not None:
                rt._on_sell_fill_trailing(ticker_from_order)
```

Add `_on_sell_fill_trailing` to `LiveRuntime`:

```python
    def _on_sell_fill_trailing(self, ticker: str) -> None:
        """Clean up trailing state when any SELL fill is confirmed."""
        if not self._trailing_enabled:
            return
        self._trailing_managers.pop(ticker, None)
        self._gtt_ids.pop(ticker, None)
        self._ws_hwm.pop(ticker, None)
        try:
            from backend.cache import cache
            cache.invalidate_exact(
                f"trailing:{self._user_id}:"
                f"{self._strategy.id}:{ticker}"
            )
        except Exception:
            pass
        _logger.info(
            "trailing: state cleared for %s after SELL fill", ticker
        )
```

- [ ] **Step 3: Write integration test**

```python
# backend/algo/live/tests/test_gtt_trailing_integration.py
"""Integration tests for GTT trailing stop in live runtime."""
from unittest.mock import MagicMock, patch, AsyncMock
from decimal import Decimal

import pytest

from backend.algo.strategy.ast import parse_strategy
from backend.algo.backtest.trailing_stop_manager import (
    TrailingStopManager, TrailingPhase,
)

_V5_RISK_DICT = {
    "per_trade": {
        "stop_loss_pct": 5.0,
        "max_qty": 10000,
        "phase1_ratchet_trigger_pct": 2.0,
        "phase1_ratchet_new_stop_pct": 3.0,
        "trailing_trigger_pct": 5.0,
        "trailing_atr_multiplier": 1.5,
    },
    "portfolio": {
        "max_exposure_pct": 100.0,
        "max_concentration_pct": 25.0,
    },
    "daily": {"max_loss_pct": 5.0, "max_open_positions": 5},
}


class TestTrailingStateRoundtrip:
    """Redis serialisation/restoration is correct."""

    def test_to_dict_from_dict_preserves_phase(self):
        from backend.algo.strategy.ast import RiskPerTrade
        risk = RiskPerTrade(
            stop_loss_pct=5.0,
            max_qty=10000,
            phase1_ratchet_trigger_pct=2.0,
            phase1_ratchet_new_stop_pct=3.0,
            trailing_trigger_pct=5.0,
            trailing_atr_multiplier=1.5,
        )
        mgr = TrailingStopManager(risk, entry_price=5000.0, atr=125.0)
        mgr.on_price_update(5100.0)   # ratchet
        mgr.on_price_update(5250.0)   # phase 2
        d = mgr.to_dict()
        mgr2 = TrailingStopManager.from_dict(d, risk)
        assert mgr2.state.phase == TrailingPhase.ATR_TRAIL
        assert abs(mgr2.current_stop - mgr.current_stop) < 0.01


class TestGttPlacedOnBuyFill:
    """on_buy_fill_trailing creates manager and calls place_gtt."""

    def _make_runtime(self):
        from backend.algo.live.runtime import LiveRuntime

        strategy = MagicMock()
        strategy.risk.per_trade.trailing_trigger_pct = 5.0
        strategy.risk.per_trade.trailing_atr_multiplier = 1.5
        strategy.risk.per_trade.phase1_ratchet_trigger_pct = 2.0
        strategy.risk.per_trade.phase1_ratchet_new_stop_pct = 3.0
        strategy.risk.per_trade.stop_loss_pct = 5.0
        strategy.risk.per_trade.max_qty = 10000
        strategy.id = "test-strategy-id"

        rt = LiveRuntime.__new__(LiveRuntime)
        rt._strategy = strategy
        rt._user_id = 1
        rt._session_id = "sess-1"
        rt._run_id = "run-1"
        rt._trailing_enabled = True
        rt._trailing_managers = {}
        rt._gtt_ids = {}
        rt._ws_hwm = {}
        rt._events = []
        rt._factor_cache = {
            (__import__("datetime").date.today(), "atr_14"): Decimal("125")
        }
        # Patch _factor_cache to match (ticker, date)
        from datetime import date
        rt._factor_cache = {("RELIANCE.NS", date.today()): {"atr_14": Decimal("125")}}
        rt._kite = MagicMock()
        rt._kite.place_gtt.return_value = 99999
        rt._positions = MagicMock()
        return rt

    def test_manager_created_on_buy_fill(self):
        rt = self._make_runtime()
        with patch("backend.cache.cache.set"):
            rt.on_buy_fill_trailing(
                ticker="RELIANCE.NS",
                fill_price=5000.0,
                qty=10,
            )
        assert "RELIANCE.NS" in rt._trailing_managers
        mgr = rt._trailing_managers["RELIANCE.NS"]
        assert abs(mgr.current_stop - 4750.0) < 0.01

    def test_gtt_placed_with_correct_trigger(self):
        rt = self._make_runtime()
        with patch("backend.cache.cache.set"):
            rt.on_buy_fill_trailing(
                ticker="RELIANCE.NS",
                fill_price=5000.0,
                qty=10,
            )
        rt._kite.place_gtt.assert_called_once()
        call_kwargs = rt._kite.place_gtt.call_args[1]
        assert abs(call_kwargs["trigger_price"] - 4750.0) < 0.01
        assert call_kwargs["qty"] == 10

    def test_gtt_placed_event_emitted(self):
        rt = self._make_runtime()
        with patch("backend.cache.cache.set"):
            rt.on_buy_fill_trailing(
                ticker="RELIANCE.NS",
                fill_price=5000.0,
                qty=10,
            )
        types = [e["type_"] for e in rt._events]
        assert "gtt_placed" in types


class TestRatchetAllGtts:
    """_ratchet_all_gtts updates GTT when HWM advances to phase 2."""

    def _make_runtime_with_position(self, entry=5000.0, atr=125.0):
        from backend.algo.live.runtime import LiveRuntime
        from backend.algo.strategy.ast import RiskPerTrade

        risk = RiskPerTrade(
            stop_loss_pct=5.0, max_qty=10000,
            phase1_ratchet_trigger_pct=2.0,
            phase1_ratchet_new_stop_pct=3.0,
            trailing_trigger_pct=5.0,
            trailing_atr_multiplier=1.5,
        )
        mgr = TrailingStopManager(risk, entry_price=entry, atr=atr)
        # Advance to phase 2
        mgr.on_price_update(5100.0)   # ratchet
        mgr.on_price_update(5250.0)   # phase 2

        rt = LiveRuntime.__new__(LiveRuntime)
        rt._strategy = MagicMock()
        rt._strategy.id = "s1"
        rt._user_id = 1
        rt._session_id = "sess-1"
        rt._trailing_enabled = True
        rt._trailing_managers = {"RELIANCE.NS": mgr}
        rt._gtt_ids = {"RELIANCE.NS": 111}
        rt._ws_hwm = {"RELIANCE.NS": 5400.0}   # new HWM in phase 2
        rt._events = []
        rt._kite = MagicMock()
        rt._kite.place_gtt.return_value = 222
        rt._positions = MagicMock()
        pos_mock = MagicMock()
        pos_mock.qty = 10
        rt._positions.open_positions.return_value = {"RELIANCE.NS": pos_mock}
        return rt

    def test_gtt_ratcheted_on_hwm_advance(self):
        rt = self._make_runtime_with_position()
        with patch.object(rt, "_save_trailing_state"):
            rt._ratchet_all_gtts()
        rt._kite.delete_gtt.assert_called_once_with(111)
        rt._kite.place_gtt.assert_called_once()
        call_kwargs = rt._kite.place_gtt.call_args[1]
        # new stop = 5400 - 125*1.5 = 5212.5
        assert abs(call_kwargs["trigger_price"] - 5212.5) < 0.01

    def test_gtt_ratcheted_event_emitted(self):
        rt = self._make_runtime_with_position()
        with patch.object(rt, "_save_trailing_state"):
            rt._ratchet_all_gtts()
        types = [e["type_"] for e in rt._events]
        assert "gtt_ratcheted" in types
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest backend/algo/live/tests/test_gtt_trailing_integration.py -v
python -m pytest backend/algo/ -v --tb=short 2>&1 | tail -20
```
Expected: new test file passes; no regressions.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/live/runtime.py \
        backend/algo/routes/webhooks.py \
        backend/algo/live/tests/test_gtt_trailing_integration.py
git commit -m "$(cat <<'EOF'
feat(live): time-stop GTT cleanup + integration tests

Time-stop exit: cancels GTT before placing LIMIT SELL to prevent
double-exit. SELL fill postback: clears trailing state + Redis.
Integration tests cover: Redis roundtrip, GTT placement on BUY fill,
gtt_ratcheted on HWM advance in phase 2.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec section | Task | Status |
|---|---|---|
| §4.1 AST 4 new fields | Task 1 | ✅ |
| §4.2 v5 template (else=hold) | Task 4 | ✅ |
| §5.2 TrailingStopManager pure module | Task 2 | ✅ |
| §6.1 Backtest conservative LOW→HIGH | Task 3 | ✅ |
| §6.2 Paper 15m bar evaluation | Task 5 | ✅ |
| §6.2 Dry-run logs GTT intents | Task 5 | ✅ |
| §7 KiteClient GTT methods | Task 6 | ✅ |
| §6.3 Wiring 1: BUY fill → GTT | Task 7 | ✅ |
| §6.3 Wiring 2: 15-min timer | Task 8 | ✅ |
| §6.3 Wiring 3: GTT fill + recovery | Task 9 | ✅ |
| §6.5 No double exits | Task 9 (time-stop path) | ✅ |
| §8 Event vocabulary | Tasks 7–9 | ✅ |
| §9 Redis state schema | Task 7 | ✅ |

**Type consistency:**
- `TrailingPhase.HARD_STOP = 1`, `RATCHETED = 15`, `ATR_TRAIL = 2` — consistent across all tasks
- `TrailingEvent.event_type` is `"STOP_UPDATED"` | `"STOP_HIT"` — consistent
- Exit reasons: `"phase1_stop"` | `"phase1_ratchet"` | `"trail_stop"` — consistent in Tasks 3, 5, 9

**Placeholder scan:** None found.

---

## Run Full Test Suite Before Each Phase Ends

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui-rsi2-exit
python -m pytest backend/algo/ -v --tb=short -q
```

Expected after Phase 1: ~18 new tests pass, 0 regressions.
Expected after Phase 2: template tests pass; runner produces trades for v5.
Expected after Phase 5: full suite green.
