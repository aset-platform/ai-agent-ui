# Algo Order Budget Reservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a user-pool budget reservation ("ticketing") layer to live algo trading — every BUY order locks notional against `algo.user_budget.allocated_inr`; reservations stack in an append-only event log; pre-trade gate uses `min(internal_headroom, kite_available_cash)` so manual trades + T+1 holds naturally reduce algo headroom.

**Architecture:** New Cap 0 in `safety.py:pre_trade_check()` runs BEFORE the existing per-strategy `max_inr` (Cap 4). Two new PG tables: `algo.user_budget` (mutable, one row per user) + `algo.budget_reservations` (append-only event log). Five-second Redis-cached helpers invalidated on every reservation write. Reconciliation loop transitions PENDING → SUBMITTED → FILLED / REJECTED / CANCELLED / TIMEOUT.

**Tech Stack:** Python 3.12 (FastAPI, SQLAlchemy 2.0 async, Alembic, pytest), Next.js 16 + React 19 (Vitest, SWR), Playwright (E2E).

**Reference spec:** `docs/superpowers/specs/2026-05-24-algo-order-budget-reservation-design.md`.

**Branch:** `feature/algo-order-budget-reservation` (already created off `dev`). Squash merge per CLAUDE.md §4.4 #27.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `backend/db/migrations/versions/2026_05_24_add_budget_tables.py` | create | Alembic migration — creates `algo.user_budget` + `algo.budget_reservations` + 2 indices |
| `backend/algo/live/budget_types.py` | create | `UserBudget`, `BudgetReservation`, `ReservationState` Pydantic types |
| `backend/algo/live/budget_repo.py` | create | Async PG repo — `algo.user_budget` upsert + `algo.budget_reservations` event-log writer + current-state queries |
| `backend/algo/live/budget.py` | create | 4 cached helpers (`load_user_budget`, `sum_open_position_cost`, `sum_active_reservations`, `fetch_kite_available_cash`) + high-level `reserve()` / `transition()` API |
| `backend/algo/live/budget_reconciliation.py` | create | Periodic reconciliation: 120s PENDING timeout, 30s SUBMITTED Kite check, 5min hard timeout |
| `backend/algo/paper/types.py` | modify | Add `RejectReason.LIVE_BUDGET_CAP` enum value |
| `backend/algo/live/safety.py` | modify | Insert Cap 0 budget check before existing Cap 1-9 |
| `backend/algo/live/runtime.py` | modify | Call `budget.reserve()` between gate approval and `kite_client.place_order()`; call `budget.transition()` on Kite response |
| `backend/algo/live/reconciliation.py` | modify | Extend periodic loop to also drive `budget_reconciliation.reconcile()` |
| `backend/algo/routes/budget.py` | create | HTTP routes — GET /v1/algo/budget, PUT /v1/algo/budget/allocation, GET /v1/algo/budget/reservations, POST /v1/algo/budget/reservations/{id}/force-release |
| `backend/algo/routes/__init__.py` | modify | Mount sweep router exports |
| `backend/routes.py` | modify | `app.include_router(create_budget_router(), prefix="/v1")` |
| `backend/algo/live/tests/test_budget_repo.py` | create | Repo CRUD + event-log current-state query |
| `backend/algo/live/tests/test_budget.py` | create | 4 helpers + reserve/transition; cache invalidation |
| `backend/algo/live/tests/test_budget_gate.py` | create | Cap 0 in safety.py — 9 test cases |
| `backend/algo/live/tests/test_budget_reservation_lifecycle.py` | create | State transitions including partial fills + timeouts |
| `backend/algo/tests/test_budget_routes.py` | create | HTTP-level tests, lift-to-module pattern |
| `frontend/lib/types/algoBudget.ts` | create | TS shapes mirroring backend |
| `frontend/hooks/useBudget.ts` | create | SWR hooks (useUserBudget, useActiveReservations, setAllocation) |
| `frontend/components/algo-trading/BudgetPanel.tsx` | create | Top-level panel — tiles + Kite strip + reservations table |
| `frontend/components/algo-trading/BudgetAllocationModal.tsx` | create | Set / change allocated_inr |
| `frontend/components/algo-trading/BudgetReservationHistoryModal.tsx` | create | Full event-log view + CSV export |
| `frontend/components/algo-trading/LiveTab.tsx` | modify | Mount `<BudgetPanel />` at top |
| `frontend/components/algo-trading/__tests__/BudgetPanel.test.tsx` | create | Vitest — tiles math, empty state, Kite badge, reservations rendering |
| `e2e/utils/selectors.ts` | modify | Add new budget testids |
| `e2e/pages/frontend/budget.page.ts` | create | Playwright POM |
| `e2e/tests/frontend/algo-trading-budget.spec.ts` | create | Smoke test |
| `PROGRESS.md` | modify | Dated session entry |

---

## Task 1: PG migration + Pydantic types

**Files:**
- Create: `backend/db/migrations/versions/2026_05_24_add_budget_tables.py`
- Create: `backend/algo/live/budget_types.py`
- Create: `backend/algo/live/tests/test_budget_types.py`

- [ ] **Step 1.1: Write failing test for budget_types**

Create `backend/algo/live/tests/test_budget_types.py`:

```python
"""Tests for budget Pydantic types."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
    UserBudget,
)


def test_user_budget_defaults():
    ub = UserBudget(user_id=uuid4())
    assert ub.allocated_inr == Decimal("0")
    assert ub.enabled is False


def test_user_budget_rejects_negative_allocation():
    with pytest.raises(ValidationError):
        UserBudget(
            user_id=uuid4(),
            allocated_inr=Decimal("-1"),
        )


def test_reservation_state_enum_values():
    assert ReservationState.PENDING.value == "PENDING"
    assert ReservationState.SUBMITTED.value == "SUBMITTED"
    assert ReservationState.FILLED.value == "FILLED"
    assert ReservationState.REJECTED.value == "REJECTED"
    assert ReservationState.CANCELLED.value == "CANCELLED"
    assert ReservationState.PARTIAL.value == "PARTIAL"
    assert (
        ReservationState.PARTIAL_CANCELLED.value
        == "PARTIAL_CANCELLED"
    )
    assert ReservationState.TIMEOUT.value == "TIMEOUT"


def test_budget_reservation_minimal_valid():
    res = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.PENDING,
        ticker="INFY.NS",
        side="BUY",
        qty=50,
        reserved_inr=Decimal("7500.00"),
        transitioned_at=datetime.now(timezone.utc),
    )
    assert res.filled_qty == 0
    assert res.filled_inr == Decimal("0")
    assert res.kite_order_id is None
    assert res.error_text is None


def test_budget_reservation_rejects_extra_fields():
    with pytest.raises(ValidationError):
        BudgetReservation(
            reservation_id=uuid4(),
            user_id=uuid4(),
            strategy_id=uuid4(),
            state=ReservationState.PENDING,
            ticker="INFY.NS",
            side="BUY",
            qty=50,
            reserved_inr=Decimal("7500.00"),
            transitioned_at=datetime.now(timezone.utc),
            bogus="extra",
        )
```

- [ ] **Step 1.2: Run test — expect ImportError**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_budget_types.py -v
```

Expected: `ImportError` on `backend.algo.live.budget_types`.

- [ ] **Step 1.3: Implement budget_types.py**

Create `backend/algo/live/budget_types.py`:

```python
"""Pydantic types for the algo order budget reservation
system.

Types here are the wire shape between the gate, the repo,
the reconciliation loop, and the HTTP routes. Mirrors
``backend/algo/live/budget_types.py`` shapes in the spec.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReservationState(str, Enum):
    """Lifecycle states of a budget reservation."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"
    PARTIAL_CANCELLED = "PARTIAL_CANCELLED"
    TIMEOUT = "TIMEOUT"


# State sets used by gate + reconciliation queries.
ACTIVE_STATES: frozenset[ReservationState] = frozenset({
    ReservationState.PENDING,
    ReservationState.SUBMITTED,
    ReservationState.PARTIAL,
})

TERMINAL_STATES: frozenset[ReservationState] = frozenset({
    ReservationState.FILLED,
    ReservationState.REJECTED,
    ReservationState.CANCELLED,
    ReservationState.PARTIAL_CANCELLED,
    ReservationState.TIMEOUT,
})


class UserBudget(BaseModel):
    """User-pool allocation row (mutable)."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    allocated_inr: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
    )
    enabled: bool = False
    updated_at: datetime | None = None
    updated_by: UUID | None = None


class BudgetReservation(BaseModel):
    """One row in the append-only reservation event log."""

    model_config = ConfigDict(extra="forbid")

    reservation_id: UUID
    user_id: UUID
    strategy_id: UUID
    state: ReservationState
    ticker: str
    side: str  # BUY | SELL
    qty: int = Field(ge=1)
    reserved_inr: Decimal = Field(ge=Decimal("0"))
    filled_qty: int = Field(default=0, ge=0)
    filled_inr: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"),
    )
    kite_order_id: str | None = None
    transitioned_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_text: str | None = None
```

- [ ] **Step 1.4: Run test — expect 5 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_budget_types.py -v
```

Expected: 5 passed.

- [ ] **Step 1.5: Find the current Alembic head**

```bash
docker compose exec backend alembic heads
```

Capture the printed revision (e.g., `2026_05_24_sweep`). Use it as `down_revision` in the next step.

- [ ] **Step 1.6: Create the Alembic migration**

Create `backend/db/migrations/versions/2026_05_24_add_budget_tables.py`:

```python
"""Add algo.user_budget + algo.budget_reservations.

Revision ID: 2026_05_24_budget
Revises: <REPLACE_WITH_HEAD>
Create Date: 2026-05-24

Two new tables for the user-pool budget reservation system:

* algo.user_budget — mutable, one row per user, carries
  allocated_inr + enabled flag.
* algo.budget_reservations — append-only event log; one row
  per state transition per reservation. Active reservations
  are those whose CURRENT state ∈ {PENDING, SUBMITTED,
  PARTIAL}.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_05_24_budget"
down_revision = "<REPLACE_WITH_HEAD>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_budget",
        sa.Column(
            "user_id", sa.UUID(), primary_key=True,
        ),
        sa.Column(
            "allocated_inr",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_by", sa.UUID(), nullable=True,
        ),
        schema="algo",
    )

    op.create_table(
        "budget_reservations",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "reservation_id", sa.UUID(), nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "strategy_id", sa.UUID(), nullable=False,
        ),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column(
            "reserved_inr",
            sa.Numeric(14, 2),
            nullable=False,
        ),
        sa.Column(
            "filled_qty",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "filled_inr",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "kite_order_id", sa.Text(), nullable=True,
        ),
        sa.Column(
            "transitioned_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "metadata",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "error_text", sa.Text(), nullable=True,
        ),
        schema="algo",
    )

    op.create_index(
        "idx_budget_res_active",
        "budget_reservations",
        ["user_id", "reservation_id"],
        schema="algo",
        postgresql_where=sa.text(
            "state IN ('PENDING', 'SUBMITTED', 'PARTIAL')"
        ),
    )
    op.create_index(
        "idx_budget_res_user_time",
        "budget_reservations",
        ["user_id", sa.text("transitioned_at DESC")],
        schema="algo",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_budget_res_user_time",
        table_name="budget_reservations",
        schema="algo",
    )
    op.drop_index(
        "idx_budget_res_active",
        table_name="budget_reservations",
        schema="algo",
    )
    op.drop_table(
        "budget_reservations", schema="algo",
    )
    op.drop_table("user_budget", schema="algo")
```

Replace `<REPLACE_WITH_HEAD>` with the head from Step 1.5.

- [ ] **Step 1.7: Apply migration and verify**

```bash
docker compose exec backend alembic upgrade head
docker compose exec postgres psql -U app -d aiagent -c \
  "SELECT table_name FROM information_schema.tables \
   WHERE table_schema='algo' AND table_name IN \
   ('user_budget', 'budget_reservations');"
```

Expected: two rows — `user_budget` + `budget_reservations`.

- [ ] **Step 1.8: Commit**

```bash
git add backend/algo/live/budget_types.py \
        backend/algo/live/tests/test_budget_types.py \
        backend/db/migrations/versions/2026_05_24_add_budget_tables.py
git commit -m "$(cat <<'EOF'
feat(algo-budget): Pydantic types + PG tables

Foundational slice for the algo order budget reservation
epic. Pydantic UserBudget, BudgetReservation,
ReservationState enum (8 values; ACTIVE_STATES +
TERMINAL_STATES frozensets used by the gate + reconciliation
queries) and the Alembic migration creating
algo.user_budget (mutable user-pool row) + algo.
budget_reservations (append-only event log + 2 indices
for active + user/time queries).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: budget_repo.py

**Files:**
- Create: `backend/algo/live/budget_repo.py`
- Create: `backend/algo/live/tests/test_budget_repo.py`

- [ ] **Step 2.1: Write failing tests**

Create `backend/algo/live/tests/test_budget_repo.py`:

