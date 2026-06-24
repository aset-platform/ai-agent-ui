"""Integration tests for atomic headroom-aware reservation.

``BudgetRepo.reserve_if_headroom`` is the single-transaction
primitive that closes the TOCTOU over-deploy gap (Critical C4):
it locks the user's ``algo.user_budget`` row ``FOR UPDATE``,
recomputes headroom UNCACHED inside the txn, and inserts a
PENDING reservation only if ``headroom >= reserved_inr``.

Real-PG via ``disposable_pg_session`` (NullPool) — same fixture
shape as ``test_budget_repo.py``.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from backend.algo.live.budget_repo import BudgetRepo
from backend.algo.live.budget_types import ReservationState
from db.engine import disposable_pg_session


async def _seed_budget(uid: UUID, allocated: Decimal) -> None:
    """Insert the user_budget row in its OWN committed txn so
    concurrent FOR-UPDATE sessions can lock it."""
    repo = BudgetRepo()
    async with disposable_pg_session() as s:
        await repo.upsert_user_budget(
            s,
            user_id=uid,
            allocated_inr=allocated,
            enabled=True,
        )
        await s.commit()


async def _cleanup(uid: UUID) -> None:
    async with disposable_pg_session() as s:
        await s.execute(
            text(
                "DELETE FROM algo.budget_reservations "
                "WHERE user_id = :u"
            ),
            {"u": uid},
        )
        await s.execute(
            text("DELETE FROM algo.user_budget WHERE user_id = :u"),
            {"u": uid},
        )
        await s.commit()


@pytest.fixture
async def user_id():
    uid = uuid4()
    yield uid
    await _cleanup(uid)


@pytest.mark.asyncio
async def test_reserve_within_headroom_returns_uuid(user_id):
    """Reserve under the cap → UUID returned + PENDING row."""
    await _seed_budget(user_id, Decimal("100000"))
    repo = BudgetRepo()
    async with disposable_pg_session() as s:
        rid = await repo.reserve_if_headroom(
            s,
            user_id=user_id,
            strategy_id=uuid4(),
            ticker="INFY.NS",
            side="BUY",
            qty=10,
            reserved_inr=Decimal("50000"),
            allocated_inr=Decimal("100000"),
            metadata={"mode": "live"},
        )
        await s.commit()
    assert isinstance(rid, UUID)

    async with disposable_pg_session() as s:
        cur = await repo.get_current_state(s, reservation_id=rid)
    assert cur is not None
    assert cur.state == ReservationState.PENDING
    assert cur.reserved_inr == Decimal("50000")


@pytest.mark.asyncio
async def test_reserve_exceeding_headroom_returns_none(user_id):
    """Reserve over the cap → None + NO row inserted."""
    await _seed_budget(user_id, Decimal("40000"))
    repo = BudgetRepo()
    async with disposable_pg_session() as s:
        rid = await repo.reserve_if_headroom(
            s,
            user_id=user_id,
            strategy_id=uuid4(),
            ticker="INFY.NS",
            side="BUY",
            qty=10,
            reserved_inr=Decimal("50000"),
            allocated_inr=Decimal("40000"),
            metadata={"mode": "live"},
        )
        await s.commit()
    assert rid is None

    async with disposable_pg_session() as s:
        res = await s.execute(
            text(
                "SELECT COUNT(*) AS n FROM "
                "algo.budget_reservations WHERE user_id = :u"
            ),
            {"u": user_id},
        )
        n = res.mappings().first()["n"]
    assert n == 0


@pytest.mark.asyncio
async def test_reserve_deducts_existing_active(user_id):
    """An existing ACTIVE reservation shrinks headroom so a
    second reserve that would overflow is rejected."""
    await _seed_budget(user_id, Decimal("100000"))
    repo = BudgetRepo()
    sid = uuid4()
    async with disposable_pg_session() as s:
        first = await repo.reserve_if_headroom(
            s,
            user_id=user_id,
            strategy_id=sid,
            ticker="A.NS",
            side="BUY",
            qty=10,
            reserved_inr=Decimal("70000"),
            allocated_inr=Decimal("100000"),
            metadata={"mode": "live"},
        )
        await s.commit()
    assert isinstance(first, UUID)

    # headroom now 30k; a 40k reserve must fail.
    async with disposable_pg_session() as s:
        second = await repo.reserve_if_headroom(
            s,
            user_id=user_id,
            strategy_id=sid,
            ticker="B.NS",
            side="BUY",
            qty=10,
            reserved_inr=Decimal("40000"),
            allocated_inr=Decimal("100000"),
            metadata={"mode": "live"},
        )
        await s.commit()
    assert second is None


@pytest.mark.asyncio
async def test_concurrent_reserves_one_wins(user_id):
    """Two concurrent reserves whose combined cost exceeds
    allocated_inr → exactly ONE returns a UUID. The FOR UPDATE
    lock serializes the headroom recompute so the loser sees the
    winner's row and is rejected."""
    await _seed_budget(user_id, Decimal("100000"))
    sid = uuid4()

    async def _attempt() -> UUID | None:
        repo = BudgetRepo()
        # Each coroutine gets its OWN connection so the FOR
        # UPDATE row lock actually serializes them.
        async with disposable_pg_session() as s:
            rid = await repo.reserve_if_headroom(
                s,
                user_id=user_id,
                strategy_id=sid,
                ticker="A.NS",
                side="BUY",
                qty=10,
                reserved_inr=Decimal("60000"),
                allocated_inr=Decimal("100000"),
                metadata={"mode": "live"},
            )
            await s.commit()
            return rid

    results = await asyncio.gather(_attempt(), _attempt())
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1, results
    assert len(losers) == 1, results

    # Exactly one PENDING row persisted.
    async with disposable_pg_session() as s:
        res = await s.execute(
            text(
                "SELECT COUNT(*) AS n FROM "
                "algo.budget_reservations WHERE user_id = :u"
            ),
            {"u": user_id},
        )
        n = res.mappings().first()["n"]
    assert n == 1


