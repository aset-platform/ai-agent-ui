"""pre_trade_check self-protects on budget I/O error (Task 0.3).

Tests:
1. load_user_budget raises → returns LIVE_BUDGET_CAP reject (fail closed).
2. sum_open_position_cost raises → returns LIVE_BUDGET_CAP reject.
3. sum_active_reservations raises → returns LIVE_BUDGET_CAP reject.
4. fetch_kite_available_cash raises (non-dry-run) → LIVE_BUDGET_CAP.
5. Happy path: all budget I/O succeeds → accept.

Patched at backend.algo.live.safety (source of the import), matching
the project's mock-patching-gotchas convention.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.algo.live.budget_types import UserBudget
from backend.algo.live.safety import pre_trade_check
from backend.algo.paper.types import (
    AccountState,
    RejectReason,
    Signal,
)

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

_USER_ID = uuid4()
_TICKER = "RELIANCE.NS"

_GOOD_BUDGET = UserBudget(
    user_id=_USER_ID,
    allocated_inr=Decimal("500000"),
)

_SAFETY_MOD = "backend.algo.live.safety"


def _signal(side: str = "BUY", qty: int = 10) -> Signal:
    return Signal(
        strategy_id=uuid4(),
        user_id=_USER_ID,
        ticker=_TICKER,
        side=side,
        qty=qty,
        emitted_at_ns=0,
    )


def _account() -> AccountState:
    return AccountState(
        user_id=_USER_ID,
        day_date=date(2026, 6, 24),
        initial_capital_inr=Decimal("500000"),
        current_equity_inr=Decimal("500000"),
        daily_realised_pnl_inr=Decimal("0"),
        daily_unrealised_pnl_inr=Decimal("0"),
    )


def _caps() -> dict:
    return {
        "live_orders_enabled": True,
        "allowed_tickers": [_TICKER],
        "max_inr": Decimal("0"),
        "max_orders_per_day": 0,
    }


def _good_patches() -> tuple:
    """Return a stack of patches representing healthy budget I/O."""
    return (
        patch(
            f"{_SAFETY_MOD}.load_user_budget",
            new=AsyncMock(return_value=_GOOD_BUDGET),
        ),
        patch(
            f"{_SAFETY_MOD}.sum_open_position_cost",
            new=AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            f"{_SAFETY_MOD}.sum_active_reservations",
            new=AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            f"{_SAFETY_MOD}.fetch_kite_available_cash",
            new=AsyncMock(return_value=Decimal("500000")),
        ),
    )


async def _check(**kwargs) -> object:
    defaults = dict(
        signal=_signal(),
        caps=_caps(),
        day_state={
            "cumulative_inr_today": Decimal("0"),
            "orders_count_today": 0,
        },
        account=_account(),
        strategy_risk={},
        last_price=Decimal("2800"),
        user_id=_USER_ID,
    )
    defaults.update(kwargs)
    return await pre_trade_check(**defaults)


# ---------------------------------------------------------------
# Fail-closed tests
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_user_budget_raises_returns_budget_cap_reject():
    """load_user_budget raising must NOT propagate — must
    return a LIVE_BUDGET_CAP reject (fail closed)."""
    with (
        patch(
            f"{_SAFETY_MOD}.load_user_budget",
            new=AsyncMock(
                side_effect=RuntimeError("DB connection lost")
            ),
        ),
        patch(
            f"{_SAFETY_MOD}.sum_open_position_cost",
            new=AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            f"{_SAFETY_MOD}.sum_active_reservations",
            new=AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            f"{_SAFETY_MOD}.fetch_kite_available_cash",
            new=AsyncMock(return_value=Decimal("500000")),
        ),
    ):
        decision = await _check()

    assert decision.outcome == "reject", (
        "Expected 'reject' but got {!r}".format(decision.outcome)
    )
    assert decision.reason == RejectReason.LIVE_BUDGET_CAP, (
        "Expected LIVE_BUDGET_CAP but got {!r}".format(decision.reason)
    )


@pytest.mark.asyncio
async def test_sum_open_position_cost_raises_returns_budget_cap():
    """sum_open_position_cost raising → LIVE_BUDGET_CAP reject."""
    with (
        patch(
            f"{_SAFETY_MOD}.load_user_budget",
            new=AsyncMock(return_value=_GOOD_BUDGET),
        ),
        patch(
            f"{_SAFETY_MOD}.sum_open_position_cost",
            new=AsyncMock(
                side_effect=IOError("Iceberg read failed")
            ),
        ),
        patch(
            f"{_SAFETY_MOD}.sum_active_reservations",
            new=AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            f"{_SAFETY_MOD}.fetch_kite_available_cash",
            new=AsyncMock(return_value=Decimal("500000")),
        ),
    ):
        decision = await _check()

    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.LIVE_BUDGET_CAP


@pytest.mark.asyncio
async def test_sum_active_reservations_raises_returns_budget_cap():
    """sum_active_reservations raising → LIVE_BUDGET_CAP reject."""
    with (
        patch(
            f"{_SAFETY_MOD}.load_user_budget",
            new=AsyncMock(return_value=_GOOD_BUDGET),
        ),
        patch(
            f"{_SAFETY_MOD}.sum_open_position_cost",
            new=AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            f"{_SAFETY_MOD}.sum_active_reservations",
            new=AsyncMock(
                side_effect=ValueError("Unexpected None")
            ),
        ),
        patch(
            f"{_SAFETY_MOD}.fetch_kite_available_cash",
            new=AsyncMock(return_value=Decimal("500000")),
        ),
    ):
        decision = await _check()

    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.LIVE_BUDGET_CAP


@pytest.mark.asyncio
async def test_fetch_kite_available_cash_raises_returns_budget_cap():
    """fetch_kite_available_cash raising (non-dry-run) →
    LIVE_BUDGET_CAP reject, not a propagated exception."""
    with (
        patch(
            f"{_SAFETY_MOD}.load_user_budget",
            new=AsyncMock(return_value=_GOOD_BUDGET),
        ),
        patch(
            f"{_SAFETY_MOD}.sum_open_position_cost",
            new=AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            f"{_SAFETY_MOD}.sum_active_reservations",
            new=AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            f"{_SAFETY_MOD}.fetch_kite_available_cash",
            new=AsyncMock(
                side_effect=ConnectionError("Kite API unreachable")
            ),
        ),
    ):
        decision = await _check(dry_run=False)

    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.LIVE_BUDGET_CAP


# ---------------------------------------------------------------
# Happy-path control
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_all_io_succeeds_accepts():
    """Control: when all budget I/O succeeds and headroom is
    sufficient, pre_trade_check returns accept."""
    load_p, open_p, res_p, kite_p = _good_patches()
    with load_p, open_p, res_p, kite_p:
        # qty=10 @ 2800 = 28000; headroom = 500000 → accept
        decision = await _check()

    assert decision.outcome == "accept", (
        "Expected 'accept' on healthy I/O but got {!r}".format(
            decision.outcome
        )
    )
