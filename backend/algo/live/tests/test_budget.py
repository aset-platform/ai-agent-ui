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
    TerminalStateError,
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
    """Kite API error -> Decimal('0') (fail-closed).

    Updated for Task 0.2: broker-cash cap now fails closed so
    a Kite outage blocks new live orders rather than silently
    removing the cash ceiling.
    """

    async def boom(*args, **kwargs):
        raise RuntimeError("kite down")

    monkeypatch.setattr(
        "backend.algo.live.budget._kite_margins_for_user",
        boom,
    )
    out = await fetch_kite_available_cash(uuid4())
    assert out == Decimal("0")


@pytest.mark.asyncio
async def test_fetch_kite_available_cash_reads_equity_cash(
    monkeypatch,
):
    async def fake_margins(_uid):
        # kc.margins("equity") returns the segment directly, no "equity" wrapper
        return {
            "available": {"cash": "78200.50"},
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


def _terminal_reservation(
    state: ReservationState,
) -> BudgetReservation:
    return BudgetReservation(
        reservation_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        state=state,
        ticker="INFY.NS",
        side="BUY",
        qty=10,
        reserved_inr=Decimal("5000.00"),
        transitioned_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_state",
    [
        ReservationState.FILLED,
        ReservationState.CANCELLED,
        ReservationState.REJECTED,
        ReservationState.PARTIAL_CANCELLED,
        ReservationState.TIMEOUT,
    ],
)
async def test_transition_refuses_terminal_state(
    terminal_state: ReservationState,
):
    """transition() MUST raise TerminalStateError when the
    current reservation state is already terminal.  No new
    event row may be inserted."""
    fake_repo = MagicMock()
    fake_repo.get_current_state = AsyncMock(
        return_value=_terminal_reservation(terminal_state),
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
        patch("backend.algo.live.budget._invalidate_cache"),
    ):
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(commit=AsyncMock()),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        with pytest.raises(TerminalStateError) as exc_info:
            await transition(
                reservation_id=uuid4(),
                new_state=ReservationState.TIMEOUT,
            )
    assert exc_info.value.current_state == terminal_state
    assert exc_info.value.requested_state == ReservationState.TIMEOUT
    fake_repo.insert_reservation_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_transition_pending_to_submitted_still_works():
    """Non-terminal (PENDING) → SUBMITTED is accepted normally."""
    fake_repo = MagicMock()
    fake_repo.get_current_state = AsyncMock(
        return_value=_terminal_reservation(
            ReservationState.PENDING,
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
        patch("backend.algo.live.budget._invalidate_cache"),
    ):
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(commit=AsyncMock()),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        # Must NOT raise.
        await transition(
            reservation_id=uuid4(),
            new_state=ReservationState.SUBMITTED,
            kite_order_id="kite-42",
        )
    fake_repo.insert_reservation_event.assert_awaited_once()
    new_row = fake_repo.insert_reservation_event.await_args.args[1]
    assert new_row.state == ReservationState.SUBMITTED


@pytest.mark.asyncio
async def test_transition_submitted_to_filled_still_works():
    """SUBMITTED → FILLED (normal fill-sync path) is accepted."""
    fake_repo = MagicMock()
    fake_repo.get_current_state = AsyncMock(
        return_value=BudgetReservation(
            reservation_id=uuid4(),
            user_id=uuid4(),
            strategy_id=uuid4(),
            state=ReservationState.SUBMITTED,
            ticker="RELIANCE.NS",
            side="BUY",
            qty=5,
            reserved_inr=Decimal("12000.00"),
            transitioned_at=datetime.now(timezone.utc),
            kite_order_id="kite-99",
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
        patch("backend.algo.live.budget._invalidate_cache"),
    ):
        factory.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(commit=AsyncMock()),
        )
        factory.return_value.__aexit__ = AsyncMock(
            return_value=None,
        )
        await transition(
            reservation_id=uuid4(),
            new_state=ReservationState.FILLED,
            filled_qty=5,
            filled_inr=Decimal("12000.00"),
        )
    fake_repo.insert_reservation_event.assert_awaited_once()
    new_row = fake_repo.insert_reservation_event.await_args.args[1]
    assert new_row.state == ReservationState.FILLED