@pytest.mark.asyncio
async def test_concurrent_reserves_no_budget_row_one_wins(user_id):
    """Critical C4 — the no-row concurrency hole.

    With NO seeded ``algo.user_budget`` row the ``SELECT ... FOR
    UPDATE`` matches zero rows and serializes NOTHING — so two
    concurrent reservers whose combined cost exceeds the caller-
    supplied ``allocated_inr`` could BOTH pass and over-deploy. The
    per-user advisory xact lock (``pg_advisory_xact_lock``) must
    serialize them regardless of row existence: exactly ONE wins.

    This test FAILS before the advisory lock (both reservers pass)
    and PASSES after.
    """
    # Deliberately NO _seed_budget call — the row does not exist.
    sid = uuid4()

    async def _attempt() -> UUID | None:
        repo = BudgetRepo()
        async with disposable_pg_session() as s:
            rid = await repo.reserve_if_headroom(
                s,
                user_id=user_id,
                strategy_id=sid,
                ticker="A.NS",
                side="BUY",
                qty=10,
                reserved_inr=Decimal("60000"),
                allocated_inr=Decimal("100000"),
                metadata={"mode": "live"},
            )
            await s.commit()
            return rid

    results = await asyncio.gather(_attempt(), _attempt())
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1, results
    assert len(losers) == 1, results

    async with disposable_pg_session() as s:
        res = await s.execute(
            text(
                "SELECT COUNT(*) AS n FROM "
                "algo.budget_reservations WHERE user_id = :u"
            ),
            {"u": user_id},
        )
        n = res.mappings().first()["n"]
    assert n == 1


@pytest.mark.asyncio
async def test_active_for_strategy_counts_only_that_strategy(
    user_id,
):
    """Critical C4 — deployed = filled + in-flight. The per-
    strategy active-reservation sum counts PENDING BUYs for ONE
    strategy and excludes other strategies, SELLs and dry-run."""
    await _seed_budget(user_id, Decimal("1000000"))
    repo = BudgetRepo()
    sid_a = uuid4()
    sid_b = uuid4()

    async with disposable_pg_session() as s:
        # strategy A: a ₹50k live BUY (counts).
        await repo.reserve_if_headroom(
            s,
            user_id=user_id,
            strategy_id=sid_a,
            ticker="A.NS",
            side="BUY",
            qty=10,
            reserved_inr=Decimal("50000"),
            allocated_inr=Decimal("1000000"),
            metadata={"mode": "live"},
        )
        # strategy B: a ₹30k live BUY (must NOT count for A).
        await repo.reserve_if_headroom(
            s,
            user_id=user_id,
            strategy_id=sid_b,
            ticker="B.NS",
            side="BUY",
            qty=10,
            reserved_inr=Decimal("30000"),
            allocated_inr=Decimal("1000000"),
            metadata={"mode": "live"},
        )
        await s.commit()

    async with disposable_pg_session() as s:
        a_total = await repo.sum_active_reservations_for_strategy(
            s,
            user_id=user_id,
            strategy_id=sid_a,
        )
        b_total = await repo.sum_active_reservations_for_strategy(
            s,
            user_id=user_id,
            strategy_id=sid_b,
        )
    assert a_total == Decimal("50000")
    assert b_total == Decimal("30000")
