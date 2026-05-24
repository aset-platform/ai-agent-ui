"""HTTP-level tests for /v1/algo/budget/* endpoints."""

from __future__ import annotations

import datetime as dt
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
    with (
        patch(
            "backend.algo.routes.budget.load_user_budget",
            AsyncMock(return_value=UserBudget(user_id=uuid4())),
        ),
        patch(
            "backend.algo.routes.budget.sum_open_position_cost",
            AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            "backend.algo.routes.budget.sum_active_reservations",
            AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            "backend.algo.routes.budget." "fetch_kite_available_cash",
            AsyncMock(return_value=Decimal("215000")),
        ),
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
    with (
        patch(
            "backend.algo.routes.budget.BudgetRepo",
            return_value=repo,
        ),
        patch(
            "backend.algo.routes.budget._session_factory",
        ) as factory,
        patch(
            "backend.algo.routes.budget._invalidate_cache",
        ),
        patch(
            "backend.algo.routes.budget.sum_open_position_cost",
            AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            "backend.algo.routes.budget.sum_active_reservations",
            AsyncMock(return_value=Decimal("0")),
        ),
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
    with (
        patch(
            "backend.algo.routes.budget.BudgetRepo",
            return_value=repo,
        ),
        patch(
            "backend.algo.routes.budget._session_factory",
        ) as factory,
        patch(
            "backend.algo.routes.budget._invalidate_cache",
        ),
        patch(
            "backend.algo.routes.budget.sum_open_position_cost",
            AsyncMock(return_value=Decimal("30000")),
        ),
        patch(
            "backend.algo.routes.budget.sum_active_reservations",
            AsyncMock(return_value=Decimal("8000")),
        ),
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
        side="BUY",
        qty=50,
        reserved_inr=Decimal("7500.00"),
        transitioned_at=dt.datetime.now(dt.timezone.utc),
    )
    repo = MagicMock()
    repo.list_active_reservations = AsyncMock(
        return_value=[fake_res],
    )
    with (
        patch(
            "backend.algo.routes.budget.BudgetRepo",
            return_value=repo,
        ),
        patch(
            "backend.algo.routes.budget._session_factory",
        ) as factory,
    ):
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
    assert out["reservations"][0]["state"] == "SUBMITTED"