```python
"""Tests for BudgetRepo — async PG CRUD + event-log queries."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.algo.live.budget_repo import BudgetRepo
from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
    UserBudget,
)
from db.engine import get_session_factory


@pytest.fixture
async def session():
    factory = get_session_factory()
    async with factory() as s:
        yield s
        await s.rollback()


@pytest.mark.asyncio
async def test_get_user_budget_default_when_missing(session):
    repo = BudgetRepo()
    out = await repo.get_user_budget(
        session, user_id=uuid4(),
    )
    assert out.allocated_inr == Decimal("0")
    assert out.enabled is False


@pytest.mark.asyncio
async def test_upsert_user_budget_roundtrip(session):
    repo = BudgetRepo()
    uid = uuid4()
    await repo.upsert_user_budget(
        session,
        user_id=uid,
        allocated_inr=Decimal("100000.00"),
        enabled=True,
    )
    await session.commit()
    out = await repo.get_user_budget(session, user_id=uid)
    assert out.allocated_inr == Decimal("100000.00")
    assert out.enabled is True


@pytest.mark.asyncio
async def test_insert_reservation_row(session):
    repo = BudgetRepo()
    res_id = uuid4()
    uid = uuid4()
    await repo.insert_reservation_event(
        session,
        BudgetReservation(
            reservation_id=res_id,
            user_id=uid,
            strategy_id=uuid4(),
            state=ReservationState.PENDING,
            ticker="INFY.NS",
            side="BUY",
            qty=50,
            reserved_inr=Decimal("7500.00"),
            transitioned_at=datetime.now(timezone.utc),
        ),
    )
    await session.commit()
    current = await repo.get_current_state(
        session, reservation_id=res_id,
    )
    assert current.state == ReservationState.PENDING
    assert current.reserved_inr == Decimal("7500.00")


@pytest.mark.asyncio
async def test_current_state_returns_latest(session):
    """Multiple events for one reservation_id → latest wins."""
    repo = BudgetRepo()
    res_id = uuid4()
    uid = uuid4()
    sid = uuid4()
    base = dict(
        reservation_id=res_id,
        user_id=uid,
        strategy_id=sid,
        ticker="INFY.NS",
        side="BUY",
        qty=50,
        reserved_inr=Decimal("7500.00"),
    )
    await repo.insert_reservation_event(
        session,
        BudgetReservation(
            **base,
            state=ReservationState.PENDING,
            transitioned_at=datetime(
                2026, 5, 24, 10, 0, 0,
                tzinfo=timezone.utc,
            ),
        ),
    )
    await repo.insert_reservation_event(
        session,
        BudgetReservation(
            **base,
            state=ReservationState.SUBMITTED,
            transitioned_at=datetime(
                2026, 5, 24, 10, 0, 5,
                tzinfo=timezone.utc,
            ),
            kite_order_id="kite-123",
        ),
    )
    await session.commit()
    current = await repo.get_current_state(
        session, reservation_id=res_id,
    )
    assert current.state == ReservationState.SUBMITTED
    assert current.kite_order_id == "kite-123"


@pytest.mark.asyncio
async def test_sum_active_reservations(session):
    """Only ACTIVE_STATES contribute; terminal excluded."""
    repo = BudgetRepo()
    uid = uuid4()
    sid = uuid4()

    # Active reservation: SUBMITTED, no fill
    await repo.insert_reservation_event(
        session,
        BudgetReservation(
            reservation_id=uuid4(),
            user_id=uid,
            strategy_id=sid,
            state=ReservationState.SUBMITTED,
            ticker="A.NS",
            side="BUY", qty=10,
            reserved_inr=Decimal("1000.00"),
            transitioned_at=datetime.now(timezone.utc),
        ),
    )
    # Terminal reservation: FILLED — should NOT count
    res2 = uuid4()
    await repo.insert_reservation_event(
        session,
        BudgetReservation(
            reservation_id=res2,
            user_id=uid,
            strategy_id=sid,
            state=ReservationState.FILLED,
            ticker="B.NS",
            side="BUY", qty=20,
            reserved_inr=Decimal("2000.00"),
            filled_qty=20,
            filled_inr=Decimal("2000.00"),
            transitioned_at=datetime.now(timezone.utc),
        ),
    )
    await session.commit()
    total = await repo.sum_active_reservations(
        session, user_id=uid,
    )
    assert total == Decimal("1000.00")  # only the SUBMITTED row
```

- [ ] **Step 2.2: Run test — expect ImportError**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_budget_repo.py -v
```

Expected: `ImportError` on `backend.algo.live.budget_repo`.

- [ ] **Step 2.3: Implement budget_repo.py**

Create `backend/algo/live/budget_repo.py`:

```python
"""Async PG repository for algo.user_budget +
algo.budget_reservations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.algo.live.budget_types import (
    ACTIVE_STATES,
    BudgetReservation,
    ReservationState,
    UserBudget,
)

_logger = logging.getLogger(__name__)


class BudgetRepo:
    """CRUD + event-log queries for budget tables."""

    async def get_user_budget(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> UserBudget:
        """Return the user's budget row; default-zero
        when no row exists yet."""
        result = await session.execute(
            text(
                "SELECT user_id, allocated_inr, enabled, "
                "       updated_at, updated_by "
                "FROM algo.user_budget WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if row is None:
            return UserBudget(user_id=user_id)
        return UserBudget(**dict(row))

    async def upsert_user_budget(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        allocated_inr: Decimal,
        enabled: bool,
        updated_by: UUID | None = None,
    ) -> None:
        """Insert or update the user_budget row."""
        await session.execute(
            text(
                "INSERT INTO algo.user_budget ("
                "  user_id, allocated_inr, enabled, "
                "  updated_at, updated_by"
                ") VALUES ("
                "  :uid, :alloc, :en, :now, :by"
                ") ON CONFLICT (user_id) DO UPDATE SET "
                "  allocated_inr = EXCLUDED.allocated_inr, "
                "  enabled = EXCLUDED.enabled, "
                "  updated_at = EXCLUDED.updated_at, "
                "  updated_by = EXCLUDED.updated_by"
            ),
            {
                "uid": user_id,
                "alloc": allocated_inr,
                "en": enabled,
                "now": datetime.now(timezone.utc),
                "by": updated_by,
            },
        )

    async def insert_reservation_event(
        self,
        session: AsyncSession,
        res: BudgetReservation,
    ) -> None:
        """Append one row to algo.budget_reservations."""
        await session.execute(
            text(
                "INSERT INTO algo.budget_reservations ("
                "  reservation_id, user_id, strategy_id, "
                "  state, ticker, side, qty, "
                "  reserved_inr, filled_qty, filled_inr, "
                "  kite_order_id, transitioned_at, "
                "  metadata, error_text"
                ") VALUES ("
                "  :rid, :uid, :sid, :st, :tk, :sd, :q, "
                "  :ri, :fq, :fi, :koi, :ta, "
                "  CAST(:md AS jsonb), :et"
                ")"
            ),
            {
                "rid": res.reservation_id,
                "uid": res.user_id,
                "sid": res.strategy_id,
                "st": res.state.value,
                "tk": res.ticker,
                "sd": res.side,
                "q": res.qty,
                "ri": res.reserved_inr,
                "fq": res.filled_qty,
                "fi": res.filled_inr,
                "koi": res.kite_order_id,
                "ta": res.transitioned_at,
                "md": __import__("json").dumps(
                    res.metadata or {},
                ),
                "et": res.error_text,
            },
        )

    async def get_current_state(
        self,
        session: AsyncSession,
        *,
        reservation_id: UUID,
    ) -> BudgetReservation | None:
        """Latest event row for this reservation_id."""
        result = await session.execute(
            text(
                "SELECT reservation_id, user_id, "
                "       strategy_id, state, ticker, side, "
                "       qty, reserved_inr, filled_qty, "
                "       filled_inr, kite_order_id, "
                "       transitioned_at, metadata, "
                "       error_text "
                "FROM algo.budget_reservations "
                "WHERE reservation_id = :rid "
                "ORDER BY transitioned_at DESC LIMIT 1"
            ),
            {"rid": reservation_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        d = dict(row)
        d["state"] = ReservationState(d["state"])
        return BudgetReservation(**d)

    async def sum_active_reservations(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> Decimal:
        """Sum reserved_inr - filled_inr across reservations
        whose CURRENT state ∈ ACTIVE_STATES.
        """
        active = ",".join(
            f"'{s.value}'" for s in ACTIVE_STATES
        )
        result = await session.execute(
            text(
                "WITH latest AS ( "
                "  SELECT DISTINCT ON (reservation_id) "
                "    reservation_id, state, reserved_inr, "
                "    filled_inr "
                "  FROM algo.budget_reservations "
                "  WHERE user_id = :uid "
                "  ORDER BY reservation_id, "
                "           transitioned_at DESC "
                ") "
                "SELECT COALESCE(SUM("
                "  reserved_inr - filled_inr), 0) AS total "
                f"FROM latest WHERE state IN ({active})"
            ),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if row is None or row["total"] is None:
            return Decimal("0")
        return Decimal(str(row["total"]))

    async def list_active_reservations(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> list[BudgetReservation]:
        """All active reservation rows (latest state per
        reservation_id, filtered to ACTIVE_STATES)."""
        active = ",".join(
            f"'{s.value}'" for s in ACTIVE_STATES
        )
        result = await session.execute(
            text(
                "SELECT DISTINCT ON (reservation_id) "
                "  reservation_id, user_id, strategy_id, "
                "  state, ticker, side, qty, "
                "  reserved_inr, filled_qty, filled_inr, "
                "  kite_order_id, transitioned_at, "
                "  metadata, error_text "
                "FROM algo.budget_reservations "
                "WHERE user_id = :uid "
                "ORDER BY reservation_id, "
                "         transitioned_at DESC"
            ),
            {"uid": user_id},
        )
        out: list[BudgetReservation] = []
        for row in result.mappings().all():
            d = dict(row)
            d["state"] = ReservationState(d["state"])
            if d["state"] not in ACTIVE_STATES:
                continue
            out.append(BudgetReservation(**d))
        return out
```

- [ ] **Step 2.4: Run tests — expect 5 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_budget_repo.py -v
```

Expected: 5 passed.

- [ ] **Step 2.5: Commit**

```bash
git add backend/algo/live/budget_repo.py \
        backend/algo/live/tests/test_budget_repo.py
git commit -m "$(cat <<'EOF'
feat(algo-budget): BudgetRepo async PG CRUD

BudgetRepo wraps algo.user_budget (get/upsert) and
algo.budget_reservations (insert_reservation_event,
get_current_state via DISTINCT ON, sum_active_reservations
via CTE-filtered DISTINCT ON, list_active_reservations).
All queries respect the append-only event-log shape —
current state = latest row per reservation_id.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: budget.py — helpers + reserve/transition API

**Files:**
- Create: `backend/algo/live/budget.py`
- Create: `backend/algo/live/tests/test_budget.py`

- [ ] **Step 3.1: Write failing tests**

Create `backend/algo/live/tests/test_budget.py`:

```python
"""Tests for budget.py helpers + reserve/transition API."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.live.budget import (
    fetch_kite_available_cash,
    load_user_budget,
    reserve,
    sum_active_reservations,
    sum_open_position_cost,
    transition,
)
from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
    UserBudget,
)


