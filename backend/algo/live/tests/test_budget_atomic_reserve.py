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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from backend.algo.live.budget_reconciliation import (
    _list_pending,
    _list_submitted_and_partial,
)
from backend.algo.live.budget_repo import BudgetRepo
from backend.algo.live.budget_types import BudgetReservation, ReservationState
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


# ---------------------------------------------------------------------------
# Task 2.4 — per-ticker cost basis (no global SELL netting)
# ---------------------------------------------------------------------------


async def _insert_filled(
    session,
    *,
    uid,
    sid,
    ticker: str,
    side: str,
    qty: int,
    reserved_inr: Decimal,
    filled_qty: int,
    filled_inr: Decimal,
    metadata: dict | None = None,
) -> None:
    """Helper: insert a single FILLED reservation row."""
    repo = BudgetRepo()
    await repo.insert_reservation_event(
        session,
        BudgetReservation(
            reservation_id=uuid4(),
            user_id=uid,
            strategy_id=sid,
            state=ReservationState.FILLED,
            ticker=ticker,
            side=side,
            qty=qty,
            reserved_inr=reserved_inr,
            filled_qty=filled_qty,
            filled_inr=filled_inr,
            transitioned_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        ),
    )


@pytest.mark.asyncio
async def test_sell_proceeds_on_b_do_not_reduce_open_cost_of_a(
    user_id,
):
    """Task 2.4 — cross-ticker netting bug (RED before fix).

    User has:
      - Ticker A: open BUY (filled 10 shares @ ₹1 000 each = ₹10 000)
      - Ticker B: profitable round-trip — BUY ₹5 000, SELL ₹8 000

    Before fix: global net = 10_000 + 5_000 − 8_000 = 7_000
    (B's sell proceeds net against A's cost basis — WRONG).

    After fix: per-ticker:
      A: max(0, 10−0) × (10_000/10) = 10_000
      B: max(0, 10−10) × avg = 0  (fully closed)
    Total = 10_000.
    """
    repo = BudgetRepo()
    sid = uuid4()

    async with disposable_pg_session() as s:
        # Ticker A: open BUY, not yet closed.
        await _insert_filled(
            s,
            uid=user_id,
            sid=sid,
            ticker="A.NS",
            side="BUY",
            qty=10,
            reserved_inr=Decimal("10000"),
            filled_qty=10,
            filled_inr=Decimal("10000"),
        )
        # Ticker B: BUY leg.
        await _insert_filled(
            s,
            uid=user_id,
            sid=sid,
            ticker="B.NS",
            side="BUY",
            qty=10,
            reserved_inr=Decimal("5000"),
            filled_qty=10,
            filled_inr=Decimal("5000"),
        )
        # Ticker B: SELL leg — profitable (8 000 > 5 000).
        await _insert_filled(
            s,
            uid=user_id,
            sid=sid,
            ticker="B.NS",
            side="SELL",
            qty=10,
            reserved_inr=Decimal("5000"),
            filled_qty=10,
            filled_inr=Decimal("8000"),
        )
        await s.commit()

    async with disposable_pg_session() as s:
        cost = await repo.sum_open_position_cost(s, user_id=user_id)

    # Only A's open BUY should be counted; B is fully closed → 0.
    assert cost == Decimal("10000"), (
        f"expected 10000, got {cost} — "
        "B's profitable SELL is netting against A's open position"
    )


