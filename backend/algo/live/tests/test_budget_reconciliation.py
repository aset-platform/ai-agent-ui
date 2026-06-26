"""Tests for budget reservation lifecycle reconciliation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.live.budget_reconciliation import (
    reconcile_one,
    reconcile_pending_timeouts,
    reconcile_submitted,
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
        side="BUY",
        qty=50,
        reserved_inr=Decimal("7500.00"),
        transitioned_at=(datetime.now(timezone.utc) - timedelta(seconds=121)),
    )
    with (
        patch(
            "backend.algo.live.budget_reconciliation._list_pending",
            AsyncMock(return_value=[pending]),
        ),
        patch(
            "backend.algo.live.budget_reconciliation.transition",
            AsyncMock(),
        ) as mock_trans,
    ):
        await reconcile_pending_timeouts()
    mock_trans.assert_awaited_once()
    kwargs = mock_trans.await_args.kwargs
    assert kwargs["new_state"] == ReservationState.TIMEOUT


@pytest.mark.asyncio
async def test_pending_under_120s_not_timed_out():
    pending = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.PENDING,
        ticker="INFY.NS",
        side="BUY",
        qty=50,
        reserved_inr=Decimal("7500.00"),
        transitioned_at=(datetime.now(timezone.utc) - timedelta(seconds=30)),
    )
    with (
        patch(
            "backend.algo.live.budget_reconciliation._list_pending",
            AsyncMock(return_value=[pending]),
        ),
        patch(
            "backend.algo.live.budget_reconciliation.transition",
            AsyncMock(),
        ) as mock_trans,
    ):
        await reconcile_pending_timeouts()
    mock_trans.assert_not_awaited()


@pytest.mark.asyncio
async def test_submitted_complete_transitions_to_filled():
    """SUBMITTED + Kite COMPLETE → FILLED (fallback path via order_history)."""
    submitted = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.SUBMITTED,
        ticker="INFY.NS",
        side="BUY",
        qty=50,
        reserved_inr=Decimal("7500.00"),
        kite_order_id="kite-99",
        transitioned_at=datetime.now(timezone.utc),
    )

    async def fake_fetch(kite_client, kite_order_id, user_id):
        return {
            "status": "COMPLETE",
            "filled_quantity": 50,
            "average_price": "150.00",
        }

    with (
        patch(
            "backend.algo.live.budget_reconciliation"
            "._fetch_order_status_for_user",
            fake_fetch,
        ),
        patch(
            "backend.algo.live.budget_reconciliation.transition",
            AsyncMock(),
        ) as mock_trans,
    ):
        await reconcile_one(submitted, kite_client=MagicMock())
    mock_trans.assert_awaited_once()
    kwargs = mock_trans.await_args.kwargs
    assert kwargs["new_state"] == ReservationState.FILLED
    assert kwargs["filled_qty"] == 50
    assert kwargs["filled_inr"] == Decimal("7500.00")


@pytest.mark.asyncio
async def test_submitted_partial_transitions_to_partial():
    """SUBMITTED + Kite OPEN+filled → PARTIAL (fallback path)."""
    submitted = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.SUBMITTED,
        ticker="INFY.NS",
        side="BUY",
        qty=100,
        reserved_inr=Decimal("10000.00"),
        kite_order_id="kite-99",
        transitioned_at=datetime.now(timezone.utc),
    )

    async def fake_fetch(kite_client, kite_order_id, user_id):
        return {
            "status": "OPEN",
            "filled_quantity": 80,
            "average_price": "100.00",
        }

    with (
        patch(
            "backend.algo.live.budget_reconciliation"
            "._fetch_order_status_for_user",
            fake_fetch,
        ),
        patch(
            "backend.algo.live.budget_reconciliation.transition",
            AsyncMock(),
        ) as mock_trans,
    ):
        await reconcile_one(submitted, kite_client=MagicMock())
    mock_trans.assert_awaited_once()
    kwargs = mock_trans.await_args.kwargs
    assert kwargs["new_state"] == ReservationState.PARTIAL
    assert kwargs["filled_qty"] == 80
    assert kwargs["filled_inr"] == Decimal("8000.00")


@pytest.mark.asyncio
async def test_submitted_cancelled_transitions_to_cancelled():
    """SUBMITTED + Kite CANCELLED (no fills) → CANCELLED (fallback path)."""
    submitted = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.SUBMITTED,
        ticker="INFY.NS",
        side="BUY",
        qty=50,
        reserved_inr=Decimal("7500.00"),
        kite_order_id="kite-99",
        transitioned_at=datetime.now(timezone.utc),
    )

    async def fake_fetch(kite_client, kite_order_id, user_id):
        return {"status": "CANCELLED", "filled_quantity": 0}

    with (
        patch(
            "backend.algo.live.budget_reconciliation"
            "._fetch_order_status_for_user",
            fake_fetch,
        ),
        patch(
            "backend.algo.live.budget_reconciliation.transition",
            AsyncMock(),
        ) as mock_trans,
    ):
        await reconcile_one(submitted, kite_client=MagicMock())
    mock_trans.assert_awaited_once()
    kwargs = mock_trans.await_args.kwargs
    assert kwargs["new_state"] == ReservationState.CANCELLED


# ---------------------------------------------------------------------------
# New tests: batch order-book fast-path + fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_one_uses_order_book_fast_path():
    """order_book hit → FILLED; _fetch_order_status_for_user NOT called."""
    uid = uuid4()
    submitted = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uid,
        strategy_id=uuid4(),
        state=ReservationState.SUBMITTED,
        ticker="RELIANCE.NS",
        side="BUY",
        qty=10,
        reserved_inr=Decimal("2500.00"),
        kite_order_id="kite-99",
        transitioned_at=datetime.now(timezone.utc),
    )
    order_book = {
        "kite-99": {
            "status": "COMPLETE",
            "filled_quantity": 10,
            "average_price": 100,
        }
    }
    mock_fetch = AsyncMock()
    with (
        patch(
            "backend.algo.live.budget_reconciliation"
            "._fetch_order_status_for_user",
            mock_fetch,
        ),
        patch(
            "backend.algo.live.budget_reconciliation.transition",
            AsyncMock(),
        ) as mock_trans,
    ):
        await reconcile_one(
            submitted,
            kite_client=MagicMock(),
            order_book=order_book,
        )
    mock_fetch.assert_not_called()
    mock_trans.assert_awaited_once()
    assert mock_trans.await_args.kwargs["new_state"] == ReservationState.FILLED


@pytest.mark.asyncio
async def test_reconcile_one_falls_back_when_order_not_in_book():
    """order_book miss → fallback to _fetch_order_status_for_user."""
    uid = uuid4()
    submitted = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uid,
        strategy_id=uuid4(),
        state=ReservationState.SUBMITTED,
        ticker="TCS.NS",
        side="BUY",
        qty=5,
        reserved_inr=Decimal("1500.00"),
        kite_order_id="kite-missing",
        transitioned_at=datetime.now(timezone.utc),
    )
    # order_book does NOT contain kite-missing
    order_book: dict[str, dict] = {"kite-other": {"status": "OPEN"}}
    mock_fetch = AsyncMock(
        return_value={
            "status": "COMPLETE",
            "filled_quantity": 5,
            "average_price": 300,
        }
    )
    with (
        patch(
            "backend.algo.live.budget_reconciliation"
            "._fetch_order_status_for_user",
            mock_fetch,
        ),
        patch(
            "backend.algo.live.budget_reconciliation.transition",
            AsyncMock(),
        ) as mock_trans,
    ):
        await reconcile_one(
            submitted,
            kite_client=MagicMock(),
            order_book=order_book,
        )
    mock_fetch.assert_awaited_once()
    mock_trans.assert_awaited_once()
    assert mock_trans.await_args.kwargs["new_state"] == ReservationState.FILLED


@pytest.mark.asyncio
async def test_reconcile_submitted_calls_orders_once_per_user():
    """reconcile_submitted makes exactly one orders() call per user.

    N real reservations for one user → kc._kc.orders called once;
    _fetch_order_status_for_user never called (all in book).
    """
    uid = uuid4()
    sid = uuid4()
    order_ids = [f"kite-{i}" for i in range(3)]

    def _make_res(oid):
        return BudgetReservation(
            reservation_id=uuid4(),
            user_id=uid,
            strategy_id=sid,
            state=ReservationState.SUBMITTED,
            ticker="INFY.NS",
            side="BUY",
            qty=10,
            reserved_inr=Decimal("1000.00"),
            kite_order_id=oid,
            transitioned_at=datetime.now(timezone.utc),
        )

    real_reservations = [_make_res(oid) for oid in order_ids]

    # Kite orders() returns all 3 as COMPLETE
    kite_book = [
        {
            "order_id": oid,
            "status": "COMPLETE",
            "filled_quantity": 10,
            "average_price": 100,
            "status_message": "",
        }
        for oid in order_ids
    ]

    mock_kc = MagicMock()
    mock_kc._kc.orders.return_value = kite_book

    mock_fetch = AsyncMock()

    with (
        patch(
            "backend.algo.live.budget_reconciliation"
            "._list_submitted_and_partial",
            AsyncMock(return_value=real_reservations),
        ),
        patch(
            "backend.algo.live.budget_reconciliation._build_kite_for_user",
            AsyncMock(return_value=mock_kc),
        ),
        patch(
            "backend.algo.live.budget_reconciliation"
            "._fetch_order_status_for_user",
            mock_fetch,
        ),
        patch(
            "backend.algo.live.budget_reconciliation.transition",
            AsyncMock(),
        ) as mock_trans,
    ):
        result = await reconcile_submitted()

    # orders() called exactly once for the single user
    mock_kc._kc.orders.assert_called_once()
    # per-order fallback never needed (all in book)
    mock_fetch.assert_not_called()
    # all 3 reconciled as FILLED
    assert mock_trans.await_count == 3
    assert result["kite_checked"] == 3
