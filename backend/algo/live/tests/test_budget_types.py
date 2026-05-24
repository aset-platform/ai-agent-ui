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