@pytest.mark.asyncio
async def test_partial_fill_overfill_does_not_reduce_active(
    user_id,
):
    """Task 2.4 — GREATEST guard on sum_active_reservations.

    When filled_inr slightly exceeds reserved_inr (e.g. slippage),
    that single row must not produce a *negative* contribution that
    reduces the total below the other rows' contribution.
    """
    repo = BudgetRepo()
    sid = uuid4()

    async with disposable_pg_session() as s:
        # Row 1: normal active BUY — ₹2 000 reserved, ₹0 filled.
        await repo.insert_reservation_event(
            s,
            BudgetReservation(
                reservation_id=uuid4(),
                user_id=user_id,
                strategy_id=sid,
                state=ReservationState.SUBMITTED,
                ticker="A.NS",
                side="BUY",
                qty=10,
                reserved_inr=Decimal("2000"),
                filled_qty=0,
                filled_inr=Decimal("0"),
                transitioned_at=datetime.now(timezone.utc),
            ),
        )
        # Row 2: partial-fill where slippage caused filled_inr to
        # exceed reserved_inr (₹1 000 reserved, ₹1 050 filled).
        await repo.insert_reservation_event(
            s,
            BudgetReservation(
                reservation_id=uuid4(),
                user_id=user_id,
                strategy_id=sid,
                state=ReservationState.PARTIAL,
                ticker="B.NS",
                side="BUY",
                qty=5,
                reserved_inr=Decimal("1000"),
                filled_qty=3,
                filled_inr=Decimal("1050"),
                transitioned_at=datetime.now(timezone.utc),
            ),
        )
        await s.commit()

    async with disposable_pg_session() as s:
        total = await repo.sum_active_reservations(
            s, user_id=user_id
        )

    # Row 2 should contribute GREATEST(1000−1050, 0) = 0.
    # Total must be exactly ₹2 000 (Row 1), not ₹1 950.
    assert total == Decimal("2000"), (
        f"expected 2000, got {total} — "
        "overfill row is producing a negative contribution"
    )


@pytest.mark.asyncio
async def test_single_open_buy_cost_unchanged(user_id):
    """Task 2.4 — simple single-ticker open BUY still returns
    the correct cost (regression guard)."""
    repo = BudgetRepo()
    sid = uuid4()

    async with disposable_pg_session() as s:
        await _insert_filled(
            s,
            uid=user_id,
            sid=sid,
            ticker="INFY.NS",
            side="BUY",
            qty=5,
            reserved_inr=Decimal("7500"),
            filled_qty=5,
            filled_inr=Decimal("7500"),
        )
        await s.commit()

    async with disposable_pg_session() as s:
        cost = await repo.sum_open_position_cost(s, user_id=user_id)

    assert cost == Decimal("7500")


# ---------------------------------------------------------------------------
# Task 2.3b — reconciler selects by LATEST reservation state
# ---------------------------------------------------------------------------


async def _insert_event(
    session,
    *,
    rid,
    uid,
    sid,
    state: ReservationState,
    ticker: str,
    transitioned_at: datetime,
) -> None:
    """Append one lifecycle event for an existing reservation_id."""
    repo = BudgetRepo()
    await repo.insert_reservation_event(
        session,
        BudgetReservation(
            reservation_id=rid,
            user_id=uid,
            strategy_id=sid,
            state=state,
            ticker=ticker,
            side="BUY",
            qty=10,
            reserved_inr=Decimal("5000"),
            filled_qty=0,
            filled_inr=Decimal("0"),
            kite_order_id="kite-1",
            transitioned_at=transitioned_at,
            metadata={"mode": "live"},
        ),
    )


