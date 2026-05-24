"""Tests for BudgetRepo — async PG CRUD + event-log queries."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from backend.algo.live.budget_repo import BudgetRepo
from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
)
from db.engine import disposable_pg_session


@pytest.fixture
async def session():
    """Yield a NullPool-backed async session + an explicit
    teardown that DELETEs every (user_id) the test touched.

    Why not SAVEPOINT-rollback? The "join into external
    transaction" recipe relies on ``Session.commit()``
    releasing the inner SAVEPOINT (not the outer transaction).
    Under :class:`AsyncSession` driving asyncpg through
    ``disposable_pg_session()``, ``await session.commit()``
    commits the outermost transaction and the test rows
    survive the fixture rollback. The savepoint pattern was
    tried first (see PR review on commit eb56067) — rows
    leaked anyway, so we fall back to explicit teardown.

    Tests append the ``user_id`` they wrote to
    ``tracked_user_ids``; the fixture's teardown then deletes
    those rows from both budget tables so the suite is
    idempotent.
    """
    tracked_user_ids: list[UUID] = []
    async with disposable_pg_session() as s:
        s.tracked_user_ids = tracked_user_ids  # type: ignore[attr-defined]
        try:
            yield s
        finally:
            for uid in tracked_user_ids:
                await s.execute(
                    text(
                        "DELETE FROM algo.budget_reservations "
                        "WHERE user_id = :u"
                    ),
                    {"u": uid},
                )
                await s.execute(
                    text("DELETE FROM algo.user_budget " "WHERE user_id = :u"),
                    {"u": uid},
                )
            await s.commit()


@pytest.mark.asyncio
async def test_get_user_budget_default_when_missing(session):
    repo = BudgetRepo()
    out = await repo.get_user_budget(
        session,
        user_id=uuid4(),
    )
    assert out.allocated_inr == Decimal("0")
    assert out.enabled is False


@pytest.mark.asyncio
async def test_upsert_user_budget_roundtrip(session):
    repo = BudgetRepo()
    uid = uuid4()
    session.tracked_user_ids.append(uid)
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
    session.tracked_user_ids.append(uid)
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
        session,
        reservation_id=res_id,
    )
    assert current.state == ReservationState.PENDING
    assert current.reserved_inr == Decimal("7500.00")


@pytest.mark.asyncio
async def test_current_state_returns_latest(session):
    """Multiple events for one reservation_id → latest wins."""
    repo = BudgetRepo()
    res_id = uuid4()
    uid = uuid4()
    session.tracked_user_ids.append(uid)
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
                2026,
                5,
                24,
                10,
                0,
                0,
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
                2026,
                5,
                24,
                10,
                0,
                5,
                tzinfo=timezone.utc,
            ),
            kite_order_id="kite-123",
        ),
    )
    await session.commit()
    current = await repo.get_current_state(
        session,
        reservation_id=res_id,
    )
    assert current.state == ReservationState.SUBMITTED
    assert current.kite_order_id == "kite-123"


@pytest.mark.asyncio
async def test_sum_active_reservations(session):
    """Only ACTIVE_STATES contribute; terminal excluded."""
    repo = BudgetRepo()
    uid = uuid4()
    session.tracked_user_ids.append(uid)
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
            side="BUY",
            qty=10,
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
            side="BUY",
            qty=20,
            reserved_inr=Decimal("2000.00"),
            filled_qty=20,
            filled_inr=Decimal("2000.00"),
            transitioned_at=datetime.now(timezone.utc),
        ),
    )
    await session.commit()
    total = await repo.sum_active_reservations(
        session,
        user_id=uid,
    )
    assert total == Decimal("1000.00")  # only the SUBMITTED row
