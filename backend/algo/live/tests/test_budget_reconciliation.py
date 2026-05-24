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
            datetime.now(timezone.utc) - timedelta(seconds=121)
        ),
    )
    with patch(
        "backend.algo.live.budget_reconciliation._list_pending",
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
    pending = BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=ReservationState.PENDING,
        ticker="INFY.NS",
        side="BUY", qty=50,
        reserved_inr=Decimal("7500.00"),
        transitioned_at=(
            datetime.now(timezone.utc) - timedelta(seconds=30)
        ),
    )
    with patch(
        "backend.algo.live.budget_reconciliation._list_pending",
        AsyncMock(return_value=[pending]),
    ), patch(
        "backend.algo.live.budget_reconciliation.transition",
        AsyncMock(),
    ) as mock_trans:
        await reconcile_pending_timeouts()
    mock_trans.assert_not_awaited()


@pytest.mark.asyncio
async def test_submitted_complete_transitions_to_filled():
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
        "backend.algo.live.budget_reconciliation"
        "._fetch_kite_order_status",
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
            "status": "OPEN",
            "filled_quantity": 80,
            "average_price": "100.00",
        }

    with patch(
        "backend.algo.live.budget_reconciliation"
        "._fetch_kite_order_status",
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
        "backend.algo.live.budget_reconciliation"
        "._fetch_kite_order_status",
        fake_kite_status,
    ), patch(
        "backend.algo.live.budget_reconciliation.transition",
        AsyncMock(),
    ) as mock_trans:
        await reconcile_one(submitted)
    mock_trans.assert_awaited_once()
    kwargs = mock_trans.await_args.kwargs
    assert kwargs["new_state"] == ReservationState.CANCELLED
