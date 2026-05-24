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
    with (
        patch(
            "backend.algo.live.budget.BudgetRepo",
            return_value=fake_repo,
        ),
        patch(
            "backend.algo.live.budget._session_factory",
        ) as factory,
    ):
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
    """Kite API error -> Decimal('inf') (fail-open)."""

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
    with (
        patch(
            "backend.algo.live.budget.BudgetRepo",
            return_value=fake_repo,
        ),
        patch(
            "backend.algo.live.budget._session_factory",
        ) as factory,
        patch(
            "backend.algo.live.budget._invalidate_cache",
        ) as inv,
    ):
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
            side="BUY",
            qty=50,
            reserved_inr=Decimal("7500.00"),
            transitioned_at=datetime.now(timezone.utc),
        ),
    )
    fake_repo.insert_reservation_event = AsyncMock()
    with (
        patch(
            "backend.algo.live.budget.BudgetRepo",
            return_value=fake_repo,
        ),
        patch(
            "backend.algo.live.budget._session_factory",
        ) as factory,
        patch(
            "backend.algo.live.budget._invalidate_cache",
        ),
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
    call_args = fake_repo.insert_reservation_event.await_args
    new_row = call_args.args[1]
    assert new_row.state == ReservationState.SUBMITTED
    assert new_row.kite_order_id == "kite-99"


@pytest.mark.asyncio
async def test_sum_open_position_cost_returns_zero_by_default(
    monkeypatch,
):
    """sum_open_position_cost reads from algo.events. Empty
    history -> zero."""

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
    with (
        patch(
            "backend.algo.live.budget.BudgetRepo",
            return_value=fake_repo,
        ),
        patch(
            "backend.algo.live.budget._session_factory",
        ) as factory,
    ):
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        out = await sum_active_reservations(uuid4())
    assert out == Decimal("8500.00")