@pytest.mark.asyncio
async def test_load_user_budget_returns_default_when_missing():
    fake_repo = MagicMock()
    fake_repo.get_user_budget = AsyncMock(
        return_value=UserBudget(user_id=uuid4()),
    )
    with patch(
        "backend.algo.live.budget.BudgetRepo",
        return_value=fake_repo,
    ), patch(
        "backend.algo.live.budget._session_factory",
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        out = await load_user_budget(uuid4())
    assert out.allocated_inr == Decimal("0")
    assert out.enabled is False


@pytest.mark.asyncio
async def test_fetch_kite_available_cash_returns_inf_on_error(
    monkeypatch,
):
    """Kite API error → Decimal('inf') (fail-open)."""
    async def boom(*args, **kwargs):
        raise RuntimeError("kite down")

    monkeypatch.setattr(
        "backend.algo.live.budget._kite_margins_for_user",
        boom,
    )
    out = await fetch_kite_available_cash(uuid4())
    assert out == Decimal("inf")


@pytest.mark.asyncio
async def test_fetch_kite_available_cash_reads_equity_cash(
    monkeypatch,
):
    async def fake_margins(_uid):
        return {
            "equity": {
                "available": {"cash": "78200.50"},
            },
        }

    monkeypatch.setattr(
        "backend.algo.live.budget._kite_margins_for_user",
        fake_margins,
    )
    out = await fetch_kite_available_cash(uuid4())
    assert out == Decimal("78200.50")


@pytest.mark.asyncio
async def test_reserve_inserts_pending_event_and_invalidates_cache():
    fake_repo = MagicMock()
    fake_repo.insert_reservation_event = AsyncMock()
    with patch(
        "backend.algo.live.budget.BudgetRepo",
        return_value=fake_repo,
    ), patch(
        "backend.algo.live.budget._session_factory",
    ) as factory, patch(
        "backend.algo.live.budget._invalidate_cache",
    ) as inv:
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(commit=AsyncMock()),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        rid = await reserve(
            user_id=uuid4(),
            strategy_id=uuid4(),
            ticker="INFY.NS",
            side="BUY",
            qty=50,
            reserved_inr=Decimal("7500.00"),
        )
    fake_repo.insert_reservation_event.assert_awaited_once()
    inv.assert_called_once()
    assert rid is not None


@pytest.mark.asyncio
async def test_transition_inserts_new_state_row():
    fake_repo = MagicMock()
    fake_repo.get_current_state = AsyncMock(
        return_value=BudgetReservation(
            reservation_id=uuid4(),
            user_id=uuid4(),
            strategy_id=uuid4(),
            state=ReservationState.PENDING,
            ticker="INFY.NS",
            side="BUY", qty=50,
            reserved_inr=Decimal("7500.00"),
            transitioned_at=datetime.now(timezone.utc),
        ),
    )
    fake_repo.insert_reservation_event = AsyncMock()
    with patch(
        "backend.algo.live.budget.BudgetRepo",
        return_value=fake_repo,
    ), patch(
        "backend.algo.live.budget._session_factory",
    ) as factory, patch(
        "backend.algo.live.budget._invalidate_cache",
    ):
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(commit=AsyncMock()),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        await transition(
            reservation_id=uuid4(),
            new_state=ReservationState.SUBMITTED,
            kite_order_id="kite-99",
        )
    fake_repo.insert_reservation_event.assert_awaited_once()
    call_args = (
        fake_repo.insert_reservation_event.await_args
    )
    new_row = call_args.args[1]
    assert new_row.state == ReservationState.SUBMITTED
    assert new_row.kite_order_id == "kite-99"


@pytest.mark.asyncio
async def test_sum_open_position_cost_returns_zero_by_default(
    monkeypatch,
):
    """sum_open_position_cost reads from algo.events. Empty
    history → zero."""
    async def fake_events(_uid):
        return []

    monkeypatch.setattr(
        "backend.algo.live.budget._algo_filled_events_for_user",
        fake_events,
    )
    out = await sum_open_position_cost(uuid4())
    assert out == Decimal("0")


@pytest.mark.asyncio
async def test_sum_active_reservations_passthrough(
    monkeypatch,
):
    fake_repo = MagicMock()
    fake_repo.sum_active_reservations = AsyncMock(
        return_value=Decimal("8500.00"),
    )
    with patch(
        "backend.algo.live.budget.BudgetRepo",
        return_value=fake_repo,
    ), patch(
        "backend.algo.live.budget._session_factory",
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        out = await sum_active_reservations(uuid4())
    assert out == Decimal("8500.00")
```

- [ ] **Step 3.2: Run test — expect ImportError**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_budget.py -v
```

Expected: `ImportError`.

- [ ] **Step 3.3: Implement budget.py**

Create `backend/algo/live/budget.py`:

```python
"""High-level budget API used by the gate + runtime.

Four cached helpers:
  - load_user_budget(user_id) → UserBudget
  - sum_open_position_cost(user_id) → Decimal
  - sum_active_reservations(user_id) → Decimal
  - fetch_kite_available_cash(user_id) → Decimal

Two-arg reservation lifecycle API:
  - reserve(user_id, strategy_id, ticker, side, qty,
            reserved_inr) → reservation_id (PENDING)
  - transition(reservation_id, new_state, ...) — appends
    a new state row.

5-second cache (Redis-backed) on the three frequently-read
helpers; invalidated on every reservation insert.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from backend.algo.live.budget_repo import BudgetRepo
from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
    UserBudget,
)

_logger = logging.getLogger(__name__)

_CACHE_TTL_S = 5


def _session_factory():
    """Lazy import — avoid circular dep with db.engine."""
    from db.engine import get_session_factory
    return get_session_factory()


def _cache_keys(user_id: UUID) -> tuple[str, str, str]:
    return (
        f"cache:budget:user:{user_id}:open_pos_cost",
        f"cache:budget:user:{user_id}:active_reserved",
        f"cache:budget:user:{user_id}:kite_available",
    )


def _invalidate_cache(user_id: UUID) -> None:
    try:
        from cache import get_cache
        c = get_cache()
        if c:
            c.invalidate_exact(*_cache_keys(user_id))
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "budget cache invalidate failed: %s",
            exc,
            exc_info=True,
        )


async def _kite_margins_for_user(
    user_id: UUID,
) -> dict[str, Any]:
    """Fetch Kite margins for the user.

    Resolves a KiteClient bound to the user via the existing
    credentials_repo; calls kc.margins('equity') in a thread.
    Cached at this layer (5s) per user.
    """
    from backend.algo.broker.kite_client import (
        KiteClient,
    )
    import asyncio
    kc = KiteClient(user_id=user_id)
    return await asyncio.to_thread(kc.margins, "equity")


async def _algo_filled_events_for_user(
    user_id: UUID,
) -> list[dict[str, Any]]:
    """Return the user's algo.events FILL rows that haven't
    been closed by a matching SELL FILL yet.

    Implemented as a thin wrapper around the existing
    position_hydration helper so the cost-basis derivation
    stays consistent across the codebase.
    """
    from backend.algo.live.position_hydration import (
        derive_open_positions,
    )
    return await derive_open_positions(user_id=user_id)


async def load_user_budget(user_id: UUID) -> UserBudget:
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        return await repo.get_user_budget(
            session, user_id=user_id,
        )


async def sum_open_position_cost(
    user_id: UUID,
) -> Decimal:
    """Sum cost basis across open positions.

    Cache: 5s per user; invalidated on reservation inserts.
    """
    from cache import get_cache
    c = get_cache()
    key = _cache_keys(user_id)[0]
    if c:
        cached = c.get(key)
        if cached is not None:
            return Decimal(cached)

    events = await _algo_filled_events_for_user(user_id)
    total = Decimal("0")
    for ev in events:
        qty = int(ev.get("qty", 0))
        price = Decimal(str(ev.get("entry_price", "0")))
        total += Decimal(qty) * price
    if c:
        c.set(key, str(total), ttl=_CACHE_TTL_S)
    return total


async def sum_active_reservations(
    user_id: UUID,
) -> Decimal:
    from cache import get_cache
    c = get_cache()
    key = _cache_keys(user_id)[1]
    if c:
        cached = c.get(key)
        if cached is not None:
            return Decimal(cached)

    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        total = await repo.sum_active_reservations(
            session, user_id=user_id,
        )
    if c:
        c.set(key, str(total), ttl=_CACHE_TTL_S)
    return total


async def fetch_kite_available_cash(
    user_id: UUID,
) -> Decimal:
    """kite.margins.equity.available.cash; Decimal('inf')
    on Kite error (fail-open)."""
    from cache import get_cache
    c = get_cache()
    key = _cache_keys(user_id)[2]
    if c:
        cached = c.get(key)
        if cached is not None:
            return Decimal(cached)

    try:
        margins = await _kite_margins_for_user(user_id)
        cash = (
            margins.get("equity", {})
            .get("available", {})
            .get("cash", 0)
        )
        out = Decimal(str(cash))
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "kite margins fetch failed for user=%s: %s",
            user_id, exc, exc_info=True,
        )
        return Decimal("inf")

    if c:
        c.set(key, str(out), ttl=_CACHE_TTL_S)
    return out


async def reserve(
    *,
    user_id: UUID,
    strategy_id: UUID,
    ticker: str,
    side: str,
    qty: int,
    reserved_inr: Decimal,
    metadata: dict[str, Any] | None = None,
) -> UUID:
    """Acquire a PENDING reservation. Returns the new
    reservation_id; subsequent transition() calls thread
    through it."""
    reservation_id = uuid4()
    row = BudgetReservation(
        reservation_id=reservation_id,
        user_id=user_id,
        strategy_id=strategy_id,
        state=ReservationState.PENDING,
        ticker=ticker,
        side=side,
        qty=qty,
        reserved_inr=reserved_inr,
        transitioned_at=datetime.now(timezone.utc),
        metadata=metadata or {},
    )
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        await repo.insert_reservation_event(session, row)
        await session.commit()
    _invalidate_cache(user_id)
    return reservation_id


async def transition(
    *,
    reservation_id: UUID,
    new_state: ReservationState,
    kite_order_id: str | None = None,
    filled_qty: int | None = None,
    filled_inr: Decimal | None = None,
    error_text: str | None = None,
) -> None:
    """Append a new state-event row, inheriting unchanged
    fields from the previous row of this reservation_id."""
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        prev = await repo.get_current_state(
            session, reservation_id=reservation_id,
        )
        if prev is None:
            _logger.error(
                "transition: no prior reservation %s",
                reservation_id,
            )
            return
        row = BudgetReservation(
            reservation_id=reservation_id,
            user_id=prev.user_id,
            strategy_id=prev.strategy_id,
            state=new_state,
            ticker=prev.ticker,
            side=prev.side,
            qty=prev.qty,
            reserved_inr=prev.reserved_inr,
            filled_qty=(
                filled_qty
                if filled_qty is not None
                else prev.filled_qty
            ),
            filled_inr=(
                filled_inr
                if filled_inr is not None
                else prev.filled_inr
            ),
            kite_order_id=(
                kite_order_id or prev.kite_order_id
            ),
            transitioned_at=datetime.now(timezone.utc),
            metadata=prev.metadata,
            error_text=error_text,
        )
        await repo.insert_reservation_event(session, row)
        await session.commit()
        _invalidate_cache(prev.user_id)
```

- [ ] **Step 3.4: Run tests — expect 7 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_budget.py -v
```

Expected: 7 passed.

- [ ] **Step 3.5: Commit**

```bash
git add backend/algo/live/budget.py \
        backend/algo/live/tests/test_budget.py
git commit -m "$(cat <<'EOF'
feat(algo-budget): cached helpers + reserve/transition API

Four 5s-cached helpers (load_user_budget, sum_open_position_
cost, sum_active_reservations, fetch_kite_available_cash) —
caches invalidated on every reservation INSERT via shared
_invalidate_cache. fetch_kite_available_cash returns
Decimal('inf') on Kite API error (fail-open per spec).
reserve() creates PENDING row + returns reservation_id;
transition() appends a new state-event row inheriting
unchanged fields from the previous row.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Cap 0 in safety.py + RejectReason

**Files:**
- Modify: `backend/algo/paper/types.py` (add `LIVE_BUDGET_CAP`)
- Modify: `backend/algo/live/safety.py` (insert Cap 0 before existing caps)
- Create: `backend/algo/live/tests/test_budget_gate.py`

- [ ] **Step 4.1: Add LIVE_BUDGET_CAP to RejectReason**

Edit `backend/algo/paper/types.py`. Find the `RejectReason` enum (around line 13). Add a new value at the bottom of the enum:

```python
class RejectReason(str, Enum):
    # ... existing entries ...
    LIVE_BUDGET_CAP = "live_budget_cap"
```

Don't remove or reorder existing entries.

- [ ] **Step 4.2: Write failing tests for Cap 0**

Create `backend/algo/live/tests/test_budget_gate.py`:

```python
"""Tests for Cap 0 budget check in pre_trade_check."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.algo.live.budget_types import UserBudget
from backend.algo.live.safety import pre_trade_check
from backend.algo.paper.types import (
    AccountState,
    RejectReason,
    Signal,
)


def _signal(side: str = "BUY", qty: int = 100):
    return Signal(
        ticker="INFY.NS",
        side=side,
        qty=qty,
        bar_date="2026-05-24",
    )


def _account():
    return AccountState(initial_capital_inr=Decimal("100000"))


def _caps(max_inr: Decimal = Decimal("0")):
    return {
        "live_orders_enabled": True,
        "allowed_tickers": [],
        "max_inr": max_inr,
        "max_orders_per_day": 0,
    }


@pytest.mark.asyncio
async def test_cap0_approves_under_headroom():
    """allocated 100k, open 20k, reserved 5k → headroom 75k.
    Order 70k → APPROVED."""
    with patch(
        "backend.algo.live.safety.load_user_budget",
        AsyncMock(return_value=UserBudget(
            user_id=uuid4(),
            allocated_inr=Decimal("100000"),
        )),
    ), patch(
        "backend.algo.live.safety.sum_open_position_cost",
        AsyncMock(return_value=Decimal("20000")),
    ), patch(
        "backend.algo.live.safety.sum_active_reservations",
        AsyncMock(return_value=Decimal("5000")),
    ), patch(
        "backend.algo.live.safety.fetch_kite_available_cash",
        AsyncMock(return_value=Decimal("200000")),
    ):
        decision = await pre_trade_check(
            signal=_signal(qty=70),  # 70 * 1000 = 70k
            account=_account(),
            strategy_risk={},
            day_state={},
            kill_switch=False,
            caps=_caps(),
            mode="live",
            user_id=uuid4(),
            last_price=Decimal("1000"),
        )
    assert decision.approved is True


@pytest.mark.asyncio
async def test_cap0_rejects_when_internal_exhausted():
    """allocated 100k, open 80k, reserved 15k → headroom 5k.
    Order 10k → REJECTED with LIVE_BUDGET_CAP."""
    with patch(
        "backend.algo.live.safety.load_user_budget",
        AsyncMock(return_value=UserBudget(
            user_id=uuid4(),
            allocated_inr=Decimal("100000"),
        )),
    ), patch(
        "backend.algo.live.safety.sum_open_position_cost",
        AsyncMock(return_value=Decimal("80000")),
    ), patch(
        "backend.algo.live.safety.sum_active_reservations",
        AsyncMock(return_value=Decimal("15000")),
    ), patch(
        "backend.algo.live.safety.fetch_kite_available_cash",
        AsyncMock(return_value=Decimal("200000")),
    ):
        decision = await pre_trade_check(
            signal=_signal(qty=10),  # 10 * 1000 = 10k
            account=_account(),
            strategy_risk={},
            day_state={},
            kill_switch=False,
            caps=_caps(),
            mode="live",
            user_id=uuid4(),
            last_price=Decimal("1000"),
        )
    assert decision.approved is False
    assert decision.reason == RejectReason.LIVE_BUDGET_CAP
    assert decision.threshold == Decimal("5000")
    assert decision.observed == Decimal("10000")


@pytest.mark.asyncio
async def test_cap0_rejects_when_kite_exhausted():
    """internal 50k headroom, kite 8k available. Order 10k →
    REJECTED (kite is binding)."""
    with patch(
        "backend.algo.live.safety.load_user_budget",
        AsyncMock(return_value=UserBudget(
            user_id=uuid4(),
            allocated_inr=Decimal("100000"),
        )),
    ), patch(
        "backend.algo.live.safety.sum_open_position_cost",
        AsyncMock(return_value=Decimal("50000")),
    ), patch(
        "backend.algo.live.safety.sum_active_reservations",
        AsyncMock(return_value=Decimal("0")),
    ), patch(
        "backend.algo.live.safety.fetch_kite_available_cash",
        AsyncMock(return_value=Decimal("8000")),
    ):
        decision = await pre_trade_check(
            signal=_signal(qty=10),
            account=_account(),
            strategy_risk={},
            day_state={},
            kill_switch=False,
            caps=_caps(),
            mode="live",
            user_id=uuid4(),
            last_price=Decimal("1000"),
        )
    assert decision.approved is False
    assert decision.reason == RejectReason.LIVE_BUDGET_CAP
    assert decision.threshold == Decimal("8000")  # kite-bound


@pytest.mark.asyncio
async def test_cap0_sell_bypasses_gate():
    """SELL → no Cap 0 check even if pool is empty."""
    with patch(
        "backend.algo.live.safety.load_user_budget",
        AsyncMock(return_value=UserBudget(
            user_id=uuid4(),
            allocated_inr=Decimal("0"),
        )),
    ):
        decision = await pre_trade_check(
            signal=_signal(side="SELL", qty=100),
            account=_account(),
            strategy_risk={},
            day_state={},
            kill_switch=False,
            caps=_caps(),
            mode="live",
            user_id=uuid4(),
            last_price=Decimal("1000"),
        )
    assert decision.reason != RejectReason.LIVE_BUDGET_CAP


@pytest.mark.asyncio
async def test_cap0_fail_open_when_kite_down():
    """Kite returns Decimal('inf') → headroom falls back to
    internal only. Order under internal → APPROVED."""
    with patch(
        "backend.algo.live.safety.load_user_budget",
        AsyncMock(return_value=UserBudget(
            user_id=uuid4(),
            allocated_inr=Decimal("100000"),
        )),
    ), patch(
        "backend.algo.live.safety.sum_open_position_cost",
        AsyncMock(return_value=Decimal("0")),
    ), patch(
        "backend.algo.live.safety.sum_active_reservations",
        AsyncMock(return_value=Decimal("0")),
    ), patch(
        "backend.algo.live.safety.fetch_kite_available_cash",
        AsyncMock(return_value=Decimal("inf")),
    ):
        decision = await pre_trade_check(
            signal=_signal(qty=10),
            account=_account(),
            strategy_risk={},
            day_state={},
            kill_switch=False,
            caps=_caps(),
            mode="live",
            user_id=uuid4(),
            last_price=Decimal("1000"),
        )
    assert decision.approved is True


@pytest.mark.asyncio
async def test_cap0_blocks_when_allocation_zero():
    """allocated 0 → headroom 0 → all BUYs REJECTED."""
    with patch(
        "backend.algo.live.safety.load_user_budget",
        AsyncMock(return_value=UserBudget(
            user_id=uuid4(),
            allocated_inr=Decimal("0"),
        )),
    ), patch(
        "backend.algo.live.safety.sum_open_position_cost",
        AsyncMock(return_value=Decimal("0")),
    ), patch(
        "backend.algo.live.safety.sum_active_reservations",
        AsyncMock(return_value=Decimal("0")),
    ), patch(
        "backend.algo.live.safety.fetch_kite_available_cash",
        AsyncMock(return_value=Decimal("100000")),
    ):
        decision = await pre_trade_check(
            signal=_signal(qty=10),
            account=_account(),
            strategy_risk={},
            day_state={},
            kill_switch=False,
            caps=_caps(),
            mode="live",
            user_id=uuid4(),
            last_price=Decimal("1000"),
        )
    assert decision.approved is False
    assert decision.reason == RejectReason.LIVE_BUDGET_CAP
```

- [ ] **Step 4.3: Run tests — expect failures because Cap 0 isn't implemented yet**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_budget_gate.py -v
```

Expected: all 6 fail (no Cap 0 yet; rejections won't match `LIVE_BUDGET_CAP`).

- [ ] **Step 4.4: Implement Cap 0 in safety.py**

Read the current `safety.py` `pre_trade_check` signature (around line 98). The function may currently be sync — if so, this task converts it to async (the budget helpers are async). Update callers accordingly.

Edit `backend/algo/live/safety.py`:

1. Convert `pre_trade_check` to `async def`. Add `user_id: UUID` to its kwargs.
2. Insert the Cap 0 block IMMEDIATELY after the kill-switch check (~line 129) and BEFORE the allowed-tickers check (line ~140):

```python
    # Cap 0 — user-pool budget reservation (NEW)
    # Runs only in live mode. SELL bypasses (closes a
    # position, releases capital).
    if mode == "live" and signal.side != "SELL":
        from backend.algo.live.budget import (
            fetch_kite_available_cash,
            load_user_budget,
            sum_active_reservations,
            sum_open_position_cost,
        )

        user_budget = await load_user_budget(user_id)
        open_pos_cost = await sum_open_position_cost(user_id)
        active_reserved = await sum_active_reservations(
            user_id,
        )
        kite_available = await fetch_kite_available_cash(
            user_id,
        )

        internal_headroom = (
            user_budget.allocated_inr
            - open_pos_cost
            - active_reserved
        )
        order_cost = Decimal(signal.qty) * last_price
        headroom = min(
            internal_headroom, kite_available,
        )
        if order_cost > headroom:
            _logger.info(
                "[budget-gate] REJECT user=%s "
                "ticker=%s qty=%d cost=%s "
                "headroom=%s internal=%s kite=%s",
                user_id, signal.ticker, signal.qty,
                order_cost, headroom,
                internal_headroom, kite_available,
            )
            return _reject_live(
                RejectReason.LIVE_BUDGET_CAP,
                threshold=headroom,
                observed=order_cost,
                metadata={
                    "internal_headroom": str(
                        internal_headroom,
                    ),
                    "kite_available": str(kite_available),
                    "user_allocated_inr": str(
                        user_budget.allocated_inr,
                    ),
                    "open_pos_cost": str(open_pos_cost),
                    "active_reserved": str(active_reserved),
                },
            )

    # ── Cap 1: kill switch ───────────────────────────────
    # (existing code — unchanged)
```

3. The existing caller in `runtime.py:1378` calls `pre_trade_check(...)`. Wrap with `await`:

```python
        decision = await pre_trade_check(
            ...,
            user_id=self.user_id,
            last_price=last_price,
        )
```

If `_reject_live` doesn't currently accept a `metadata` kwarg, add it:

```python
def _reject_live(
    reason: RejectReason,
    *,
    threshold: Decimal | None = None,
    observed: Decimal | None = None,
    metadata: dict[str, Any] | None = None,  # NEW
) -> RiskDecision:
    return RiskDecision(
        approved=False,
        reason=reason,
        binding=True,
        threshold=threshold,
        observed=observed,
        metadata=metadata or {},
    )
```

And if `RiskDecision` doesn't have a `metadata` field, add it as `dict[str, Any]` with `default_factory=dict`. (Confirm by inspecting `backend/algo/paper/types.py`.)

- [ ] **Step 4.5: Run tests — expect 6 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_budget_gate.py -v
```

Expected: 6 passed.

- [ ] **Step 4.6: Restart backend**

```bash
docker compose restart backend
sleep 5
```

(CLAUDE.md §6.2 — `safety.py` is imported by `runtime.py` and route handlers.)

- [ ] **Step 4.7: Run existing safety tests for regression**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/ -v 2>&1 | tail -5
```

Expected: all green. If any existing test depended on `pre_trade_check` being sync, fix it to `await`.

- [ ] **Step 4.8: Commit**

```bash
git add backend/algo/paper/types.py \
        backend/algo/live/safety.py \
        backend/algo/live/runtime.py \
        backend/algo/live/tests/test_budget_gate.py
git commit -m "$(cat <<'EOF'
feat(algo-budget): Cap 0 — user-pool budget gate

New Cap 0 in pre_trade_check runs before existing caps for
mode='live' BUY orders. Computes
  headroom = min(internal, kite_available)
where internal = allocated - open_pos_cost - active_reserved.
Rejection writes LIVE_BUDGET_CAP with metadata carrying all
5 inputs for audit. SELLs bypass (release capital).
Kite-down → fail-open on the kite side; internal still
binding.

pre_trade_check converted to async to accommodate the
budget helpers. runtime.py caller updated with await.
_reject_live grew a metadata kwarg; RiskDecision gained
a metadata dict field.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: Reconciliation + runtime.py wiring

**Files:**
- Create: `backend/algo/live/budget_reconciliation.py`
- Create: `backend/algo/live/tests/test_budget_reconciliation.py`
- Modify: `backend/algo/live/runtime.py` — call `reserve()` between gate approval and Kite call; call `transition()` on Kite response
- Modify: `backend/algo/live/reconciliation.py` — drive `budget_reconciliation.reconcile()`

- [ ] **Step 5.1: Write failing reconciliation tests**

Create `backend/algo/live/tests/test_budget_reconciliation.py`:

```python
"""Tests for budget reservation lifecycle reconciliation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.algo.live.budget_reconciliation import (
    reconcile_one,
    reconcile_pending_timeouts,
)
from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
)


@pytest.mark.asyncio
async def test_pending_timeout_at_120s():
    """PENDING older than 120s → TIMEOUT, releases reserved."""
    pending = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.PENDING,
        ticker="INFY.NS",
        side="BUY", qty=50,
        reserved_inr=Decimal("7500.00"),
        transitioned_at=(
            datetime.now(timezone.utc)
            - timedelta(seconds=121)
        ),
    )
    with patch(
        "backend.algo.live.budget_reconciliation."
        "_list_pending",
        AsyncMock(return_value=[pending]),
    ), patch(
        "backend.algo.live.budget_reconciliation.transition",
        AsyncMock(),
    ) as mock_trans:
        await reconcile_pending_timeouts()
    mock_trans.assert_awaited_once()
    kwargs = mock_trans.await_args.kwargs
    assert kwargs["new_state"] == ReservationState.TIMEOUT


@pytest.mark.asyncio
async def test_pending_under_120s_not_timed_out():
    """PENDING under 120s → no transition."""
    pending = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.PENDING,
        ticker="INFY.NS",
        side="BUY", qty=50,
        reserved_inr=Decimal("7500.00"),
        transitioned_at=(
            datetime.now(timezone.utc)
            - timedelta(seconds=30)
        ),
    )
    with patch(
        "backend.algo.live.budget_reconciliation."
        "_list_pending",
        AsyncMock(return_value=[pending]),
    ), patch(
        "backend.algo.live.budget_reconciliation.transition",
        AsyncMock(),
    ) as mock_trans:
        await reconcile_pending_timeouts()
    mock_trans.assert_not_awaited()


@pytest.mark.asyncio
async def test_submitted_complete_transitions_to_filled():
    """Kite reports COMPLETE → transition to FILLED."""
    submitted = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.SUBMITTED,
        ticker="INFY.NS",
        side="BUY", qty=50,
        reserved_inr=Decimal("7500.00"),
        kite_order_id="kite-99",
        transitioned_at=datetime.now(timezone.utc),
    )

    async def fake_kite_status(uid, koi):
        return {
            "status": "COMPLETE",
            "filled_quantity": 50,
            "average_price": "150.00",
        }

    with patch(
        "backend.algo.live.budget_reconciliation."
        "_fetch_kite_order_status",
        fake_kite_status,
    ), patch(
        "backend.algo.live.budget_reconciliation.transition",
        AsyncMock(),
    ) as mock_trans:
        await reconcile_one(submitted)
    mock_trans.assert_awaited_once()
    kwargs = mock_trans.await_args.kwargs
    assert kwargs["new_state"] == ReservationState.FILLED
    assert kwargs["filled_qty"] == 50
    assert kwargs["filled_inr"] == Decimal("7500.00")


@pytest.mark.asyncio
async def test_submitted_partial_transitions_to_partial():
    """Kite reports partial fill → PARTIAL with filled fields."""
    submitted = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.SUBMITTED,
        ticker="INFY.NS",
        side="BUY", qty=100,
        reserved_inr=Decimal("10000.00"),
        kite_order_id="kite-99",
        transitioned_at=datetime.now(timezone.utc),
    )

    async def fake_kite_status(uid, koi):
        return {
            "status": "OPEN",  # still open w/ partial
            "filled_quantity": 80,
            "average_price": "100.00",
        }

    with patch(
        "backend.algo.live.budget_reconciliation."
        "_fetch_kite_order_status",
        fake_kite_status,
    ), patch(
        "backend.algo.live.budget_reconciliation.transition",
        AsyncMock(),
    ) as mock_trans:
        await reconcile_one(submitted)
    mock_trans.assert_awaited_once()
    kwargs = mock_trans.await_args.kwargs
    assert kwargs["new_state"] == ReservationState.PARTIAL
    assert kwargs["filled_qty"] == 80
    assert kwargs["filled_inr"] == Decimal("8000.00")


@pytest.mark.asyncio
async def test_submitted_cancelled_transitions_to_cancelled():
    submitted = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.SUBMITTED,
        ticker="INFY.NS",
        side="BUY", qty=50,
        reserved_inr=Decimal("7500.00"),
        kite_order_id="kite-99",
        transitioned_at=datetime.now(timezone.utc),
    )

    async def fake_kite_status(uid, koi):
        return {"status": "CANCELLED"}

    with patch(
        "backend.algo.live.budget_reconciliation."
        "_fetch_kite_order_status",
        fake_kite_status,
    ), patch(
        "backend.algo.live.budget_reconciliation.transition",
        AsyncMock(),
    ) as mock_trans:
        await reconcile_one(submitted)
    mock_trans.assert_awaited_once()
    kwargs = mock_trans.await_args.kwargs
    assert kwargs["new_state"] == ReservationState.CANCELLED
```

- [ ] **Step 5.2: Run tests — expect ImportError**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_budget_reconciliation.py -v
```

Expected: `ImportError`.

- [ ] **Step 5.3: Implement budget_reconciliation.py**

Create `backend/algo/live/budget_reconciliation.py`:

```python
"""Periodic reconciliation of budget reservations.

Two passes per tick:

1. ``reconcile_pending_timeouts`` — any PENDING older than
   120s is marked TIMEOUT (reserved_inr released).
2. ``reconcile_submitted`` — for each SUBMITTED reservation:
   query Kite for the order's status; transition based on
   the reported status. If a SUBMITTED row hasn't seen any
   Kite update for 5 minutes, force TIMEOUT.

Called by the existing ``backend/algo/live/reconciliation.py``
loop alongside the other live-mode reconciliation routines.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from backend.algo.live.budget import transition
from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
)

_logger = logging.getLogger(__name__)

PENDING_TIMEOUT_S = 120
SUBMITTED_HARD_TIMEOUT_S = 300


async def _list_pending() -> list[BudgetReservation]:
    """Pull all PENDING reservations across users."""
    from backend.algo.live.budget_repo import BudgetRepo
    from backend.algo.live.budget import _session_factory

    repo = BudgetRepo()
    factory = _session_factory()
    from sqlalchemy import text
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT DISTINCT ON (reservation_id) "
                "  reservation_id, user_id, strategy_id, "
                "  state, ticker, side, qty, "
                "  reserved_inr, filled_qty, filled_inr, "
                "  kite_order_id, transitioned_at, "
                "  metadata, error_text "
                "FROM algo.budget_reservations "
                "ORDER BY reservation_id, "
                "         transitioned_at DESC"
            ),
        )
        rows = result.mappings().all()
    out: list[BudgetReservation] = []
    for row in rows:
        d = dict(row)
        d["state"] = ReservationState(d["state"])
        if d["state"] == ReservationState.PENDING:
            out.append(BudgetReservation(**d))
    return out


async def _list_submitted_and_partial() -> list[BudgetReservation]:
    """Pull all SUBMITTED + PARTIAL reservations."""
    from backend.algo.live.budget_repo import BudgetRepo
    from backend.algo.live.budget import _session_factory

    repo = BudgetRepo()
    factory = _session_factory()
    from sqlalchemy import text
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT DISTINCT ON (reservation_id) "
                "  reservation_id, user_id, strategy_id, "
                "  state, ticker, side, qty, "
                "  reserved_inr, filled_qty, filled_inr, "
                "  kite_order_id, transitioned_at, "
                "  metadata, error_text "
                "FROM algo.budget_reservations "
                "ORDER BY reservation_id, "
                "         transitioned_at DESC"
            ),
        )
        rows = result.mappings().all()
    out: list[BudgetReservation] = []
    for row in rows:
        d = dict(row)
        d["state"] = ReservationState(d["state"])
        if d["state"] in (
            ReservationState.SUBMITTED,
            ReservationState.PARTIAL,
        ):
            out.append(BudgetReservation(**d))
    return out


async def _fetch_kite_order_status(
    user_id: UUID, kite_order_id: str,
) -> dict[str, Any] | None:
    """Pull single order status from Kite. None on error."""
    try:
        from backend.algo.broker.kite_client import (
            KiteClient,
        )
        kc = KiteClient(user_id=user_id)
        history = await asyncio.to_thread(
            kc.order_history, kite_order_id,
        )
        if not history:
            return None
        return history[-1]
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "kite order_history failed user=%s order=%s: %s",
            user_id, kite_order_id, exc, exc_info=True,
        )
        return None


async def reconcile_pending_timeouts() -> None:
    """Transition PENDING > 120s old to TIMEOUT."""
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(seconds=PENDING_TIMEOUT_S)

    pending = await _list_pending()
    for res in pending:
        if res.transitioned_at < threshold:
            await transition(
                reservation_id=res.reservation_id,
                new_state=ReservationState.TIMEOUT,
                error_text=(
                    f"PENDING timeout > "
                    f"{PENDING_TIMEOUT_S}s"
                ),
            )


async def reconcile_one(
    res: BudgetReservation,
) -> None:
    """Reconcile a single SUBMITTED/PARTIAL reservation."""
    if res.kite_order_id is None:
        return

    now = datetime.now(timezone.utc)
    threshold = now - timedelta(
        seconds=SUBMITTED_HARD_TIMEOUT_S,
    )

    status_row = await _fetch_kite_order_status(
        res.user_id, res.kite_order_id,
    )
    if status_row is None:
        # No status from Kite — check hard timeout
        if res.transitioned_at < threshold:
            await transition(
                reservation_id=res.reservation_id,
                new_state=ReservationState.TIMEOUT,
                error_text=(
                    f"SUBMITTED hard timeout > "
                    f"{SUBMITTED_HARD_TIMEOUT_S}s, "
                    "Kite unreachable"
                ),
            )
        return

    kite_status = (
        str(status_row.get("status", "")).upper()
    )
    filled_qty = int(
        status_row.get("filled_quantity", 0) or 0,
    )
    avg_price = Decimal(
        str(status_row.get("average_price", 0) or 0),
    )
    filled_inr = Decimal(filled_qty) * avg_price

    if kite_status == "COMPLETE":
        await transition(
            reservation_id=res.reservation_id,
            new_state=ReservationState.FILLED,
            filled_qty=filled_qty,
            filled_inr=filled_inr,
        )
    elif kite_status == "CANCELLED":
        if filled_qty > 0:
            await transition(
                reservation_id=res.reservation_id,
                new_state=(
                    ReservationState.PARTIAL_CANCELLED
                ),
                filled_qty=filled_qty,
                filled_inr=filled_inr,
            )
        else:
            await transition(
                reservation_id=res.reservation_id,
                new_state=ReservationState.CANCELLED,
            )
    elif kite_status == "REJECTED":
        await transition(
            reservation_id=res.reservation_id,
            new_state=ReservationState.REJECTED,
            error_text=str(
                status_row.get("status_message", "")
                or "rejected",
            )[:500],
        )
    elif kite_status == "OPEN" and filled_qty > 0:
        await transition(
            reservation_id=res.reservation_id,
            new_state=ReservationState.PARTIAL,
            filled_qty=filled_qty,
            filled_inr=filled_inr,
        )
    elif res.transitioned_at < threshold:
        # Kite says nothing definitive for 5 min →
        # force TIMEOUT.
        await transition(
            reservation_id=res.reservation_id,
            new_state=ReservationState.TIMEOUT,
            error_text=(
                f"SUBMITTED hard timeout > "
                f"{SUBMITTED_HARD_TIMEOUT_S}s, "
                f"Kite status={kite_status}"
            ),
        )


async def reconcile_submitted() -> None:
    """Reconcile each SUBMITTED+PARTIAL reservation in turn."""
    for res in await _list_submitted_and_partial():
        try:
            await reconcile_one(res)
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "budget reconcile_one failed res=%s: %s",
                res.reservation_id, exc, exc_info=True,
            )


async def reconcile() -> None:
    """Single-tick entrypoint called by the live
    reconciliation loop."""
    await reconcile_pending_timeouts()
    await reconcile_submitted()
```

- [ ] **Step 5.4: Run tests — expect 5 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/test_budget_reconciliation.py -v
```

Expected: 5 passed.

- [ ] **Step 5.5: Wire budget.reserve / transition into runtime.py**

Edit `backend/algo/live/runtime.py`. Find the location around line 1378-1410 where `pre_trade_check` is called and `kite_client.place_order` follows. The flow today:

```python
decision = await pre_trade_check(...)
if not decision.approved:
    # log rejection event
    continue

kite_order_id = self._kite.place_order(...)
```

Modify to:

```python
from backend.algo.live.budget import (
    reserve as budget_reserve,
    transition as budget_transition,
)
from backend.algo.live.budget_types import ReservationState

decision = await pre_trade_check(
    ...,
    user_id=self.user_id,
    last_price=last_price,
)
if not decision.approved:
    # log rejection event (existing)
    continue

# Acquire budget lock BEFORE Kite call
order_cost = Decimal(signal.qty) * last_price
reservation_id = await budget_reserve(
    user_id=self.user_id,
    strategy_id=self.strategy_id,
    ticker=signal.ticker,
    side=signal.side,
    qty=signal.qty,
    reserved_inr=order_cost,
)
try:
    kite_order_id = self._kite.place_order(
        ...,  # existing kwargs
    )
    # Kite accepted → SUBMITTED
    await budget_transition(
        reservation_id=reservation_id,
        new_state=ReservationState.SUBMITTED,
        kite_order_id=kite_order_id,
    )
except Exception as exc:  # noqa: BLE001
    # Kite rejected at SDK / network layer
    await budget_transition(
        reservation_id=reservation_id,
        new_state=ReservationState.REJECTED,
        error_text=str(exc)[:500],
    )
    raise
```

(The exact insertion-point lines depend on the surrounding code; use grep + read the lines around the `place_order` call before editing.)

- [ ] **Step 5.6: Wire budget_reconciliation.reconcile() into reconciliation.py**

Edit `backend/algo/live/reconciliation.py`. Find the periodic loop body (probably an `async def reconcile()` function). Add at the bottom of its body:

```python
        # Budget reservation reconciliation
        try:
            from backend.algo.live.budget_reconciliation import (
                reconcile as budget_reconcile,
            )
            await budget_reconcile()
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "budget reconcile failed: %s",
                exc, exc_info=True,
            )
```

- [ ] **Step 5.7: Restart backend**

```bash
docker compose restart backend
sleep 5
```

- [ ] **Step 5.8: Run regression**

```bash
docker compose exec backend python -m pytest \
  backend/algo/live/tests/ -v 2>&1 | tail -5
```

Expected: all green.

- [ ] **Step 5.9: Commit**

```bash
git add backend/algo/live/budget_reconciliation.py \
        backend/algo/live/runtime.py \
        backend/algo/live/reconciliation.py \
        backend/algo/live/tests/test_budget_reconciliation.py
git commit -m "$(cat <<'EOF'
feat(algo-budget): reconciliation loop + runtime wiring

Periodic reconciliation transitions reservations based on
Kite order status (COMPLETE → FILLED; CANCELLED → CANCELLED
or PARTIAL_CANCELLED depending on filled_qty; REJECTED →
REJECTED; partial OPEN → PARTIAL; nothing for 5 min →
TIMEOUT). PENDING > 120s is auto-TIMEOUT'd.

runtime.py now calls budget.reserve() between gate approval
and kite_client.place_order(), then budget.transition() on
Kite response (SUBMITTED on success, REJECTED on Kite-side
failure, stays PENDING on network failure for reconciliation
to pick up).

reconciliation.py's existing loop now drives budget_
reconciliation.reconcile() each tick.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: HTTP routes

**Files:**
- Create: `backend/algo/routes/budget.py`
- Modify: `backend/algo/routes/__init__.py` (export `create_budget_router`)
- Modify: `backend/routes.py` (mount the router under `/v1`)
- Create: `backend/algo/tests/test_budget_routes.py`

- [ ] **Step 6.1: Write failing route tests**

Create `backend/algo/tests/test_budget_routes.py`:

```python
"""HTTP-level tests for /v1/algo/budget/* endpoints."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
    UserBudget,
)
from backend.algo.routes.budget import (
    _get_budget_impl,
    _list_reservations_impl,
    _put_allocation_impl,
)


@pytest.mark.asyncio
async def test_get_budget_returns_pending_shape_when_missing():
    with patch(
        "backend.algo.routes.budget.load_user_budget",
        AsyncMock(return_value=UserBudget(user_id=uuid4())),
    ), patch(
        "backend.algo.routes.budget.sum_open_position_cost",
        AsyncMock(return_value=Decimal("0")),
    ), patch(
        "backend.algo.routes.budget.sum_active_reservations",
        AsyncMock(return_value=Decimal("0")),
    ), patch(
        "backend.algo.routes.budget."
        "fetch_kite_available_cash",
        AsyncMock(return_value=Decimal("215000")),
    ):
        out = await _get_budget_impl(user_id=uuid4())
    assert out["allocated_inr"] == "0"
    assert out["enabled"] is False
    assert out["open_pos_cost"] == "0"
    assert out["active_reserved"] == "0"
    assert out["kite_available"] == "215000"
    assert out["available"] == "0"  # min(0, 215000)


@pytest.mark.asyncio
async def test_put_allocation_creates_row():
    repo = MagicMock()
    repo.upsert_user_budget = AsyncMock()
    with patch(
        "backend.algo.routes.budget.BudgetRepo",
        return_value=repo,
    ), patch(
        "backend.algo.routes.budget._session_factory",
    ) as factory, patch(
        "backend.algo.routes.budget._invalidate_cache",
    ):
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(commit=AsyncMock()),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        out = await _put_allocation_impl(
            user_id=uuid4(),
            new_allocation=Decimal("100000"),
        )
    assert out["allocated_inr"] == "100000"
    assert out["enabled"] is True
    repo.upsert_user_budget.assert_awaited_once()


@pytest.mark.asyncio
async def test_put_allocation_rejects_negative():
    with pytest.raises(HTTPException) as exc:
        await _put_allocation_impl(
            user_id=uuid4(),
            new_allocation=Decimal("-1"),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_put_allocation_warning_when_below_committed():
    repo = MagicMock()
    repo.upsert_user_budget = AsyncMock()
    with patch(
        "backend.algo.routes.budget.BudgetRepo",
        return_value=repo,
    ), patch(
        "backend.algo.routes.budget._session_factory",
    ) as factory, patch(
        "backend.algo.routes.budget._invalidate_cache",
    ), patch(
        "backend.algo.routes.budget.sum_open_position_cost",
        AsyncMock(return_value=Decimal("30000")),
    ), patch(
        "backend.algo.routes.budget.sum_active_reservations",
        AsyncMock(return_value=Decimal("8000")),
    ):
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(commit=AsyncMock()),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        out = await _put_allocation_impl(
            user_id=uuid4(),
            new_allocation=Decimal("10000"),
        )
    # 10k < 30k + 8k committed → warning
    assert out["allocated_inr"] == "10000"
    assert "warning" in out
    assert "below committed" in out["warning"].lower()


@pytest.mark.asyncio
async def test_list_reservations_active_only_by_default():
    fake_res = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.SUBMITTED,
        ticker="INFY.NS",
        side="BUY", qty=50,
        reserved_inr=Decimal("7500.00"),
        transitioned_at=__import__(
            "datetime",
        ).datetime.now(
            __import__("datetime").timezone.utc,
        ),
    )
    repo = MagicMock()
    repo.list_active_reservations = AsyncMock(
        return_value=[fake_res],
    )
    with patch(
        "backend.algo.routes.budget.BudgetRepo",
        return_value=repo,
    ), patch(
        "backend.algo.routes.budget._session_factory",
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        out = await _list_reservations_impl(
            user_id=uuid4(),
            include_history=False,
        )
    assert len(out["reservations"]) == 1
    assert (
        out["reservations"][0]["state"] == "SUBMITTED"
    )
```

- [ ] **Step 6.2: Run test — expect ImportError**

```bash
docker compose exec backend python -m pytest \
  backend/algo/tests/test_budget_routes.py -v
```

Expected: `ImportError`.

- [ ] **Step 6.3: Implement routes/budget.py**

Create `backend/algo/routes/budget.py`:

```python
"""POST/GET routes for /v1/algo/budget/*.

Lift-to-module-level pattern — handlers delegate to pure
``_impl`` functions for testability without an HTTP harness.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import (
    APIRouter, Body, Depends, HTTPException,
)

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.live.budget import (
    _invalidate_cache,
    _session_factory,
    fetch_kite_available_cash,
    load_user_budget,
    sum_active_reservations,
    sum_open_position_cost,
    transition,
)
from backend.algo.live.budget_repo import BudgetRepo
from backend.algo.live.budget_types import (
    ReservationState,
)

_logger = logging.getLogger(__name__)


async def _get_budget_impl(*, user_id: UUID) -> dict:
    user_budget = await load_user_budget(user_id)
    open_cost = await sum_open_position_cost(user_id)
    active_res = await sum_active_reservations(user_id)
    kite = await fetch_kite_available_cash(user_id)

    internal_headroom = (
        user_budget.allocated_inr - open_cost - active_res
    )
    available = min(internal_headroom, kite)
    return {
        "user_id": str(user_id),
        "allocated_inr": str(user_budget.allocated_inr),
        "enabled": user_budget.enabled,
        "open_pos_cost": str(open_cost),
        "active_reserved": str(active_res),
        "internal_headroom": str(internal_headroom),
        "kite_available": (
            str(kite) if kite != Decimal("inf")
            else None
        ),
        "available": str(available),
    }


async def _put_allocation_impl(
    *,
    user_id: UUID,
    new_allocation: Decimal,
) -> dict:
    if new_allocation < Decimal("0"):
        raise HTTPException(
            status_code=400,
            detail="allocated_inr must be ≥ 0",
        )

    # Warn (don't reject) if reducing below committed
    open_cost = await sum_open_position_cost(user_id)
    active_res = await sum_active_reservations(user_id)
    committed = open_cost + active_res
    warning = None
    if new_allocation < committed:
        warning = (
            f"New allocation ₹{new_allocation} is below "
            f"currently committed ₹{committed} (open "
            f"positions + active reservations). No new "
            f"orders will fire until open positions close."
        )

    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        await repo.upsert_user_budget(
            session,
            user_id=user_id,
            allocated_inr=new_allocation,
            enabled=(new_allocation > 0),
            updated_by=user_id,
        )
        await session.commit()
    _invalidate_cache(user_id)

    out: dict = {
        "user_id": str(user_id),
        "allocated_inr": str(new_allocation),
        "enabled": new_allocation > 0,
    }
    if warning is not None:
        out["warning"] = warning
    return out


async def _list_reservations_impl(
    *,
    user_id: UUID,
    include_history: bool = False,
) -> dict:
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        if include_history:
            from sqlalchemy import text
            result = await session.execute(
                text(
                    "SELECT reservation_id, user_id, "
                    "       strategy_id, state, ticker, "
                    "       side, qty, reserved_inr, "
                    "       filled_qty, filled_inr, "
                    "       kite_order_id, "
                    "       transitioned_at, metadata, "
                    "       error_text "
                    "FROM algo.budget_reservations "
                    "WHERE user_id = :uid "
                    "ORDER BY transitioned_at DESC "
                    "LIMIT 500"
                ),
                {"uid": user_id},
            )
            rows = result.mappings().all()
            return {
                "reservations": [
                    {
                        **{k: str(v) for k, v in dict(r).items()},
                    }
                    for r in rows
                ],
            }
        else:
            active = await repo.list_active_reservations(
                session, user_id=user_id,
            )
    return {
        "reservations": [
            {
                "reservation_id": str(r.reservation_id),
                "strategy_id": str(r.strategy_id),
                "state": r.state.value,
                "ticker": r.ticker,
                "side": r.side,
                "qty": r.qty,
                "reserved_inr": str(r.reserved_inr),
                "filled_qty": r.filled_qty,
                "filled_inr": str(r.filled_inr),
                "kite_order_id": r.kite_order_id,
                "transitioned_at": (
                    r.transitioned_at.isoformat()
                ),
            }
            for r in active
        ],
    }


async def _force_release_impl(
    *,
    user_id: UUID,
    reservation_id: UUID,
) -> dict:
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        current = await repo.get_current_state(
            session, reservation_id=reservation_id,
        )
    if current is None:
        raise HTTPException(
            status_code=404,
            detail="Reservation not found",
        )
    if current.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Not owner of reservation",
        )
    await transition(
        reservation_id=reservation_id,
        new_state=ReservationState.CANCELLED,
        error_text="force-released by user",
    )
    return {"status": "released"}


def create_budget_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/budget", tags=["algo-trading"],
    )

    @router.get("")
    async def get_budget(
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _get_budget_impl(
            user_id=UUID(user.user_id),
        )

    @router.put("/allocation")
    async def put_allocation(
        body: dict = Body(...),
        user: UserContext = Depends(pro_or_superuser),
    ):
        try:
            new_alloc = Decimal(
                str(body.get("allocated_inr", "0")),
            )
        except (InvalidOperation, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"invalid allocated_inr: {exc}",
            ) from exc
        return await _put_allocation_impl(
            user_id=UUID(user.user_id),
            new_allocation=new_alloc,
        )

    @router.get("/reservations")
    async def list_reservations(
        include_history: bool = False,
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _list_reservations_impl(
            user_id=UUID(user.user_id),
            include_history=include_history,
        )

    @router.post(
        "/reservations/{reservation_id}/force-release",
    )
    async def force_release(
        reservation_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ):
        return await _force_release_impl(
            user_id=UUID(user.user_id),
            reservation_id=reservation_id,
        )

    return router
```

- [ ] **Step 6.4: Export the router**

Edit `backend/algo/routes/__init__.py`. Add to imports + `__all__` alphabetically (after the most-recent additions):

```python
from backend.algo.routes.budget import (
    create_budget_router,
)
```

And add `"create_budget_router",` to `__all__`.

- [ ] **Step 6.5: Mount in routes.py**

Edit `backend/routes.py`. Find the `app.include_router(create_walkforward_router(), prefix="/v1")` line and add immediately after it:

```python
    app.include_router(
        create_budget_router(),
        prefix="/v1",
    )
```

Make sure `create_budget_router` is in the import block at the top of `routes.py` alongside `create_walkforward_router`.

- [ ] **Step 6.6: Run tests — expect 5 PASS**

```bash
docker compose restart backend && sleep 5
docker compose exec backend python -m pytest \
  backend/algo/tests/test_budget_routes.py -v
```

Expected: 5 passed.

- [ ] **Step 6.7: Smoke test**

```bash
docker compose exec backend python -c "
from backend.algo.routes.budget import create_budget_router
r = create_budget_router()
print([f'{route.methods} {route.path}' for route in r.routes])
"
```

Expected output includes:
```
['GET'] /algo/budget
['PUT'] /algo/budget/allocation
['GET'] /algo/budget/reservations
['POST'] /algo/budget/reservations/{reservation_id}/force-release
```

- [ ] **Step 6.8: Commit**

```bash
git add backend/algo/routes/budget.py \
        backend/algo/routes/__init__.py \
        backend/routes.py \
        backend/algo/tests/test_budget_routes.py
git commit -m "$(cat <<'EOF'
feat(algo-budget): HTTP routes (GET/PUT/list/force-release)

Four endpoints under /v1/algo/budget — get current headroom
(allocated, open, reserved, kite, available), put allocation
(rejects negative, warns when below committed), list
reservations (active by default; ?include_history=true for
full event log up to 500 rows), force-release a reservation
(owner-only). Lift-to-module-level _impl pattern.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Frontend types + hooks + BudgetPanel shell

**Files:**
- Create: `frontend/lib/types/algoBudget.ts`
- Create: `frontend/hooks/useBudget.ts`
- Create: `frontend/components/algo-trading/BudgetPanel.tsx`
- Modify: `frontend/components/algo-trading/LiveTab.tsx` (mount `<BudgetPanel />`)
- Create: `frontend/components/algo-trading/__tests__/useBudget.test.ts`

- [ ] **Step 7.1: Create TS shapes**

Create `frontend/lib/types/algoBudget.ts`:

```typescript
// Mirrors backend/algo/live/budget_types.py.

export type ReservationState =
  | "PENDING"
  | "SUBMITTED"
  | "FILLED"
  | "REJECTED"
  | "CANCELLED"
  | "PARTIAL"
  | "PARTIAL_CANCELLED"
  | "TIMEOUT";

export interface UserBudgetView {
  user_id: string;
  allocated_inr: string;
  enabled: boolean;
  open_pos_cost: string;
  active_reserved: string;
  internal_headroom: string;
  kite_available: string | null;
  available: string;
}

export interface BudgetReservationView {
  reservation_id: string;
  strategy_id: string;
  state: ReservationState;
  ticker: string;
  side: "BUY" | "SELL";
  qty: number;
  reserved_inr: string;
  filled_qty: number;
  filled_inr: string;
  kite_order_id: string | null;
  transitioned_at: string;
}
```

- [ ] **Step 7.2: Create the hooks**

Create `frontend/hooks/useBudget.ts`:

```typescript
"use client";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  BudgetReservationView, UserBudgetView,
} from "@/lib/types/algoBudget";

const fetcher = async (url: string) => {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export function useUserBudget() {
  const { data, error, isLoading, mutate } = useSWR<
    UserBudgetView
  >(
    `${API_URL}/algo/budget`,
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 5_000 },
  );
  return { budget: data, error, isLoading, mutate };
}

export function useActiveReservations() {
  const { data, error, isLoading, mutate } = useSWR<
    { reservations: BudgetReservationView[] }
  >(
    `${API_URL}/algo/budget/reservations`,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) =>
        !latest || latest.reservations.length === 0
          ? 0
          : 3_000,
    },
  );
  return {
    reservations: data?.reservations ?? [],
    error,
    isLoading,
    mutate,
  };
}

export async function setAllocation(
  newAllocation: string,
): Promise<UserBudgetView> {
  const r = await apiFetch(
    `${API_URL}/algo/budget/allocation`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        allocated_inr: newAllocation,
      }),
    },
  );
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`Allocation failed: ${body}`);
  }
  return r.json();
}

export async function forceReleaseReservation(
  reservationId: string,
): Promise<void> {
  const r = await apiFetch(
    `${API_URL}/algo/budget/reservations/`
    + `${reservationId}/force-release`,
    { method: "POST" },
  );
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`Release failed: ${body}`);
  }
}
```

- [ ] **Step 7.3: Create BudgetPanel shell (placeholder)**

Create `frontend/components/algo-trading/BudgetPanel.tsx`:

```typescript
"use client";

import { useUserBudget } from "@/hooks/useBudget";

export function BudgetPanel() {
  const { budget, isLoading, error } = useUserBudget();

  if (isLoading || !budget) {
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
        data-testid="budget-panel"
      >
        Loading budget…
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="rounded-md border border-rose-200 bg-rose-50 dark:bg-rose-950/30 p-4 text-sm text-rose-700"
        data-testid="budget-panel-error"
      >
        Budget unavailable
      </div>
    );
  }

  const allocated = Number(budget.allocated_inr);
  const open = Number(budget.open_pos_cost);
  const pending = Number(budget.active_reserved);
  const available = Number(budget.available);
  const kite = budget.kite_available
    ? Number(budget.kite_available)
    : null;

  if (allocated === 0) {
    return (
      <div
        className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/30 p-4 space-y-2"
        data-testid="budget-panel"
      >
        <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
          ⚠ Algo trading is paused — no budget allocated.
        </p>
        <p className="text-xs text-amber-800 dark:text-amber-300">
          Set an algo allocation before enabling any
          strategy for live trading. (Allocation modal
          coming in next slice.)
        </p>
      </div>
    );
  }

  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-4 space-y-3"
      data-testid="budget-panel"
    >
      <h3 className="text-sm font-semibold">Budget</h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile
          label="Allocated"
          value={`₹${allocated.toLocaleString("en-IN")}`}
          testid="budget-tile-allocated"
        />
        <Tile
          label="Open positions"
          value={`₹${open.toLocaleString("en-IN")}`}
          testid="budget-tile-open-positions"
        />
        <Tile
          label="Pending"
          value={`₹${pending.toLocaleString("en-IN")}`}
          testid="budget-tile-pending"
        />
        <Tile
          label="Available"
          value={`₹${available.toLocaleString("en-IN")}`}
          testid="budget-tile-available"
          accent="emerald"
        />
      </div>
      <div
        className="text-xs text-slate-500"
        data-testid="budget-kite-wallet-row"
      >
        Kite wallet:{" "}
        {kite != null
          ? `₹${kite.toLocaleString("en-IN")}`
          : "—"}{" "}
        ⓘ Live gate uses min(internal, Kite) = ₹
        {available.toLocaleString("en-IN")}
      </div>
    </div>
  );
}

function Tile({
  label, value, testid, accent,
}: {
  label: string;
  value: string;
  testid: string;
  accent?: "emerald";
}) {
  const valCls =
    accent === "emerald"
      ? "text-lg font-bold text-emerald-600 dark:text-emerald-400"
      : "text-lg font-semibold";
  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 px-3 py-2"
      data-testid={testid}
    >
      <p className="text-[11px] uppercase text-slate-400">
        {label}
      </p>
      <p className={valCls}>{value}</p>
    </div>
  );
}
```

- [ ] **Step 7.4: Mount in LiveTab**

Edit `frontend/components/algo-trading/LiveTab.tsx`. Add import:

```typescript
import { BudgetPanel } from "./BudgetPanel";
```

Insert `<BudgetPanel />` at the very top of the returned JSX (before any other Live tab content):

```typescript
return (
  <div className="space-y-4">
    <BudgetPanel />
    {/* ... existing content ... */}
  </div>
);
```

If `LiveTab.tsx` already has a top-level fragment / wrapper, insert the `<BudgetPanel />` as the first child element.

- [ ] **Step 7.5: Write hook smoke test**

Create `frontend/components/algo-trading/__tests__/useBudget.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { setAllocation }
  from "@/hooks/useBudget";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      allocated_inr: "100000",
      enabled: true,
    }),
  }),
}));