@pytest.mark.asyncio
async def test_list_active_excludes_terminal_backlog(user_id):
    """Task 2.3b — RED before the latest-state-first rewrite.

    ``_list_submitted_and_partial`` must return only reservations
    whose CURRENT (latest) state is SUBMITTED/PARTIAL. The old
    ``WHERE state IN (...)`` BEFORE ``DISTINCT ON`` matched a
    reservation via its stale SUBMITTED row even after it had
    transitioned to a terminal state (TIMEOUT/FILLED), so the
    reconciler re-processed the whole terminal backlog every tick.

    Four reservations:
      - latest=SUBMITTED            → MUST be returned
      - latest=PARTIAL              → MUST be returned
      - SUBMITTED→TIMEOUT (2 rows)  → MUST NOT be returned
      - SUBMITTED→FILLED  (2 rows)  → MUST NOT be returned

    FAILS against the filter-before-distinct query (the terminal
    pair leak through via their old SUBMITTED rows); PASSES after.
    """
    repo = BudgetRepo()
    sid = uuid4()
    rid_submitted = uuid4()
    rid_partial = uuid4()
    rid_timeout = uuid4()
    rid_filled = uuid4()
    base = datetime.now(timezone.utc) - timedelta(minutes=10)

    async with disposable_pg_session() as s:
        # Latest = SUBMITTED (single row).
        await _insert_event(
            s,
            rid=rid_submitted,
            uid=user_id,
            sid=sid,
            state=ReservationState.SUBMITTED,
            ticker="SUB.NS",
            transitioned_at=base,
        )
        # Latest = PARTIAL (single row).
        await _insert_event(
            s,
            rid=rid_partial,
            uid=user_id,
            sid=sid,
            state=ReservationState.PARTIAL,
            ticker="PAR.NS",
            transitioned_at=base,
        )
        # SUBMITTED then TIMEOUT — latest is terminal.
        await _insert_event(
            s,
            rid=rid_timeout,
            uid=user_id,
            sid=sid,
            state=ReservationState.SUBMITTED,
            ticker="TMO.NS",
            transitioned_at=base,
        )
        await _insert_event(
            s,
            rid=rid_timeout,
            uid=user_id,
            sid=sid,
            state=ReservationState.TIMEOUT,
            ticker="TMO.NS",
            transitioned_at=base + timedelta(minutes=5),
        )
        # SUBMITTED then FILLED — latest is terminal.
        await _insert_event(
            s,
            rid=rid_filled,
            uid=user_id,
            sid=sid,
            state=ReservationState.SUBMITTED,
            ticker="FIL.NS",
            transitioned_at=base,
        )
        await _insert_event(
            s,
            rid=rid_filled,
            uid=user_id,
            sid=sid,
            state=ReservationState.FILLED,
            ticker="FIL.NS",
            transitioned_at=base + timedelta(minutes=5),
        )
        await s.commit()

    rows = await _list_submitted_and_partial()
    mine = {
        r.reservation_id: r
        for r in rows
        if r.reservation_id
        in {
            rid_submitted,
            rid_partial,
            rid_timeout,
            rid_filled,
        }
    }

    assert set(mine) == {rid_submitted, rid_partial}, (
        "expected only the two active reservations; got "
        f"{sorted(str(k) for k in mine)}"
    )
    assert mine[rid_submitted].state == ReservationState.SUBMITTED
    assert mine[rid_partial].state == ReservationState.PARTIAL
    assert rid_timeout not in mine, (
        "terminal TIMEOUT reservation leaked via stale SUBMITTED row"
    )
    assert rid_filled not in mine, (
        "terminal FILLED reservation leaked via stale SUBMITTED row"
    )


@pytest.mark.asyncio
async def test_list_pending_excludes_terminal_backlog(user_id):
    """Task 2.3c — RED before the latest-state-first rewrite of
    ``_list_pending``.

    ``_list_pending`` must return only reservations whose CURRENT
    (latest) state is PENDING. The old ``WHERE state = 'PENDING'``
    BEFORE ``DISTINCT ON`` matched a reservation via its stale
    PENDING row even after it had transitioned to a terminal state,
    causing ``reconcile_pending_timeouts`` to re-attempt the
    transition and spam TerminalStateError tracebacks.

    Three reservations:
      - latest=PENDING                   → MUST be returned
      - PENDING→TIMEOUT (2 rows present) → MUST NOT be returned
      - PENDING→FILLED  (2 rows present) → MUST NOT be returned

    FAILS against the filter-before-distinct query (terminal pair
    leaks through via old PENDING rows); PASSES after the fix.
    """
    repo = BudgetRepo()
    sid = uuid4()
    rid_pending = uuid4()
    rid_timeout = uuid4()
    rid_filled = uuid4()
    base = datetime.now(timezone.utc) - timedelta(minutes=10)

    async with disposable_pg_session() as s:
        # Latest = PENDING (single row — still active).
        await _insert_event(
            s,
            rid=rid_pending,
            uid=user_id,
            sid=sid,
            state=ReservationState.PENDING,
            ticker="PND.NS",
            transitioned_at=base,
        )
        # PENDING then TIMEOUT — latest is terminal.
        await _insert_event(
            s,
            rid=rid_timeout,
            uid=user_id,
            sid=sid,
            state=ReservationState.PENDING,
            ticker="TMO.NS",
            transitioned_at=base,
        )
        await _insert_event(
            s,
            rid=rid_timeout,
            uid=user_id,
            sid=sid,
            state=ReservationState.TIMEOUT,
            ticker="TMO.NS",
            transitioned_at=base + timedelta(minutes=5),
        )
        # PENDING then FILLED — latest is terminal.
        await _insert_event(
            s,
            rid=rid_filled,
            uid=user_id,
            sid=sid,
            state=ReservationState.PENDING,
            ticker="FIL.NS",
            transitioned_at=base,
        )
        await _insert_event(
            s,
            rid=rid_filled,
            uid=user_id,
            sid=sid,
            state=ReservationState.FILLED,
            ticker="FIL.NS",
            transitioned_at=base + timedelta(minutes=5),
        )
        await s.commit()

    rows = await _list_pending()
    mine = {
        r.reservation_id: r
        for r in rows
        if r.reservation_id in {rid_pending, rid_timeout, rid_filled}
    }

    assert set(mine) == {rid_pending}, (
        "expected only the PENDING reservation; got "
        f"{sorted(str(k) for k in mine)}"
    )
    assert mine[rid_pending].state == ReservationState.PENDING
    assert rid_timeout not in mine, (
        "terminal TIMEOUT reservation leaked via stale PENDING row"
    )
    assert rid_filled not in mine, (
        "terminal FILLED reservation leaked via stale PENDING row"
    )


