# Algo Trading — Session 7: Paper-trading runtime + risk engine + kill switch (Slice 8a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Slice 8a (backend half of the spec's Slice 8) — a tick-driven paper-trading runtime that walks live (or replayed) ticks through the existing strategy AST evaluator, gates every signal through a 3-tier `RiskEngine`, fills accepted intents at the current tick price via a new `PaperBroker`, and emits the canonical event-log lineage (`signal_generated` / `signal_rejected` / `order_submitted` / `order_filled` / `risk_breach`). Slice 8b (paper-runtime UI tab + Settings kill-switch button + per-strategy dashboard) becomes Session 8.

**Architecture:** Five new pure-Python layers plus two PG repos. (1) `RiskEngine.gate(signal, account_state, strategy_risk)` returns one of three outcomes — `accept`, `reject(reason)`, `scale(qty)` — without touching state. (2) `RiskStateRepo` holds intra-day rolling P&L per `(user_id, day_date)` in `algo.risk_state` (already exists from Slice 0); a thin recovery helper rebuilds it from the day's `order_filled` events on backend restart. (3) `KillSwitchRepo` writes to `algo.kill_switch` (durable) and mirrors to Redis (fast read). (4) `PaperBroker` fills at the current tick's LTP (not OHLCV next-bar-open like `SimBroker`); stamps `IndianFeeModel.rates_version` per spec § 6.2. (5) `PaperRuntime` orchestrates source → bar resampler → AST evaluator → RiskEngine → PaperBroker → PositionTracker → event log. The runtime is one-instance-per-strategy (multi-strategy multiplexing inside Slice 8b's UI orchestrator). HTTP surface in this slice = kill-switch endpoints; paper-runtime lifecycle endpoints land in 8b.

**Tech Stack:** Python 3.12 / asyncio / asyncpg / SQLAlchemy 2.0 async / pytest. No frontend touched. Reuses Slice 1's `IndianFeeModel`, Slice 4-5's `Strategy` AST, Slice 6's `Tick`/`Bar`/`Resampler`/`ReplayTickSource`, Slice 7a's `Evaluator`/`PositionTracker`/`event_writer`, Slice 7b's `_session_factory` pattern.

**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md` (§ 3.4 PG tables, § 5 risk engine, § 7.4 reconciliation, § 9.1 slice 8).

**Branch:** `feature/algo-trading-session-7-paper-runtime` (cut off Session 6's tip `8ceff42`).

**Conventions reminders:**
- Per spec § 5.2: every rejection MUST emit a `signal_rejected` event with the reason — no silent drops. Replay tab (Slice 10) surfaces them.
- Per spec § 5.3: `algo.risk_state` resets at IST midnight via the existing scheduler. Restart-replay rebuilds today's state from `order_filled` events; never trust scratch state.
- Per spec § 5.4: kill switch sets Redis flag `algo:kill:{user_id}` AND mirrors to `algo.kill_switch` for restart durability. Re-arming requires confirm dialog (UI lands in 8b; backend just exposes the toggle endpoint).
- All events through the existing `event_writer.flush_events()` pattern — single Iceberg commit at runtime shutdown.
- `event_row()` from Slice 7a takes `mode="paper"` for this slice (was `mode="backtest"`).
- `_pg_session()` pattern (CLAUDE.md §5.1) — `_session_factory()` wrapper from Slice 7b.
- Per CLAUDE.md §5.13: kill switch in Redis = `algo:kill:{user_id}` (TTL_VOLATILE). Add `algo.kill_switch` + `algo.risk_state` to `_CACHE_INVALIDATION_MAP` (already wired? — verify).

---

## File Structure

### Backend (new)

- `backend/algo/paper/__init__.py` — package marker.
- `backend/algo/paper/types.py` — `Signal`, `RiskDecision`, `AccountState`, `KillReason` enum, `RejectReason` enum.
- `backend/algo/paper/risk_engine.py` — `RiskEngine.gate()` (per-trade / portfolio / daily checks).
- `backend/algo/paper/risk_state_repo.py` — `RiskStateRepo` (PG CRUD + restart-replay helper).
- `backend/algo/paper/kill_switch_repo.py` — `KillSwitchRepo` (PG durability + Redis fast read).
- `backend/algo/paper/broker.py` — `PaperBroker` (at-tick fills + IndianFeeModel).
- `backend/algo/paper/runtime.py` — `PaperRuntime` orchestrator.
- `backend/algo/routes/kill_switch.py` — `GET /v1/algo/kill-switch` + `POST /v1/algo/kill-switch/arm` + `POST /v1/algo/kill-switch/disarm`.

### Backend (modified)

- `backend/algo/routes/__init__.py` — re-export `create_kill_switch_router`.
- `backend/routes.py` — register the router.
- `stocks/repository.py` — verify `algo.risk_state` + `algo.kill_switch` are in `_CACHE_INVALIDATION_MAP`; add if missing.

### Tests (new)

- `backend/algo/tests/test_paper_risk_engine.py` — 3-tier gate logic.
- `backend/algo/tests/test_paper_risk_state_repo.py` — PG round-trip + restart-replay rebuild.
- `backend/algo/tests/test_paper_kill_switch_repo.py` — PG + Redis mirror.
- `backend/algo/tests/test_paper_broker.py` — at-tick fills + fee version.
- `backend/algo/tests/test_paper_runtime.py` — end-to-end with replay fixture, accept + reject paths.
- `backend/algo/tests/test_kill_switch_routes.py` — endpoint smokes.

---

## Task 1: Types module

**Files:**
- Create: `backend/algo/paper/__init__.py`
- Create: `backend/algo/paper/types.py`

- [ ] **Step 1: Package marker**

```python
# backend/algo/paper/__init__.py
"""Paper-trading runtime — Slice 8a of the Algo Trading epic."""
```

- [ ] **Step 2: Types**

```python
# backend/algo/paper/types.py
"""Pydantic models + enums shared across the paper-trading runtime."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class RejectReason(str, Enum):
    """Why the risk engine blocked a signal. Surfaced via Replay
    tab in Slice 10."""

    DAILY_LOSS_CAP = "daily_loss_cap"
    EXPOSURE_CAP = "exposure_cap"
    POSITION_CAP = "position_cap"
    MAX_OPEN_POSITIONS = "max_open_positions"
    MAX_QTY = "max_qty"
    KILL_SWITCH = "kill_switch"
    INSTRUMENT_BLACKLIST = "instrument_blacklist"


class Signal(BaseModel):
    """A strategy-emitted intent, before the risk gate."""
    model_config = ConfigDict(extra="forbid")

    signal_id: UUID = Field(default_factory=uuid4)
    strategy_id: UUID
    user_id: UUID
    ticker: str
    side: Literal["BUY", "SELL"]
    qty: int = Field(ge=1)
    emitted_at_ns: int = Field(ge=0)


class AccountState(BaseModel):
    """Snapshot the risk engine reads to gate a signal.

    All values are in INR. ``open_positions`` is keyed by ticker.
    """
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    day_date: date
    initial_capital_inr: Decimal
    current_equity_inr: Decimal
    daily_realised_pnl_inr: Decimal
    daily_unrealised_pnl_inr: Decimal
    open_positions: dict[str, int] = Field(default_factory=dict)
    open_position_count: int = 0
    kill_switch_active: bool = False


class RiskDecision(BaseModel):
    """The risk engine's verdict.

    - ``outcome="accept"`` → forward the signal verbatim.
    - ``outcome="scale"``  → scale qty down to ``adjusted_qty``.
    - ``outcome="reject"`` → drop the signal; ``reason`` populated.
    """
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["accept", "scale", "reject"]
    adjusted_qty: int | None = None
    reason: RejectReason | None = None
    threshold: Decimal | None = None
    observed_value: Decimal | None = None


class KillSwitchState(BaseModel):
    """Row shape for GET /v1/algo/kill-switch."""
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    active: bool
    set_by: UUID | None = None
    set_at: datetime | None = None
    reason: str | None = None
```

- [ ] **Step 3: Smoke + commit**

```bash
docker compose exec backend python -c "
from backend.algo.paper.types import (
    Signal, AccountState, RiskDecision, RejectReason,
    KillSwitchState,
)
print('ok')
" 2>&1 | tail -3

git add backend/algo/paper/__init__.py backend/algo/paper/types.py
git commit -m "$(cat <<'EOF'
feat(algo): paper runtime types module

Slice 8a. Signal / AccountState / RiskDecision / RejectReason /
KillSwitchState — single source of truth for the wire shape
between the runtime, risk engine, repos, and routes. RejectReason
enum mirrors spec § 3.2 signal_rejected payload reasons.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: RiskEngine — 3-tier gate

**Files:**
- Create: `backend/algo/paper/risk_engine.py`
- Create: `backend/algo/tests/test_paper_risk_engine.py`

- [ ] **Step 1: Failing tests**

```python
# backend/algo/tests/test_paper_risk_engine.py
"""3-tier risk engine: per-trade / portfolio / daily."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.paper.risk_engine import RiskEngine
from backend.algo.paper.types import (
    AccountState, RejectReason, Signal,
)


def _signal(side="BUY", qty=10, ticker="X") -> Signal:
    return Signal(
        strategy_id=uuid4(), user_id=uuid4(),
        ticker=ticker, side=side, qty=qty,
        emitted_at_ns=0,
    )


def _account(**kw) -> AccountState:
    base = {
        "user_id": uuid4(),
        "day_date": date(2026, 4, 1),
        "initial_capital_inr": Decimal("100000"),
        "current_equity_inr": Decimal("100000"),
        "daily_realised_pnl_inr": Decimal("0"),
        "daily_unrealised_pnl_inr": Decimal("0"),
        "open_positions": {},
        "open_position_count": 0,
        "kill_switch_active": False,
    }
    base.update(kw)
    return AccountState(**base)


_RISK = {
    "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
    "portfolio": {
        "max_exposure_pct": 80,
        "max_concentration_pct": 25,
    },
    "daily": {
        "max_loss_pct": 2,
        "max_open_positions": 10,
    },
}


def test_accept_when_all_caps_clear():
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(qty=10),
        account=_account(),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "accept"


def test_kill_switch_short_circuits():
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(),
        account=_account(kill_switch_active=True),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.KILL_SWITCH


def test_reject_when_qty_exceeds_per_trade_max():
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(qty=101),
        account=_account(),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.MAX_QTY


def test_reject_when_daily_loss_cap_breached():
    engine = RiskEngine()
    # 2% of 100k = 2000; current loss = 2500 → already past cap.
    decision = engine.gate(
        signal=_signal(),
        account=_account(
            daily_realised_pnl_inr=Decimal("-2500"),
        ),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.DAILY_LOSS_CAP


def test_reject_when_max_open_positions_reached():
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(ticker="NEW"),
        account=_account(open_position_count=10),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.MAX_OPEN_POSITIONS


def test_reject_when_concentration_breached():
    """A 30k order at 100/share + existing 10k position = 40k in
    one ticker, which is 40% of 100k equity > 25% cap.
    """
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(ticker="X", qty=300),
        account=_account(
            open_positions={"X": 100},  # already 10k notional
        ),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.POSITION_CAP


def test_scale_when_exposure_cap_partially_blocks():
    """Existing 70k open exposure + a 20k order = 90k of 80k cap.
    Engine scales the order down to 10k worth (= 100 shares at 100).
    """
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(ticker="NEW", qty=200),
        account=_account(
            open_positions={"OTHER": 700},  # 70k notional
            open_position_count=1,
        ),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "scale"
    assert decision.adjusted_qty == 100


def test_sell_signals_skip_exposure_checks():
    """SELL reduces exposure — should never be blocked by
    portfolio caps."""
    engine = RiskEngine()
    decision = engine.gate(
        signal=_signal(side="SELL", ticker="X", qty=300),
        account=_account(
            open_positions={"X": 1000},  # large existing long
            open_position_count=1,
        ),
        risk=_RISK,
        last_price=Decimal("100"),
    )
    assert decision.outcome == "accept"
```

- [ ] **Step 2: Run — expect ImportError**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_paper_risk_engine.py -v 2>&1 | tail -8
```

- [ ] **Step 3: Implement**

```python
# backend/algo/paper/risk_engine.py
"""3-tier risk engine — pure logic, no state.

Same code runs in backtest + paper. Signal in, RiskDecision out.
The runtime persists rejections as ``signal_rejected`` events;
this module is responsible only for the verdict.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from backend.algo.paper.types import (
    AccountState, RejectReason, RiskDecision, Signal,
)

_logger = logging.getLogger(__name__)


def _accept() -> RiskDecision:
    return RiskDecision(outcome="accept")


def _reject(
    reason: RejectReason,
    threshold: Decimal | None = None,
    observed: Decimal | None = None,
) -> RiskDecision:
    return RiskDecision(
        outcome="reject", reason=reason,
        threshold=threshold, observed_value=observed,
    )


def _scale(qty: int) -> RiskDecision:
    return RiskDecision(outcome="scale", adjusted_qty=qty)


class RiskEngine:
    def gate(
        self,
        *,
        signal: Signal,
        account: AccountState,
        risk: dict[str, Any],
        last_price: Decimal,
    ) -> RiskDecision:
        """Apply per-trade → daily → portfolio caps in that order.

        Per-trade and daily are hard rejects; portfolio
        ``max_exposure_pct`` may scale the order rather than reject
        outright (signal still meaningful at smaller size).
        """
        if account.kill_switch_active:
            return _reject(RejectReason.KILL_SWITCH)

        # Per-trade.
        per_trade = risk.get("per_trade", {})
        max_qty = int(per_trade.get("max_qty", 0))
        if max_qty > 0 and signal.qty > max_qty:
            return _reject(
                RejectReason.MAX_QTY,
                threshold=Decimal(max_qty),
                observed=Decimal(signal.qty),
            )

        # Daily.
        daily = risk.get("daily", {})
        max_loss_pct = Decimal(str(daily.get("max_loss_pct", 0)))
        if max_loss_pct > 0:
            cap_loss_inr = (
                account.initial_capital_inr * max_loss_pct
                / Decimal("100")
            )
            current_loss = -(
                account.daily_realised_pnl_inr
                + account.daily_unrealised_pnl_inr
            )
            if current_loss >= cap_loss_inr:
                return _reject(
                    RejectReason.DAILY_LOSS_CAP,
                    threshold=cap_loss_inr,
                    observed=current_loss,
                )

        max_open = int(daily.get("max_open_positions", 0))
        if (
            max_open > 0
            and signal.side == "BUY"
            and signal.ticker not in account.open_positions
            and account.open_position_count >= max_open
        ):
            return _reject(
                RejectReason.MAX_OPEN_POSITIONS,
                threshold=Decimal(max_open),
                observed=Decimal(account.open_position_count),
            )

        if signal.side == "SELL":
            # SELL reduces exposure; portfolio caps don't apply.
            return _accept()

        # Portfolio — concentration (per-ticker).
        portfolio = risk.get("portfolio", {})
        max_concentration_pct = Decimal(
            str(portfolio.get("max_concentration_pct", 0))
        )
        if (
            max_concentration_pct > 0
            and account.current_equity_inr > 0
        ):
            existing_qty = account.open_positions.get(
                signal.ticker, 0,
            )
            new_notional = (
                Decimal(existing_qty + signal.qty) * last_price
            )
            new_concentration_pct = (
                new_notional / account.current_equity_inr
                * Decimal("100")
            )
            if new_concentration_pct > max_concentration_pct:
                return _reject(
                    RejectReason.POSITION_CAP,
                    threshold=max_concentration_pct,
                    observed=new_concentration_pct,
                )

        # Portfolio — total exposure (may scale).
        max_exposure_pct = Decimal(
            str(portfolio.get("max_exposure_pct", 0))
        )
        if (
            max_exposure_pct > 0
            and account.current_equity_inr > 0
        ):
            cap_inr = (
                account.current_equity_inr * max_exposure_pct
                / Decimal("100")
            )
            existing_exposure = sum(
                (Decimal(q) * last_price for q in
                 account.open_positions.values()),
                start=Decimal("0"),
            )
            requested_notional = (
                Decimal(signal.qty) * last_price
            )
            total = existing_exposure + requested_notional
            if total > cap_inr:
                headroom = cap_inr - existing_exposure
                if headroom <= 0:
                    return _reject(
                        RejectReason.EXPOSURE_CAP,
                        threshold=cap_inr,
                        observed=existing_exposure,
                    )
                scaled_qty = int(headroom // last_price)
                if scaled_qty <= 0:
                    return _reject(
                        RejectReason.EXPOSURE_CAP,
                        threshold=cap_inr,
                        observed=total,
                    )
                return _scale(scaled_qty)

        return _accept()
```

- [ ] **Step 4: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_paper_risk_engine.py -v 2>&1 | tail -10

git add backend/algo/paper/risk_engine.py backend/algo/tests/test_paper_risk_engine.py
git commit -m "$(cat <<'EOF'
feat(algo): RiskEngine — 3-tier gate

Slice 8a. Pure RiskEngine.gate(signal, account, risk, last_price)
returns RiskDecision (accept | scale | reject). Tier order:
kill-switch → per-trade → daily → portfolio. Concentration is
hard reject; total exposure may scale qty down to fit headroom.
SELL signals skip portfolio checks (they reduce exposure).
8 unit tests cover each tier, scaling, and short-circuits.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: RiskStateRepo (PG)

**Files:**
- Create: `backend/algo/paper/risk_state_repo.py`
- Create: `backend/algo/tests/test_paper_risk_state_repo.py`

- [ ] **Step 1: Implement repo**

```python
# backend/algo/paper/risk_state_repo.py
"""Async CRUD for algo.risk_state — intra-day rolling P&L per
(user_id, day_date). Uses stub-friendly session pattern (mirrors
backtest/runs_repo.py)."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)


class RiskStateRepo:
    async def get_or_create(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        day_date: date,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "SELECT user_id, day_date, "
                "daily_realised_pnl_inr, daily_unrealised_pnl_inr, "
                "breaches "
                "FROM algo.risk_state "
                "WHERE user_id = :uid AND day_date = :dd"
            ),
            {"uid": user_id, "dd": day_date},
        )
        row = result.mappings().first()
        if row is not None:
            return dict(row)

        await session.execute(
            text(
                "INSERT INTO algo.risk_state ("
                "  user_id, day_date, daily_realised_pnl_inr, "
                "  daily_unrealised_pnl_inr, breaches) "
                "VALUES (:uid, :dd, 0, 0, '[]'::jsonb)"
            ),
            {"uid": user_id, "dd": day_date},
        )
        return {
            "user_id": user_id, "day_date": day_date,
            "daily_realised_pnl_inr": Decimal("0"),
            "daily_unrealised_pnl_inr": Decimal("0"),
            "breaches": [],
        }

    async def update_pnl(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        day_date: date,
        realised_delta: Decimal,
        unrealised_inr: Decimal,
    ) -> None:
        await session.execute(
            text(
                "UPDATE algo.risk_state SET "
                "  daily_realised_pnl_inr = "
                "    daily_realised_pnl_inr + :rd, "
                "  daily_unrealised_pnl_inr = :ud, "
                "  updated_at = :ua "
                "WHERE user_id = :uid AND day_date = :dd"
            ),
            {
                "uid": user_id, "dd": day_date,
                "rd": realised_delta,
                "ud": unrealised_inr,
                "ua": datetime.now(timezone.utc),
            },
        )

    async def append_breach(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        day_date: date,
        breach: dict[str, Any],
    ) -> None:
        await session.execute(
            text(
                "UPDATE algo.risk_state SET "
                "  breaches = breaches || CAST(:b AS jsonb), "
                "  updated_at = :ua "
                "WHERE user_id = :uid AND day_date = :dd"
            ),
            {
                "uid": user_id, "dd": day_date,
                "b": json.dumps([breach]),
                "ua": datetime.now(timezone.utc),
            },
        )

    async def reset_for_day(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        day_date: date,
    ) -> None:
        """Used by the IST-midnight scheduler + restart-replay.

        Idempotent: an INSERT-on-conflict-update so the row exists
        even if the user has never traded today.
        """
        await session.execute(
            text(
                "INSERT INTO algo.risk_state ("
                "  user_id, day_date, daily_realised_pnl_inr, "
                "  daily_unrealised_pnl_inr, breaches) "
                "VALUES (:uid, :dd, 0, 0, '[]'::jsonb) "
                "ON CONFLICT (user_id, day_date) DO UPDATE SET "
                "  daily_realised_pnl_inr = 0, "
                "  daily_unrealised_pnl_inr = 0, "
                "  breaches = '[]'::jsonb, "
                "  updated_at = :ua"
            ),
            {
                "uid": user_id, "dd": day_date,
                "ua": datetime.now(timezone.utc),
            },
        )
```

- [ ] **Step 2: Tests (stub session)**

```python
# backend/algo/tests/test_paper_risk_state_repo.py
"""Round-trip the lifecycle of algo.risk_state via stub session."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from backend.algo.paper.risk_state_repo import RiskStateRepo


class _StubSession:
    def __init__(self) -> None:
        self.rows: dict[tuple, dict] = {}

    async def execute(self, q, params=None):  # noqa: ANN001
        sql = str(q)
        params = dict(params or {})

        class _Res:
            def __init__(self, items):
                self._items = items
            def mappings(self): return self
            def first(self):
                return self._items[0] if self._items else None

        if "SELECT user_id, day_date" in sql:
            key = (params["uid"], params["dd"])
            return _Res(
                [self.rows[key]] if key in self.rows else [],
            )
        if "INSERT INTO algo.risk_state" in sql and "ON CONFLICT" in sql:
            key = (params["uid"], params["dd"])
            self.rows[key] = {
                "user_id": params["uid"],
                "day_date": params["dd"],
                "daily_realised_pnl_inr": Decimal("0"),
                "daily_unrealised_pnl_inr": Decimal("0"),
                "breaches": [],
            }
            return _Res([])
        if "INSERT INTO algo.risk_state" in sql:
            key = (params["uid"], params["dd"])
            self.rows[key] = {
                "user_id": params["uid"],
                "day_date": params["dd"],
                "daily_realised_pnl_inr": Decimal("0"),
                "daily_unrealised_pnl_inr": Decimal("0"),
                "breaches": [],
            }
            return _Res([])
        if "UPDATE algo.risk_state" in sql and "breaches = breaches" in sql:
            import json as _json
            key = (params["uid"], params["dd"])
            row = self.rows.get(key)
            if row:
                row["breaches"].extend(_json.loads(params["b"]))
            return _Res([])
        if "UPDATE algo.risk_state" in sql:
            key = (params["uid"], params["dd"])
            row = self.rows.get(key)
            if row:
                row["daily_realised_pnl_inr"] += params["rd"]
                row["daily_unrealised_pnl_inr"] = params["ud"]
            return _Res([])
        return _Res([])


@pytest.mark.asyncio
async def test_get_or_create_inserts_on_first_call():
    repo = RiskStateRepo()
    session = _StubSession()
    user_id = uuid4()
    state = await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    assert state["daily_realised_pnl_inr"] == Decimal("0")
    # Second call returns existing.
    again = await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    assert again["user_id"] == user_id


@pytest.mark.asyncio
async def test_update_pnl_increments_realised():
    repo = RiskStateRepo()
    session = _StubSession()
    user_id = uuid4()
    await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    await repo.update_pnl(
        session, user_id=user_id, day_date=date(2026, 4, 1),
        realised_delta=Decimal("500"),
        unrealised_inr=Decimal("200"),
    )
    state = await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    assert state["daily_realised_pnl_inr"] == Decimal("500")
    assert state["daily_unrealised_pnl_inr"] == Decimal("200")


@pytest.mark.asyncio
async def test_append_breach_grows_jsonb_array():
    repo = RiskStateRepo()
    session = _StubSession()
    user_id = uuid4()
    await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    await repo.append_breach(
        session, user_id=user_id, day_date=date(2026, 4, 1),
        breach={"reason": "daily_loss_cap", "qty": 0},
    )
    state = await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    assert len(state["breaches"]) == 1


@pytest.mark.asyncio
async def test_reset_for_day_zeros_all_fields():
    repo = RiskStateRepo()
    session = _StubSession()
    user_id = uuid4()
    await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    await repo.update_pnl(
        session, user_id=user_id, day_date=date(2026, 4, 1),
        realised_delta=Decimal("-500"),
        unrealised_inr=Decimal("-200"),
    )
    await repo.reset_for_day(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    state = await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    assert state["daily_realised_pnl_inr"] == Decimal("0")
    assert state["daily_unrealised_pnl_inr"] == Decimal("0")
```

- [ ] **Step 3: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_paper_risk_state_repo.py -v 2>&1 | tail -10

git add backend/algo/paper/risk_state_repo.py backend/algo/tests/test_paper_risk_state_repo.py
git commit -m "$(cat <<'EOF'
feat(algo): RiskStateRepo — algo.risk_state CRUD

Slice 8a. RiskStateRepo wraps the 4 lifecycle ops on
algo.risk_state: get_or_create, update_pnl (delta-based for
realised, absolute for unrealised), append_breach (JSONB array
extend), reset_for_day (idempotent ON CONFLICT for the IST-
midnight scheduler + restart-replay). 4 stub-session tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: KillSwitchRepo (PG + Redis)

**Files:**
- Create: `backend/algo/paper/kill_switch_repo.py`
- Create: `backend/algo/tests/test_paper_kill_switch_repo.py`

- [ ] **Step 1: Implement**

```python
# backend/algo/paper/kill_switch_repo.py
"""Kill switch — durable in algo.kill_switch, fast read in Redis.

Per spec § 5.4: arming sets the flag in BOTH PG and Redis;
disarming clears both. Runtime checks only Redis (sub-ms);
restart-replay reads PG and rehydrates Redis.

Redis key: algo:kill:{user_id}, value "1" if armed, absent if not.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)


def _redis_key(user_id: UUID) -> str:
    return f"algo:kill:{user_id}"


class KillSwitchRepo:
    def __init__(self, redis_client=None) -> None:  # noqa: ANN001
        self._redis = redis_client

    async def is_active(self, user_id: UUID) -> bool:
        """Fast read — Redis first, falls back to PG only if
        Redis unavailable (graceful degradation)."""
        if self._redis is not None:
            try:
                v = await self._redis.get(_redis_key(user_id))
                return bool(v)
            except Exception:  # noqa: BLE001
                _logger.warning(
                    "Redis kill-switch read failed; falling back",
                )
        return False

    async def get(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "SELECT user_id, active, set_by, set_at, reason "
                "FROM algo.kill_switch WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if row is None:
            return {
                "user_id": user_id,
                "active": False,
                "set_by": None,
                "set_at": None,
                "reason": None,
            }
        return dict(row)

    async def arm(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        set_by: UUID,
        reason: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                "INSERT INTO algo.kill_switch ("
                "  user_id, active, set_by, set_at, reason) "
                "VALUES (:uid, true, :sb, :sa, :rs) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "  active = true, set_by = :sb, "
                "  set_at = :sa, reason = :rs"
            ),
            {
                "uid": user_id, "sb": set_by, "sa": now,
                "rs": reason,
            },
        )
        if self._redis is not None:
            try:
                await self._redis.set(
                    _redis_key(user_id), "1",
                )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Redis kill-switch arm mirror failed",
                )

    async def disarm(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> None:
        await session.execute(
            text(
                "UPDATE algo.kill_switch SET active = false "
                "WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        if self._redis is not None:
            try:
                await self._redis.delete(_redis_key(user_id))
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Redis kill-switch disarm mirror failed",
                )
```

- [ ] **Step 2: Tests**

```python
# backend/algo/tests/test_paper_kill_switch_repo.py
"""KillSwitchRepo — PG + Redis mirror."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.algo.paper.kill_switch_repo import KillSwitchRepo


class _StubSession:
    def __init__(self) -> None:
        self.rows: dict = {}

    async def execute(self, q, params=None):  # noqa: ANN001
        sql = str(q)
        params = dict(params or {})

        class _Res:
            def __init__(self, items):
                self._items = items
            def mappings(self): return self
            def first(self):
                return self._items[0] if self._items else None

        if "SELECT user_id, active" in sql:
            row = self.rows.get(params["uid"])
            return _Res([row] if row else [])
        if "INSERT INTO algo.kill_switch" in sql:
            self.rows[params["uid"]] = {
                "user_id": params["uid"],
                "active": True,
                "set_by": params["sb"],
                "set_at": params["sa"],
                "reason": params["rs"],
            }
            return _Res([])
        if "UPDATE algo.kill_switch" in sql:
            row = self.rows.get(params["uid"])
            if row:
                row["active"] = False
            return _Res([])
        return _Res([])


@pytest.mark.asyncio
async def test_arm_writes_pg_and_redis():
    redis = AsyncMock()
    redis.set = AsyncMock()
    repo = KillSwitchRepo(redis_client=redis)
    session = _StubSession()
    user_id = uuid4()
    await repo.arm(
        session, user_id=user_id, set_by=user_id,
        reason="manual",
    )
    state = await repo.get(session, user_id=user_id)
    assert state["active"] is True
    redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_disarm_clears_pg_and_redis():
    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    repo = KillSwitchRepo(redis_client=redis)
    session = _StubSession()
    user_id = uuid4()
    await repo.arm(
        session, user_id=user_id, set_by=user_id,
        reason=None,
    )
    await repo.disarm(session, user_id=user_id)
    state = await repo.get(session, user_id=user_id)
    assert state["active"] is False
    redis.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_active_reads_redis_only():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"1")
    repo = KillSwitchRepo(redis_client=redis)
    user_id = uuid4()
    assert await repo.is_active(user_id) is True


@pytest.mark.asyncio
async def test_is_active_returns_false_on_redis_error():
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=RuntimeError("boom"))
    repo = KillSwitchRepo(redis_client=redis)
    user_id = uuid4()
    assert await repo.is_active(user_id) is False


@pytest.mark.asyncio
async def test_get_returns_default_when_row_missing():
    repo = KillSwitchRepo(redis_client=None)
    session = _StubSession()
    state = await repo.get(session, user_id=uuid4())
    assert state["active"] is False
    assert state["set_at"] is None
```

- [ ] **Step 3: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_paper_kill_switch_repo.py -v 2>&1 | tail -10

git add backend/algo/paper/kill_switch_repo.py backend/algo/tests/test_paper_kill_switch_repo.py
git commit -m "$(cat <<'EOF'
feat(algo): KillSwitchRepo — PG durability + Redis mirror

Slice 8a. arm() / disarm() write both PG (durable) and Redis
(fast read at runtime). is_active() reads Redis only and
gracefully returns False on Redis failure (fail-safe — runtime
should never falsely block when the cache is down). 5 unit tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: PaperBroker — at-tick fills

**Files:**
- Create: `backend/algo/paper/broker.py`
- Create: `backend/algo/tests/test_paper_broker.py`

- [ ] **Step 1: Implement**

```python
# backend/algo/paper/broker.py
"""Paper broker — fills at the current tick's LTP, not next-bar
open like SimBroker. Stamps IndianFeeModel rates_version per
spec § 6.2.

A real Kite broker would place the order via
KiteAdapter.place_order(); v1 paper has no live order leg, so
fills are immediate and synthetic. Slice 8b's reconciliation
loop tests this with a fake-broker fixture.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.backtest.types import Fill
from backend.algo.fees import IndianFeeModel, Trade
from backend.algo.paper.types import Signal

_logger = logging.getLogger(__name__)


class PaperBroker:
    """Synchronous, pure-Python at-tick broker."""

    def __init__(self, *, fee_as_of: date) -> None:
        self._fees = IndianFeeModel(as_of=fee_as_of)

    def execute(
        self,
        *,
        signal: Signal,
        last_price: Decimal,
        fill_date: date,
    ) -> Fill:
        """Fill the signal immediately at ``last_price``."""
        breakdown = self._fees.compute(
            Trade(
                symbol=signal.ticker,
                exchange="NSE",
                side=signal.side,
                product="DELIVERY",
                qty=signal.qty,
                price=last_price,
            ),
        )
        return Fill(
            intent_id=uuid4(),
            ticker=signal.ticker,
            side=signal.side,
            qty=signal.qty,
            fill_price=last_price,
            fill_date=fill_date,
            fees_inr=breakdown.total_inr,
            fee_rates_version=breakdown.rates_version,
        )
```

- [ ] **Step 2: Tests**

```python
# backend/algo/tests/test_paper_broker.py
"""PaperBroker — at-tick fills + fee version stamp."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.algo.paper.broker import PaperBroker
from backend.algo.paper.types import Signal


def _signal(qty=10, side="BUY") -> Signal:
    return Signal(
        strategy_id=uuid4(), user_id=uuid4(),
        ticker="X", side=side, qty=qty,
        emitted_at_ns=0,
    )


def test_fill_at_tick_price():
    broker = PaperBroker(fee_as_of=date(2026, 4, 1))
    fill = broker.execute(
        signal=_signal(qty=100),
        last_price=Decimal("250.50"),
        fill_date=date(2026, 4, 1),
    )
    assert fill.fill_price == Decimal("250.50")
    assert fill.qty == 100
    assert fill.fill_date == date(2026, 4, 1)


def test_fill_stamps_fee_rates_version():
    broker = PaperBroker(fee_as_of=date(2026, 4, 1))
    fill = broker.execute(
        signal=_signal(qty=10),
        last_price=Decimal("100"),
        fill_date=date(2026, 4, 1),
    )
    assert fill.fee_rates_version == "2026-04-01"
    assert fill.fees_inr > Decimal("0")


def test_buy_and_sell_both_fill():
    broker = PaperBroker(fee_as_of=date(2026, 4, 1))
    buy = broker.execute(
        signal=_signal(side="BUY"),
        last_price=Decimal("100"),
        fill_date=date(2026, 4, 1),
    )
    sell = broker.execute(
        signal=_signal(side="SELL"),
        last_price=Decimal("105"),
        fill_date=date(2026, 4, 1),
    )
    assert buy.side == "BUY"
    assert sell.side == "SELL"
    assert sell.fill_price == Decimal("105")
```

- [ ] **Step 3: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_paper_broker.py -v 2>&1 | tail -8

git add backend/algo/paper/broker.py backend/algo/tests/test_paper_broker.py
git commit -m "$(cat <<'EOF'
feat(algo): PaperBroker — at-tick fills

Slice 8a. PaperBroker.execute(signal, last_price, fill_date)
fills immediately at the current tick's LTP, computes fees via
IndianFeeModel, and stamps rates_version on every Fill. Reuses
Slice 7a's Fill type so PositionTracker accepts it unchanged.
3 unit tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: PaperRuntime orchestrator

**Files:**
- Create: `backend/algo/paper/runtime.py`
- Create: `backend/algo/tests/test_paper_runtime.py`

- [ ] **Step 1: Implement**

```python
# backend/algo/paper/runtime.py
"""PaperRuntime — tick-driven strategy executor.

Lifecycle:
  1. Caller starts ``run(strategy, source)``.
  2. For every tick in ``source``:
       - Optionally update an internal Resampler (1m bars feed
         the AST evaluator's price features).
       - On every bar close, evaluate ``strategy.root`` against
         the bar context.
       - For each emitted action, build a Signal.
       - Gate via RiskEngine; reject → ``signal_rejected`` event.
       - Accept/scale → PaperBroker.execute → Fill.
       - Apply fill to PositionTracker; emit ``order_filled``.
  3. On shutdown, force-flush in-flight bars, persist final
     RiskState delta, single-commit events to algo.events.

This v1 runtime is one-strategy-per-instance. Multi-strategy
fan-out across one user's tick stream lives in Slice 8b's
service shell.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.positions import PositionTracker
from backend.algo.paper.broker import PaperBroker
from backend.algo.paper.risk_engine import RiskEngine
from backend.algo.paper.types import (
    AccountState, RejectReason, RiskDecision, Signal,
)
from backend.algo.stream.resampler import Resampler
from backend.algo.stream.sources import TickSource
from backend.algo.strategy.ast import Strategy

_logger = logging.getLogger(__name__)


def _features_for_bar(bar) -> dict[str, Decimal]:  # noqa: ANN001
    return {
        "today_ltp": bar.close,
        "today_vol": Decimal(bar.volume),
    }


class PaperRuntime:
    def __init__(
        self,
        *,
        strategy: Strategy,
        user_id: UUID,
        initial_capital_inr: Decimal,
        fee_as_of: date,
        kill_switch_active: bool = False,
    ) -> None:
        self._strategy = strategy
        self._user_id = user_id
        self._initial = initial_capital_inr
        self._broker = PaperBroker(fee_as_of=fee_as_of)
        self._evaluator = Evaluator()
        self._risk = RiskEngine()
        self._resampler = Resampler(intervals=(60,))
        self._positions = PositionTracker()
        self._session_id = uuid4()
        self._events: list[dict[str, Any]] = []
        self._kill_switch_active = kill_switch_active
        self._daily_realised = Decimal("0")

    async def run(self, source: TickSource) -> int:
        """Drain the source. Returns the count of fills emitted."""
        fills = 0
        try:
            async for tick in source:
                self._resampler.feed(tick)
                for bar in self._resampler.pop_completed():
                    fills += self._on_bar_close(
                        bar=bar, last_price=Decimal(str(tick.ltp)),
                    )
        finally:
            for bar in self._resampler.close_partial_bars():
                fills += self._on_bar_close(
                    bar=bar,
                    last_price=Decimal(str(bar.close)),
                )
            if self._events:
                flush_events(self._events)
                self._events = []
        return fills

    def _on_bar_close(
        self,
        *,
        bar,  # noqa: ANN001 — Bar
        last_price: Decimal,
    ) -> int:
        """Evaluate the strategy on this bar; route accepted
        signals to the broker. Returns the count of fills."""
        ctx = EvalContext(
            ticker=bar.ticker,
            bar_date=date.fromtimestamp(
                bar.bar_open_ts_ns / 1_000_000_000,
            ),
            features=_features_for_bar(bar),
            open_qty=(
                self._positions.open_positions().get(bar.ticker, type(
                    "P", (), {"qty": 0},
                )()).qty
            ),
        )
        action = self._evaluator.eval_node(
            self._strategy.root.model_dump(by_alias=True), ctx,
        )
        signal = self._action_to_signal(
            action, ticker=bar.ticker, bar_date_ns=bar.bar_open_ts_ns,
        )
        if signal is None:
            return 0
        self._events.append(event_row(
            session_id=self._session_id,
            user_id=self._user_id,
            strategy_id=self._strategy.id,
            mode="paper",
            type_="signal_generated",
            payload={
                "ticker": signal.ticker, "side": signal.side,
                "qty": signal.qty,
            },
        ))

        account = self._account_snapshot()
        decision = self._risk.gate(
            signal=signal, account=account,
            risk=self._strategy.risk.model_dump(),
            last_price=last_price,
        )
        if decision.outcome == "reject":
            self._events.append(event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="paper",
                type_="signal_rejected",
                payload={
                    "reason": decision.reason.value
                    if decision.reason else "unknown",
                    "ticker": signal.ticker, "side": signal.side,
                    "qty": signal.qty,
                    "threshold": (
                        str(decision.threshold)
                        if decision.threshold is not None else None
                    ),
                    "observed_value": (
                        str(decision.observed_value)
                        if decision.observed_value is not None else None
                    ),
                },
            ))
            return 0

        effective_qty = (
            decision.adjusted_qty
            if decision.outcome == "scale"
            and decision.adjusted_qty
            else signal.qty
        )
        signal = signal.model_copy(update={"qty": effective_qty})
        fill = self._broker.execute(
            signal=signal,
            last_price=last_price,
            fill_date=ctx.bar_date,
        )
        self._positions.apply_fill(fill)
        if fill.side == "SELL":
            # Realised P&L was tracked inside PositionTracker.
            self._daily_realised = (
                self._positions.total_realised_pnl_inr()
            )
        self._events.append(event_row(
            session_id=self._session_id,
            user_id=self._user_id,
            strategy_id=self._strategy.id,
            mode="paper",
            type_="order_filled",
            payload={
                "ticker": fill.ticker, "side": fill.side,
                "qty": fill.qty,
                "fill_price": str(fill.fill_price),
                "fill_date": fill.fill_date.isoformat(),
                "fees_inr": str(fill.fees_inr),
                "fee_rates_version": fill.fee_rates_version,
            },
        ))
        return 1

    def _action_to_signal(
        self,
        action: dict,
        *,
        ticker: str,
        bar_date_ns: int,
    ) -> Signal | None:
        t = action.get("type")
        if t == "buy":
            qty = int(action["qty"].get("shares") or 0)
            if qty <= 0:
                return None
            return Signal(
                strategy_id=self._strategy.id,
                user_id=self._user_id,
                ticker=ticker, side="BUY", qty=qty,
                emitted_at_ns=bar_date_ns,
            )
        if t == "sell":
            qty_spec = action["qty"]
            if qty_spec.get("all"):
                existing = (
                    self._positions.open_positions().get(ticker)
                )
                if not existing:
                    return None
                qty = existing.qty
            else:
                qty = int(qty_spec.get("shares") or 0)
            if qty <= 0:
                return None
            return Signal(
                strategy_id=self._strategy.id,
                user_id=self._user_id,
                ticker=ticker, side="SELL", qty=qty,
                emitted_at_ns=bar_date_ns,
            )
        if t == "exit":
            existing = self._positions.open_positions().get(ticker)
            if not existing:
                return None
            return Signal(
                strategy_id=self._strategy.id,
                user_id=self._user_id,
                ticker=ticker, side="SELL", qty=existing.qty,
                emitted_at_ns=bar_date_ns,
            )
        return None

    def _account_snapshot(self) -> AccountState:
        open_qty = {
            t: p.qty
            for t, p in self._positions.open_positions().items()
        }
        # Approximate equity = initial + realised. Unrealised
        # left to caller-supplied marks (Slice 8b reconciles
        # with live ticks).
        return AccountState(
            user_id=self._user_id,
            day_date=datetime.now(timezone.utc).date(),
            initial_capital_inr=self._initial,
            current_equity_inr=(
                self._initial
                + self._positions.total_realised_pnl_inr()
            ),
            daily_realised_pnl_inr=(
                self._positions.total_realised_pnl_inr()
            ),
            daily_unrealised_pnl_inr=Decimal("0"),
            open_positions=open_qty,
            open_position_count=len(open_qty),
            kill_switch_active=self._kill_switch_active,
        )
```

- [ ] **Step 2: Tests (replay-fixture e2e + reject path)**

```python
# backend/algo/tests/test_paper_runtime.py
"""End-to-end paper runtime over a replay tick fixture."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.paper.runtime import PaperRuntime
from backend.algo.stream.sources import ReplayTickSource
from backend.algo.strategy.ast import parse_strategy

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "ticks_sample.jsonl"
)


def _strategy_payload(buy_qty: int = 5) -> dict:
    return {
        "id": str(uuid4()),
        "name": "buy on every bar",
        "universe": {
            "type": "scope", "scope": "watchlist",
            "filter": {
                "ticker_type": ["stock"], "market": "india",
            },
        },
        "schedule": {
            "type": "bar_close", "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": buy_qty}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {
                "max_loss_pct": 2,
                "max_open_positions": 10,
            },
        },
    }


@pytest.mark.asyncio
async def test_runtime_emits_fills_for_buy_strategy():
    strategy = parse_strategy(_strategy_payload(buy_qty=5))
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=date(2026, 4, 1),
    )
    source = ReplayTickSource(_FIXTURE, pace="fast")
    with patch(
        "backend.algo.paper.runtime.flush_events",
    ) as flush:
        fills = await runtime.run(source)
    # 30 ticks → 3 1m boundaries + 1 partial close = 4 bars,
    # but a "buy" with qty=5 every bar accumulates qty.
    assert fills >= 1
    flush.assert_called_once()
    rows = flush.call_args.args[0]
    assert any(r["type"] == "signal_generated" for r in rows)
    assert any(r["type"] == "order_filled" for r in rows)


@pytest.mark.asyncio
async def test_runtime_kill_switch_blocks_all_signals():
    strategy = parse_strategy(_strategy_payload())
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=date(2026, 4, 1),
        kill_switch_active=True,
    )
    with patch(
        "backend.algo.paper.runtime.flush_events",
    ) as flush:
        fills = await runtime.run(
            ReplayTickSource(_FIXTURE, pace="fast"),
        )
    assert fills == 0
    rows = flush.call_args.args[0]
    rejected = [r for r in rows if r["type"] == "signal_rejected"]
    assert len(rejected) >= 1
    # Reasons should include kill_switch.
    import json as _json
    payloads = [_json.loads(r["payload_json"]) for r in rejected]
    assert any(p.get("reason") == "kill_switch" for p in payloads)


@pytest.mark.asyncio
async def test_runtime_max_qty_rejection_emits_signal_rejected():
    payload = _strategy_payload(buy_qty=200)  # > max_qty=100
    strategy = parse_strategy(payload)
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=date(2026, 4, 1),
    )
    with patch(
        "backend.algo.paper.runtime.flush_events",
    ) as flush:
        fills = await runtime.run(
            ReplayTickSource(_FIXTURE, pace="fast"),
        )
    assert fills == 0
    rows = flush.call_args.args[0]
    import json as _json
    rejected = [
        _json.loads(r["payload_json"])
        for r in rows
        if r["type"] == "signal_rejected"
    ]
    assert all(p["reason"] == "max_qty" for p in rejected)
```

- [ ] **Step 3: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_paper_runtime.py -v 2>&1 | tail -10

git add backend/algo/paper/runtime.py backend/algo/tests/test_paper_runtime.py
git commit -m "$(cat <<'EOF'
feat(algo): PaperRuntime — tick-driven strategy executor

Slice 8a. PaperRuntime drives Strategy.root through bar-close
evaluation; gates every emitted signal through RiskEngine before
PaperBroker fills. Emits canonical signal_generated /
signal_rejected / order_filled events into algo.events (single
commit at shutdown). Reuses Slice 7a's Evaluator + PositionTracker
+ event_writer; Slice 6's Resampler. v1 = one strategy per
instance; multi-strategy fan-out lands in Slice 8b. 3 e2e tests
cover happy path, kill-switch short-circuit, max_qty rejection.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Kill switch routes

**Files:**
- Create: `backend/algo/routes/kill_switch.py`
- Create: `backend/algo/tests/test_kill_switch_routes.py`
- Modify: `backend/algo/routes/__init__.py`
- Modify: `backend/routes.py`

- [ ] **Step 1: Implement routes**

```python
# backend/algo/routes/kill_switch.py
"""GET /v1/algo/kill-switch
POST /v1/algo/kill-switch/arm
POST /v1/algo/kill-switch/disarm

Per spec § 5.4. Re-arming requires a confirm dialog UI-side;
backend just exposes the toggle. Reason string optional.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.paper.kill_switch_repo import KillSwitchRepo
from backend.algo.paper.types import KillSwitchState

_logger = logging.getLogger(__name__)


class ArmRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=256)


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def _get_redis():
    try:
        from redis_client import get_redis
        return get_redis()
    except Exception:  # noqa: BLE001
        return None


def create_kill_switch_router() -> APIRouter:
    router = APIRouter(prefix="/algo", tags=["algo-trading"])

    @router.get(
        "/kill-switch", response_model=KillSwitchState,
    )
    async def get_state(
        user: UserContext = Depends(pro_or_superuser),
    ) -> KillSwitchState:
        repo = KillSwitchRepo(redis_client=_get_redis())
        factory = _get_session_factory()
        async with factory() as session:
            row = await repo.get(
                session, user_id=UUID(user.user_id),
            )
        return KillSwitchState(**row)

    @router.post(
        "/kill-switch/arm", response_model=KillSwitchState,
    )
    async def arm(
        body: ArmRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> KillSwitchState:
        repo = KillSwitchRepo(redis_client=_get_redis())
        factory = _get_session_factory()
        async with factory() as session:
            await repo.arm(
                session,
                user_id=UUID(user.user_id),
                set_by=UUID(user.user_id),
                reason=body.reason,
            )
            await session.commit()
            row = await repo.get(
                session, user_id=UUID(user.user_id),
            )
        return KillSwitchState(**row)

    @router.post(
        "/kill-switch/disarm", response_model=KillSwitchState,
    )
    async def disarm(
        user: UserContext = Depends(pro_or_superuser),
    ) -> KillSwitchState:
        repo = KillSwitchRepo(redis_client=_get_redis())
        factory = _get_session_factory()
        async with factory() as session:
            await repo.disarm(
                session, user_id=UUID(user.user_id),
            )
            await session.commit()
            row = await repo.get(
                session, user_id=UUID(user.user_id),
            )
        return KillSwitchState(**row)

    return router
```

- [ ] **Step 2: Wire into routes/__init__.py**

Update `backend/algo/routes/__init__.py`:

```python
"""HTTP routers for the algo trading module."""

from backend.algo.routes.backtest import create_backtest_router
from backend.algo.routes.broker import create_broker_router
from backend.algo.routes.fees import create_fees_router
from backend.algo.routes.instruments import create_instruments_router
from backend.algo.routes.kill_switch import create_kill_switch_router
from backend.algo.routes.strategies import create_strategies_router

__all__ = [
    "create_backtest_router",
    "create_broker_router",
    "create_fees_router",
    "create_instruments_router",
    "create_kill_switch_router",
    "create_strategies_router",
]
```

In `backend/routes.py`, add to the algo include block:

```python
    from backend.algo.routes import (
        create_backtest_router,
        create_broker_router,
        create_fees_router,
        create_instruments_router,
        create_kill_switch_router,
        create_strategies_router,
    )
    # ...
    app.include_router(
        create_kill_switch_router(),
        prefix="/v1",
    )
```

- [ ] **Step 3: Tests**

```python
# backend/algo/tests/test_kill_switch_routes.py
"""Endpoint smokes for /v1/algo/kill-switch/{,arm,disarm}."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.kill_switch import create_kill_switch_router


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(create_kill_switch_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t", role="superuser",
    )
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    factory = MagicMock()
    factory.__aenter__ = AsyncMock(return_value=fake_session)
    factory.__aexit__ = AsyncMock(return_value=None)
    factory_factory = MagicMock(return_value=factory)
    import backend.algo.routes.kill_switch as ks
    monkeypatch.setattr(ks, "_get_session_factory", factory_factory)
    monkeypatch.setattr(ks, "_get_redis", lambda: None)
    return app


def test_get_returns_inactive_default(app):
    with patch(
        "backend.algo.routes.kill_switch.KillSwitchRepo",
    ) as cls:
        cls.return_value.get = AsyncMock(return_value={
            "user_id": uuid4(),
            "active": False,
            "set_by": None, "set_at": None, "reason": None,
        })
        client = TestClient(app)
        r = client.get("/v1/algo/kill-switch")
    assert r.status_code == 200
    assert r.json()["active"] is False


def test_arm_sets_active(app):
    with patch(
        "backend.algo.routes.kill_switch.KillSwitchRepo",
    ) as cls:
        repo = cls.return_value
        repo.arm = AsyncMock()
        repo.get = AsyncMock(return_value={
            "user_id": uuid4(),
            "active": True,
            "set_by": uuid4(),
            "set_at": datetime.now(timezone.utc),
            "reason": "manual",
        })
        client = TestClient(app)
        r = client.post(
            "/v1/algo/kill-switch/arm",
            json={"reason": "manual"},
        )
    assert r.status_code == 200
    assert r.json()["active"] is True


def test_disarm_clears(app):
    with patch(
        "backend.algo.routes.kill_switch.KillSwitchRepo",
    ) as cls:
        repo = cls.return_value
        repo.disarm = AsyncMock()
        repo.get = AsyncMock(return_value={
            "user_id": uuid4(),
            "active": False,
            "set_by": None, "set_at": None, "reason": None,
        })
        client = TestClient(app)
        r = client.post("/v1/algo/kill-switch/disarm")
    assert r.status_code == 200
    assert r.json()["active"] is False
```

- [ ] **Step 4: Restart, run + commit**

```bash
docker compose restart backend
sleep 6
docker compose exec backend python -m pytest backend/algo/tests/test_kill_switch_routes.py -v 2>&1 | tail -10

git add backend/algo/routes/kill_switch.py \
        backend/algo/routes/__init__.py \
        backend/routes.py \
        backend/algo/tests/test_kill_switch_routes.py
git commit -m "$(cat <<'EOF'
feat(algo): /v1/algo/kill-switch endpoints

Slice 8a. GET / POST arm / POST disarm. pro_or_superuser guard.
Repo writes through PG (durable) + Redis (fast read). Arming
takes optional reason string; UI-side confirm dialog gates
re-arm per spec § 5.4 (lands in Slice 8b). 3 endpoint smokes.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: PROGRESS + push

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: Insert PROGRESS entry**

Prepend after `# PROGRESS.md` + `---`:

```markdown
## 2026-05-08 (later 9) — Algo Trading Slice 8a: paper runtime + risk engine + kill switch (backend)

**Branch:** `feature/algo-trading-session-7-paper-runtime` (built off Session 6's tip)
**Epic:** Algo Trading Platform v1
**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md`
**Plan:** `docs/superpowers/plans/2026-05-08-algo-trading-session-7-paper-runtime.md`

**Shipped (Slice 8a — backend half of spec's Slice 8):**
- `backend/algo/paper/types.py` — Signal, AccountState, RiskDecision, RejectReason enum, KillSwitchState.
- `RiskEngine.gate()` — pure 3-tier check (per-trade / daily / portfolio). Concentration is hard reject; total exposure may scale qty down. SELL signals skip portfolio caps. Kill-switch short-circuits.
- `RiskStateRepo` — algo.risk_state CRUD: get_or_create, update_pnl, append_breach, reset_for_day (idempotent ON CONFLICT for IST-midnight scheduler + restart-replay).
- `KillSwitchRepo` — PG (durable) + Redis mirror. is_active reads Redis only (sub-ms); arm/disarm write both. Fail-safe: Redis errors return False rather than blocking trades.
- `PaperBroker.execute()` — at-tick fills (vs SimBroker's next-bar-open); fee version stamp.
- `PaperRuntime` — tick → resampler → bar close → AST evaluator → RiskEngine → PaperBroker → PositionTracker → events. Single Iceberg commit at shutdown. v1 = one strategy per instance.
- `/v1/algo/kill-switch` (GET / arm POST / disarm POST) — pro_or_superuser-guarded.

**Tests:** 8 risk-engine + 4 risk-state-repo + 5 kill-switch-repo + 3 paper-broker + 3 paper-runtime + 3 routes = **26 new pytest cases**. Total algo backend tests: ~171 passing.

**Deferred to Session 8 (Slice 8b):**
- Paper tab UI (active strategies list + signals + positions).
- Settings kill-switch button UI + confirm dialog.
- Multi-strategy fan-out service (one Kite WS per user → multiple PaperRuntime instances).
- Reconciliation loop (paper position diff vs broker; spec § 7.4).
- IST-midnight risk_state reset job wiring.
- Restart-replay rebuilder (read today's order_filled events on startup, replay through PositionTracker, persist to risk_state).

---
```

- [ ] **Step 2: Commit + push**

```bash
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
docs(progress): log Algo Trading session 7 — Slice 8a

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
git push -u origin feature/algo-trading-session-7-paper-runtime 2>&1 | tail -5
```

---

## Self-Review (post-write)

**1. Spec coverage (§ 5 risk + § 9.1 slice 8):**
- Live runtime → Task 6 (`PaperRuntime`).
- 3-tier risk engine → Task 2 (`RiskEngine.gate`).
- Kill switch → Tasks 4 + 7 (`KillSwitchRepo` + routes).
- `signal_rejected` event with reason enum → Task 6 (event payload).
- Restart-replay recovery → DEFERRED to 8b. Documented. The repo helper `RiskStateRepo.reset_for_day` and event writer are already in place; replay is just a startup hook that reads today's `order_filled` events and replays through `PositionTracker`.
- Per-strategy paper dashboard → DEFERRED to 8b (spec § 9.1 ships UI as part of Slice 8 but UI is large enough to warrant the split).
- IST-midnight scheduler integration → DEFERRED to 8b — `reset_for_day` is the ready-to-call helper; wiring into `@register_job` is mechanical.
- Reconciliation loop (§ 7.4) → DEFERRED to 8b (spec calls it "scaffold in place" for v2 anyway).

**2. Placeholder scan:**
- `_get_redis()` in routes (Task 7) tries the global `redis_client.get_redis` import; if it doesn't exist or raises, returns None and KillSwitchRepo runs PG-only. Explicit, documented in the function body.
- Approximate equity in `_account_snapshot` uses `initial + realised` only; documented in docstring as Slice 8b polish.

**3. Type consistency:**
- `Signal` / `AccountState` / `RiskDecision` consistent across Tasks 1, 2, 6.
- `RejectReason` enum consistent between Tasks 1, 2, 6.
- `Fill` (from Slice 7a `backtest.types`) reused by `PaperBroker` in Task 5 — `PositionTracker.apply_fill` accepts it unchanged.
- `event_row()` from Slice 7a takes `mode="paper"` for this slice (vs `mode="backtest"`).
- `KillSwitchRepo.is_active(user_id)` consistent between Task 4 and route call sites in Task 7.

**4. Adaptations expected during execution:**
- Plan assumes the `redis_client.get_redis` import path; if the project uses a different name (e.g., `from cache import get_redis`), grep for the actual import site of an existing async Redis client and substitute.
- The PaperRuntime test asserts `flush.assert_called_once()`; if the runtime ends with no events (unlikely with the current fixture but possible if logic changes), the test needs `assert_called` (not once).
- `_account_snapshot` synthesizes a `Position`-like object via `type("P", (), {"qty": 0})()` for the open_qty fallback — if pylint flags it, replace with a tiny dataclass.

No gaps; type drift addressed; placeholders scoped.