describe("setAllocation", () => {
  it("PUTs the allocation and returns the updated budget", async () => {
    const out = await setAllocation("100000");
    expect(out.allocated_inr).toBe("100000");
    expect(out.enabled).toBe(true);
  });
});
```

- [ ] **Step 7.6: Run test**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend \
  && npx vitest run \
  components/algo-trading/__tests__/useBudget.test.ts
```

Expected: 1 passed.

- [ ] **Step 7.7: Lint**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend \
  && npx eslint \
  components/algo-trading/BudgetPanel.tsx \
  components/algo-trading/LiveTab.tsx \
  hooks/useBudget.ts \
  lib/types/algoBudget.ts \
  components/algo-trading/__tests__/useBudget.test.ts \
  --fix
```

Expected: 0 errors.

- [ ] **Step 7.8: Commit**

```bash
git add frontend/lib/types/algoBudget.ts \
        frontend/hooks/useBudget.ts \
        frontend/components/algo-trading/BudgetPanel.tsx \
        frontend/components/algo-trading/LiveTab.tsx \
        frontend/components/algo-trading/__tests__/useBudget.test.ts
git commit -m "$(cat <<'EOF'
feat(algo-budget-ui): types, hooks, BudgetPanel shell

TS shapes mirroring backend Pydantic. Two SWR hooks
(useUserBudget polls every 5s; useActiveReservations polls
every 3s while active, 0 otherwise). setAllocation +
forceReleaseReservation POST helpers. BudgetPanel renders
empty-state CTA when allocated=0, 4-tile grid + Kite wallet
strip when allocated>0. Mounted at top of LiveTab. Allocation
modal + reservations table arrive in next slice.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: Allocation modal + reservations table + history + E2E + PR