@pytest.mark.asyncio
async def test_per_strategy_overfill_does_not_reduce_active(
    user_id,
):
    """Task 2.4 — GREATEST floor on
    sum_active_reservations_for_strategy.

    When filled_inr slightly exceeds reserved_inr (slippage) on one
    active row, that row must contribute 0 — not a negative — so the
    overfill cannot drag the total below the other rows' contribution.

    Setup (all belong to strategy sid_a):
      Row 1 (SUBMITTED): reserved=₹3 000, filled=₹0    → contrib ₹3 000
      Row 2 (PARTIAL):   reserved=₹1 000, filled=₹1 100 → contrib ₹0
                                                (GREATEST floors it)
    Expected total: ₹3 000 (not ₹2 900).

    A second strategy sid_b has its own ₹5 000 SUBMITTED row and
    must NOT affect sid_a's total.
    """
    repo = BudgetRepo()
    sid_a = uuid4()
    sid_b = uuid4()

    async with disposable_pg_session() as s:
        # sid_a — Row 1: normal active BUY, no fill yet.
        await repo.insert_reservation_event(
            s,
            BudgetReservation(
                reservation_id=uuid4(),
                user_id=user_id,
                strategy_id=sid_a,
                state=ReservationState.SUBMITTED,
                ticker="A.NS",
                side="BUY",
                qty=10,
                reserved_inr=Decimal("3000"),
                filled_qty=0,
                filled_inr=Decimal("0"),
                transitioned_at=datetime.now(timezone.utc),
                metadata={"mode": "live"},
            ),
        )
        # sid_a — Row 2: partial-fill with slippage overfill.
        await repo.insert_reservation_event(
            s,
            BudgetReservation(
                reservation_id=uuid4(),
                user_id=user_id,
                strategy_id=sid_a,
                state=ReservationState.PARTIAL,
                ticker="B.NS",
                side="BUY",
                qty=5,
                reserved_inr=Decimal("1000"),
                filled_qty=3,
                filled_inr=Decimal("1100"),
                transitioned_at=datetime.now(timezone.utc),
                metadata={"mode": "live"},
            ),
        )
        # sid_b — unrelated BUY that must not bleed into sid_a.
        await repo.insert_reservation_event(
            s,
            BudgetReservation(
                reservation_id=uuid4(),
                user_id=user_id,
                strategy_id=sid_b,
                state=ReservationState.SUBMITTED,
                ticker="C.NS",
                side="BUY",
                qty=20,
                reserved_inr=Decimal("5000"),
                filled_qty=0,
                filled_inr=Decimal("0"),
                transitioned_at=datetime.now(timezone.utc),
                metadata={"mode": "live"},
            ),
        )
        await s.commit()

    async with disposable_pg_session() as s:
        total_a = await repo.sum_active_reservations_for_strategy(
            s,
            user_id=user_id,
            strategy_id=sid_a,
        )
        total_b = await repo.sum_active_reservations_for_strategy(
            s,
            user_id=user_id,
            strategy_id=sid_b,
        )

    # Row 2 must floor at 0 — not drag total below ₹3 000.
    assert total_a == Decimal("3000"), (
        f"expected 3000, got {total_a} — "
        "overfill row is producing a negative contribution "
        "in sum_active_reservations_for_strategy"
    )
    # sid_b must be unaffected.
    assert total_b == Decimal("5000"), (
        f"expected 5000, got {total_b}"
    )
