"""Unit tests for POST /v1/algo/budget/reservations/{id}/force-release.

Covers the Task-2.3 regression fix: ``_force_release_impl`` must
return HTTP 409 (not 500) when ``transition()`` raises
``TerminalStateError``, and must still return success for a
non-terminal reservation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.algo.live.budget_types import (
    ReservationState,
    TerminalStateError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_reservation(state: ReservationState, user_id=None):
    """Return a minimal mock reservation object."""
    res = MagicMock()
    res.reservation_id = uuid4()
    res.user_id = user_id or uuid4()
    res.state = state
    return res


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_force_release_terminal_returns_409():
    """Force-releasing a FILLED reservation must raise 409, not 500."""
    from backend.algo.routes.budget import _force_release_impl

    user_id = uuid4()
    res = _make_reservation(ReservationState.FILLED, user_id=user_id)

    terminal_err = TerminalStateError(
        reservation_id=res.reservation_id,
        current_state=ReservationState.FILLED,
        requested_state=ReservationState.CANCELLED,
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    mock_repo = MagicMock()
    mock_repo.get_current_state = AsyncMock(return_value=res)

    with (
        patch(
            "backend.algo.routes.budget._session_factory",
            return_value=mock_factory,
        ),
        patch(
            "backend.algo.routes.budget.BudgetRepo",
            return_value=mock_repo,
        ),
        patch(
            "backend.algo.routes.budget.transition",
            new_callable=AsyncMock,
            side_effect=terminal_err,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _force_release_impl(
                user_id=user_id,
                reservation_id=res.reservation_id,
            )

    assert exc_info.value.status_code == 409
    assert "terminal state" in exc_info.value.detail.lower()
    assert "FILLED" in exc_info.value.detail


@pytest.mark.asyncio
async def test_force_release_non_terminal_returns_released():
    """Force-releasing a PENDING reservation succeeds with status=released."""
    from backend.algo.routes.budget import _force_release_impl

    user_id = uuid4()
    res = _make_reservation(ReservationState.PENDING, user_id=user_id)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    mock_repo = MagicMock()
    mock_repo.get_current_state = AsyncMock(return_value=res)

    with (
        patch(
            "backend.algo.routes.budget._session_factory",
            return_value=mock_factory,
        ),
        patch(
            "backend.algo.routes.budget.BudgetRepo",
            return_value=mock_repo,
        ),
        patch(
            "backend.algo.routes.budget.transition",
            new_callable=AsyncMock,
        ),
    ):
        result = await _force_release_impl(
            user_id=user_id,
            reservation_id=res.reservation_id,
        )

    assert result == {"status": "released"}