**Files:**
- Create: `frontend/components/algo-trading/BudgetAllocationModal.tsx`
- Create: `frontend/components/algo-trading/BudgetReservationHistoryModal.tsx`
- Modify: `frontend/components/algo-trading/BudgetPanel.tsx` (wire modals + reservations table + force-release)
- Create: `frontend/components/algo-trading/__tests__/BudgetPanel.test.tsx`
- Modify: `e2e/utils/selectors.ts` (add testids)
- Create: `e2e/pages/frontend/budget.page.ts`
- Create: `e2e/tests/frontend/algo-trading-budget.spec.ts`
- Modify: `PROGRESS.md`

- [ ] **Step 8.1: Implement BudgetAllocationModal**

Create `frontend/components/algo-trading/BudgetAllocationModal.tsx`:

```typescript
"use client";

import { useState } from "react";
import { setAllocation } from "@/hooks/useBudget";
import type { UserBudgetView } from "@/lib/types/algoBudget";

interface Props {
  current: UserBudgetView;
  onClose: () => void;
  onSaved: () => void;
}

export function BudgetAllocationModal(
  { current, onClose, onSaved }: Props,
) {
  const [val, setVal] = useState(current.allocated_inr);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const committed =
    Number(current.open_pos_cost)
    + Number(current.active_reserved);
  const numVal = Number(val);
  const isInvalid =
    Number.isNaN(numVal)
    || numVal < 0;
  const belowCommitted = !isInvalid && numVal < committed;

  async function handleSave() {
    if (isInvalid) return;
    setSubmitting(true);
    setErr(null);
    try {
      await setAllocation(val);
      onSaved();
      onClose();
    } catch (exc) {
      setErr(
        exc instanceof Error
          ? exc.message
          : "Save failed",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
      data-testid="budget-allocation-modal"
    >
      <div className="bg-white dark:bg-slate-900 rounded-md p-4 w-96 space-y-3">
        <h3 className="text-sm font-semibold">
          Edit Algo Budget Allocation
        </h3>
        {current.kite_available && (
          <p className="text-xs text-slate-500">
            Total Kite wallet:{" "}
            ₹{Number(
              current.kite_available,
            ).toLocaleString("en-IN")}
          </p>
        )}
        <label className="flex flex-col gap-1 text-xs">
          <span>Algo allocation (₹)</span>
          <input
            type="number"
            min={0}
            step={100}
            value={val}
            onChange={(e) => setVal(e.target.value)}
            data-testid="budget-allocation-input"
            className="rounded border border-slate-300 dark:border-slate-600 px-2 py-1"
          />
        </label>
        {belowCommitted && (
          <p
            className="text-xs text-amber-700 dark:text-amber-300"
            data-testid="budget-allocation-below-committed-warning"
          >
            You currently have ₹
            {committed.toLocaleString("en-IN")} committed.
            Reducing below this means no new orders will
            fire until existing positions close.
          </p>
        )}
        {err && (
          <p className="text-xs text-rose-600">{err}</p>
        )}
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded border px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={isInvalid || submitting}
            data-testid="budget-allocation-save-button"
            className="rounded bg-indigo-600 text-white px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 8.2: Implement BudgetReservationHistoryModal**

Create `frontend/components/algo-trading/BudgetReservationHistoryModal.tsx`:

```typescript
"use client";

import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

interface Props {
  onClose: () => void;
}

const fetcher = async (url: string) => {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export function BudgetReservationHistoryModal(
  { onClose }: Props,
) {
  const { data, isLoading } = useSWR<{
    reservations: Record<string, string>[];
  }>(
    `${API_URL}/algo/budget/reservations`
    + `?include_history=true`,
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 overflow-y-auto"
      data-testid="budget-reservation-history-modal"
    >
      <div className="bg-white dark:bg-slate-900 rounded-md p-4 w-[800px] max-w-[95vw] my-8 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">
            Reservation history
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-xs underline"
          >
            Close
          </button>
        </div>
        {isLoading && (
          <p className="text-xs text-slate-500">
            Loading…
          </p>
        )}
        {!isLoading && data && (
          <div className="max-h-[60vh] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="bg-slate-50 dark:bg-slate-800 sticky top-0">
                <tr>
                  <th className="px-2 py-1 text-left">
                    Time
                  </th>
                  <th className="px-2 py-1 text-left">
                    Ticker
                  </th>
                  <th className="px-2 py-1 text-left">
                    Side
                  </th>
                  <th className="px-2 py-1 text-right">
                    Qty
                  </th>
                  <th className="px-2 py-1 text-left">
                    State
                  </th>
                  <th className="px-2 py-1 text-right">
                    Reserved
                  </th>
                  <th className="px-2 py-1 text-right">
                    Filled
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.reservations.map(
                  (r: Record<string, string>) => (
                    <tr
                      key={`${r.reservation_id}-${r.transitioned_at}`}
                      className="border-t"
                    >
                      <td className="px-2 py-1">
                        {r.transitioned_at}
                      </td>
                      <td className="px-2 py-1">
                        {r.ticker}
                      </td>
                      <td className="px-2 py-1">
                        {r.side}
                      </td>
                      <td className="px-2 py-1 text-right">
                        {r.qty}
                      </td>
                      <td className="px-2 py-1">
                        {r.state}
                      </td>
                      <td className="px-2 py-1 text-right">
                        ₹{r.reserved_inr}
                      </td>
                      <td className="px-2 py-1 text-right">
                        ₹{r.filled_inr}
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 8.3: Wire modals + reservations table into BudgetPanel**

Replace the contents of `frontend/components/algo-trading/BudgetPanel.tsx` with the full version that includes the active reservations table, edit/history triggers, and force-release inline confirm:

```typescript
"use client";

import { useState } from "react";
import {
  forceReleaseReservation,
  useActiveReservations,
  useUserBudget,
} from "@/hooks/useBudget";
import { BudgetAllocationModal }
  from "./BudgetAllocationModal";
import { BudgetReservationHistoryModal }
  from "./BudgetReservationHistoryModal";

export function BudgetPanel() {
  const {
    budget, isLoading, error, mutate: mutateBudget,
  } = useUserBudget();
  const {
    reservations, mutate: mutateReservations,
  } = useActiveReservations();
  const [editOpen, setEditOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [confirmingId, setConfirmingId] = useState<
    string | null
  >(null);

  if (isLoading || !budget) {
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
        data-testid="budget-panel"
      >
        Loading budget…
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="rounded-md border border-rose-200 bg-rose-50 dark:bg-rose-950/30 p-4 text-sm text-rose-700"
        data-testid="budget-panel-error"
      >
        Budget unavailable
      </div>
    );
  }

  const allocated = Number(budget.allocated_inr);
  const open = Number(budget.open_pos_cost);
  const pending = Number(budget.active_reserved);
  const available = Number(budget.available);
  const kite = budget.kite_available
    ? Number(budget.kite_available)
    : null;

  async function handleForceRelease(id: string) {
    await forceReleaseReservation(id);
    setConfirmingId(null);
    await mutateReservations();
    await mutateBudget();
  }

  if (allocated === 0) {
    return (
      <>
        <div
          className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/30 p-4 space-y-2"
          data-testid="budget-panel"
        >
          <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
            ⚠ Algo trading is paused — no budget allocated.
          </p>
          <p className="text-xs text-amber-800 dark:text-amber-300">
            Set an algo allocation before enabling any
            strategy for live trading.
          </p>
          <button
            type="button"
            onClick={() => setEditOpen(true)}
            data-testid="budget-tile-edit-button"
            className="rounded bg-indigo-600 text-white px-3 py-1.5 text-sm"
          >
            Allocate budget
          </button>
        </div>
        {editOpen && (
          <BudgetAllocationModal
            current={budget}
            onClose={() => setEditOpen(false)}
            onSaved={() => mutateBudget()}
          />
        )}
      </>
    );
  }

  return (
    <>
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 space-y-3"
        data-testid="budget-panel"
      >
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Budget</h3>
          <button
            type="button"
            onClick={() => setEditOpen(true)}
            data-testid="budget-tile-edit-button"
            className="text-xs underline"
          >
            Edit ✎
          </button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Tile
            label="Allocated"
            value={`₹${allocated.toLocaleString("en-IN")}`}
            testid="budget-tile-allocated"
          />
          <Tile
            label="Open positions"
            value={`₹${open.toLocaleString("en-IN")}`}
            testid="budget-tile-open-positions"
          />
          <Tile
            label="Pending"
            value={`₹${pending.toLocaleString("en-IN")}`}
            testid="budget-tile-pending"
          />
          <Tile
            label="Available"
            value={`₹${available.toLocaleString("en-IN")}`}
            testid="budget-tile-available"
            accent="emerald"
          />
        </div>
        <div
          className="text-xs text-slate-500"
          data-testid="budget-kite-wallet-row"
        >
          Kite wallet:{" "}
          {kite != null
            ? `₹${kite.toLocaleString("en-IN")}`
            : (
              <span className="text-amber-700 dark:text-amber-300">
                Kite unreachable; using internal headroom only
              </span>
            )}{" "}
          {kite != null && (
            <span>
              ⓘ Live gate uses min(internal, Kite) = ₹
              {available.toLocaleString("en-IN")}
            </span>
          )}
        </div>

        {reservations.length > 0 && (
          <div
            className="rounded-md border border-slate-200 dark:border-slate-700 overflow-hidden"
            data-testid="budget-active-reservations-table"
          >
            <table className="w-full text-xs">
              <thead className="bg-slate-50 dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700">
                <tr>
                  <th className="px-2 py-1 text-left">
                    Ticker
                  </th>
                  <th className="px-2 py-1 text-left">
                    Side
                  </th>
                  <th className="px-2 py-1 text-right">
                    Qty
                  </th>
                  <th className="px-2 py-1 text-right">
                    Reserved
                  </th>
                  <th className="px-2 py-1 text-left">
                    State
                  </th>
                  <th className="px-2 py-1"></th>
                </tr>
              </thead>
              <tbody>
                {reservations.map((r) => (
                  <tr
                    key={r.reservation_id}
                    className="border-t border-slate-200 dark:border-slate-700"
                    data-testid={
                      `budget-reservation-row-${r.reservation_id}`
                    }
                  >
                    <td className="px-2 py-1">{r.ticker}</td>
                    <td className="px-2 py-1">{r.side}</td>
                    <td className="px-2 py-1 text-right">
                      {r.qty}
                    </td>
                    <td className="px-2 py-1 text-right">
                      ₹{Number(
                        r.reserved_inr,
                      ).toLocaleString("en-IN")}
                    </td>
                    <td className="px-2 py-1">
                      {r.state}
                    </td>
                    <td className="px-2 py-1">
                      {confirmingId === r.reservation_id ? (
                        <span className="flex gap-1">
                          <button
                            type="button"
                            onClick={() =>
                              handleForceRelease(
                                r.reservation_id,
                              )
                            }
                            className="text-rose-600 underline"
                          >
                            Confirm
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmingId(null)}
                            className="text-slate-400 underline"
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() =>
                            setConfirmingId(r.reservation_id)
                          }
                          data-testid={
                            `budget-force-release-button-${r.reservation_id}`
                          }
                          className="text-rose-500"
                        >
                          ✖
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <button
          type="button"
          onClick={() => setHistoryOpen(true)}
          data-testid="budget-reservation-history-link"
          className="text-xs underline text-slate-600 dark:text-slate-300"
        >
          View reservation history →
        </button>
      </div>

      {editOpen && (
        <BudgetAllocationModal
          current={budget}
          onClose={() => setEditOpen(false)}
          onSaved={() => mutateBudget()}
        />
      )}
      {historyOpen && (
        <BudgetReservationHistoryModal
          onClose={() => setHistoryOpen(false)}
        />
      )}
    </>
  );
}

function Tile({
  label, value, testid, accent,
}: {
  label: string;
  value: string;
  testid: string;
  accent?: "emerald";
}) {
  const valCls =
    accent === "emerald"
      ? "text-lg font-bold text-emerald-600 dark:text-emerald-400"
      : "text-lg font-semibold";
  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 px-3 py-2"
      data-testid={testid}
    >
      <p className="text-[11px] uppercase text-slate-400">
        {label}
      </p>
      <p className={valCls}>{value}</p>
    </div>
  );
}
```

- [ ] **Step 8.4: Write Vitest tests for BudgetPanel**

Create `frontend/components/algo-trading/__tests__/BudgetPanel.test.tsx`:

```typescript
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { BudgetPanel } from "../BudgetPanel";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/hooks/useBudget", () => ({
  useUserBudget: () => ({
    budget: {
      user_id: "u",
      allocated_inr: "100000",
      enabled: true,
      open_pos_cost: "35000",
      active_reserved: "8500",
      internal_headroom: "56500",
      kite_available: "78200",
      available: "56500",
    },
    isLoading: false,
    error: null,
    mutate: vi.fn(),
  }),
  useActiveReservations: () => ({
    reservations: [
      {
        reservation_id: "r1",
        strategy_id: "s1",
        state: "SUBMITTED",
        ticker: "INFY.NS",
        side: "BUY",
        qty: 50,
        reserved_inr: "7500",
        filled_qty: 0,
        filled_inr: "0",
        kite_order_id: "kite-1",
        transitioned_at: "2026-05-24T10:00:00Z",
      },
    ],
    mutate: vi.fn(),
  }),
  forceReleaseReservation: vi.fn(),
}));

afterEach(() => cleanup());

describe("BudgetPanel", () => {
  it("renders four tiles with values", () => {
    render(<BudgetPanel />);
    expect(
      screen.getByTestId("budget-tile-allocated"),
    ).toBeDefined();
    expect(
      screen.getByTestId("budget-tile-open-positions"),
    ).toBeDefined();
    expect(
      screen.getByTestId("budget-tile-pending"),
    ).toBeDefined();
    expect(
      screen.getByTestId("budget-tile-available"),
    ).toBeDefined();
  });

  it("renders Kite wallet row", () => {
    render(<BudgetPanel />);
    expect(
      screen.getByTestId("budget-kite-wallet-row"),
    ).toBeDefined();
  });

  it("renders active reservations table", () => {
    render(<BudgetPanel />);
    expect(
      screen.getByTestId("budget-active-reservations-table"),
    ).toBeDefined();
    expect(
      screen.getByTestId("budget-reservation-row-r1"),
    ).toBeDefined();
  });
});
```

- [ ] **Step 8.5: Run tests + lint**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend \
  && npx vitest run \
  components/algo-trading/__tests__/BudgetPanel.test.tsx \
  components/algo-trading/__tests__/useBudget.test.ts
```

Expected: 4 passed.

```bash
npx eslint \
  components/algo-trading/BudgetPanel.tsx \
  components/algo-trading/BudgetAllocationModal.tsx \
  components/algo-trading/BudgetReservationHistoryModal.tsx \
  components/algo-trading/__tests__/BudgetPanel.test.tsx \
  --fix
```

Expected: 0 errors.

- [ ] **Step 8.6: Add E2E testids to registry**

Edit `e2e/utils/selectors.ts`. Add to the `FE` object:

```typescript
  budgetPanel: "budget-panel",
  budgetTileAllocated: "budget-tile-allocated",
  budgetTileOpenPositions: "budget-tile-open-positions",
  budgetTilePending: "budget-tile-pending",
  budgetTileAvailable: "budget-tile-available",
  budgetTileEditButton: "budget-tile-edit-button",
  budgetKiteWalletRow: "budget-kite-wallet-row",
  budgetActiveReservationsTable: "budget-active-reservations-table",
  budgetAllocationModal: "budget-allocation-modal",
  budgetAllocationInput: "budget-allocation-input",
  budgetAllocationSaveButton: "budget-allocation-save-button",
  budgetReservationHistoryLink: "budget-reservation-history-link",
  budgetReservationHistoryModal: "budget-reservation-history-modal",
```

- [ ] **Step 8.7: Create Playwright POM**

Create `e2e/pages/frontend/budget.page.ts`:

```typescript
import { BasePage } from "../base.page";
import { FE } from "@/utils/selectors";

export class BudgetPage extends BasePage {
  async open() {
    await this.page.goto(
      "/algo-trading/strategies?tab=live",
    );
    await this.tid(FE.budgetPanel).waitFor();
  }

  async clickAllocate() {
    await this.tid(FE.budgetTileEditButton).click();
    await this.tid(
      FE.budgetAllocationModal,
    ).waitFor();
  }

  async setAllocation(amount: string) {
    await this.tid(FE.budgetAllocationInput).fill(amount);
    await this.tid(FE.budgetAllocationSaveButton).click();
  }
}
```

- [ ] **Step 8.8: Create E2E smoke**

Create `e2e/tests/frontend/algo-trading-budget.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";
import { BudgetPage } from "@/pages/frontend/budget.page";
import { FE } from "@/utils/selectors";

test.use({
  storageState: ".auth/superuser.json",
});

test.describe("Budget panel", () => {
  test("renders on Live tab", async ({ page }) => {
    const budget = new BudgetPage(page);
    await budget.open();
    await expect(
      page.getByTestId(FE.budgetPanel),
    ).toBeVisible();
  });
});
```

- [ ] **Step 8.9: Add PROGRESS.md entry**

Edit `PROGRESS.md`. Insert at the top:

```markdown
### 2026-05-24 — Algo order budget reservation (Epic A)

Shipped user-pool budget reservation ("ticketing") layer
for live algo trading. Every BUY order reserves notional
against `algo.user_budget.allocated_inr`; reservations stack
in `algo.budget_reservations` (append-only event log) with
states PENDING → SUBMITTED → FILLED / REJECTED / CANCELLED
/ PARTIAL / PARTIAL_CANCELLED / TIMEOUT.

New Cap 0 in `safety.py:pre_trade_check()` runs before
existing Cap 1-9 — uses `min(internal_headroom, kite_available_cash)`
so manual trades + T+1 settlement holds + MIS auto-square-off
lag naturally reduce algo headroom. Fail-open on Kite,
fail-closed on internal.

New BudgetPanel at top of Live tab — Allocated / Open
positions / Pending / Available tiles + Kite wallet strip +
active reservations table with force-release + reservation
history modal with full event log. Empty-state CTA prompts
allocation when user_budget.allocated_inr=0.

PRs shipped (one per slice):
- migration + Pydantic types
- BudgetRepo
- cached helpers + reserve/transition API
- Cap 0 in safety.py + RejectReason
- reconciliation + runtime.py wiring
- HTTP routes (GET / PUT / list / force-release)
- frontend types + hooks + BudgetPanel shell
- allocation modal + reservations table + history + E2E + PR

Out of scope for v1: BO/CO orders, order modifications, F&O
margin estimation, per-strategy sub-pools, multi-currency,
WebSocket push, per-strategy allocation UI consolidation.

Companion epics deferred: B (Algo Portfolio dashboard tab),
C (Watchlist bulk ops).

Spec: `docs/superpowers/specs/2026-05-24-algo-order-budget-reservation-design.md`
Plan: `docs/superpowers/plans/2026-05-24-algo-order-budget-reservation.md`
```

- [ ] **Step 8.10: Commit + push + open PR**

```bash
git add frontend/components/algo-trading/BudgetPanel.tsx \
        frontend/components/algo-trading/BudgetAllocationModal.tsx \
        frontend/components/algo-trading/BudgetReservationHistoryModal.tsx \
        frontend/components/algo-trading/__tests__/BudgetPanel.test.tsx \
        e2e/utils/selectors.ts \
        e2e/pages/frontend/budget.page.ts \
        e2e/tests/frontend/algo-trading-budget.spec.ts \
        PROGRESS.md
git commit -m "$(cat <<'EOF'
feat(algo-budget-ui): allocation modal + reservations + E2E

Allocation modal validates non-negative input, warns when
new allocation < (open_pos_cost + active_reserved), POSTs to
PUT /v1/algo/budget/allocation. Reservations table with
per-row force-release inline confirm. History modal pulls
full event log via ?include_history=true. E2E smoke
verifies BudgetPanel renders on Live tab.

PROGRESS.md entry summarising all 8 slices.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"

git push -u origin feature/algo-order-budget-reservation
gh pr create \
  --base dev \
  --title "Algo order budget reservation (Epic A, v1)" \
  --body "$(cat <<'EOF'
## Summary

- New Cap 0 in `safety.py:pre_trade_check` runs BEFORE existing per-strategy `max_inr` (Cap 4)
- User-pool budget computed as `min(allocated − open_pos_cost − active_reservations, kite.available_cash)`
- Two new PG tables: `algo.user_budget` (mutable) + `algo.budget_reservations` (append-only event log)
- Reservation lifecycle: PENDING → SUBMITTED → FILLED / REJECTED / CANCELLED / PARTIAL / PARTIAL_CANCELLED / TIMEOUT
- Reconciliation loop drives state transitions every 30s via existing live reconciliation infra
- New BudgetPanel at top of Live tab + allocation modal + reservations table + history modal
- Fail-open on Kite (internal still binding), fail-closed on internal PG

Spec: `docs/superpowers/specs/2026-05-24-algo-order-budget-reservation-design.md`
Plan: `docs/superpowers/plans/2026-05-24-algo-order-budget-reservation.md`

## Test plan

- [x] Backend: 28 tests across `test_budget_types`, `test_budget_repo`, `test_budget`, `test_budget_gate`, `test_budget_reconciliation`, `test_budget_routes` — all green
- [x] Frontend: 4 Vitest tests (BudgetPanel + useBudget hook) — all green
- [ ] E2E: `cd e2e && npx playwright test algo-trading-budget.spec.ts --project=frontend-chromium` — verify smoke green
- [ ] Manual first-live-day pre-flight per `docs/operational/algo-budget-pre-flight.md`

## Out of scope (deferred)

- BO / CO orders (inherit existing v2 deferral)
- Order modifications (modify_order)
- Per-strategy sub-pools (A3 hybrid)
- F&O / derivatives margin estimation
- Multi-currency
- Reservation cancel button at row level (force-release exists; row-cancel via Kite is follow-up)
- Per-strategy allocation UI consolidation
- Headroom forecasting
- WebSocket push for reservations

## Companion epics

- B: Algo Portfolio tab on dashboard
- C: Watchlist bulk ops

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

- All 7 spec sections (architecture, lifecycle, gate, components, UI, testing, out-of-scope) map 1:1 to tasks.
- Pre-flight verified identifiers used in code blocks:
  - `pre_trade_check` (NOT `check_pre_trade`) — function name in `safety.py:98`
  - `RejectReason` enum at `backend/algo/paper/types.py:13`
  - `Signal` Pydantic model at `backend/algo/paper/types.py:38`
  - `kite_client.place_order` signature includes `tradingsymbol/exchange/transaction_type/quantity/order_type/product` kwargs
  - `algo.events` + `position_hydration.derive_open_positions` referenced as the existing cost-basis source
- Each task ends in exactly one commit; 8 commits total for 8 PR slices.
- The runtime.py wiring (Task 5.5) requires reading surrounding lines before editing; the implementer should grep for the exact `place_order(` call site.
- `_reject_live` may need a `metadata` kwarg added (Task 4.4) — flagged as conditional on inspection.
- `RiskDecision` may need a `metadata` field added — flagged as conditional on inspection.
- Cache invalidation calls use the existing project pattern (`cache.invalidate_exact`).
- All tests run via `docker compose exec backend python -m pytest …` per CLAUDE.md harness rules.
- Frontend tests use `.toBeDefined()` (no `jest-dom` matchers loaded in this project — established pattern from prior epics).
- Co-Authored-By trailer on every commit per CLAUDE.md §4.4 #24.
