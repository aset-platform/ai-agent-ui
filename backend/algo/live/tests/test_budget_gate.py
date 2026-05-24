"""Tests for Cap 0 budget check in pre_trade_check."""

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


def _signal(side: str = "BUY", qty: int = 100) -> Signal:
    return Signal(
        strategy_id=uuid4(),
        user_id=uuid4(),
        ticker="INFY.NS",
        side=side,
        qty=qty,
        emitted_at_ns=0,
    )


def _account() -> AccountState:
    return AccountState(
        user_id=uuid4(),
        day_date=date(2026, 5, 24),
        initial_capital_inr=Decimal("100000"),
        current_equity_inr=Decimal("100000"),
        daily_realised_pnl_inr=Decimal("0"),
        daily_unrealised_pnl_inr=Decimal("0"),
    )


def _caps() -> dict:
    return {
        "live_orders_enabled": True,
        "allowed_tickers": ["INFY.NS"],
        "max_inr": Decimal("0"),
        "max_orders_per_day": 0,
    }


@pytest.mark.asyncio
async def test_cap0_approves_under_headroom():
    """allocated 100k, open 20k, reserved 5k → headroom 75k.
    Order 70 * 1000 = 70k → ACCEPT."""
    with (
        patch(
            "backend.algo.live.safety.load_user_budget",
            AsyncMock(
                return_value=UserBudget(
                    user_id=uuid4(),
                    allocated_inr=Decimal("100000"),
                )
            ),
        ),
        patch(
            "backend.algo.live.safety.sum_open_position_cost",
            AsyncMock(return_value=Decimal("20000")),
        ),
        patch(
            "backend.algo.live.safety.sum_active_reservations",
            AsyncMock(return_value=Decimal("5000")),
        ),
        patch(
            "backend.algo.live.safety.fetch_kite_available_cash",
            AsyncMock(return_value=Decimal("200000")),
        ),
    ):
        decision = await pre_trade_check(
            signal=_signal(qty=70),
            caps=_caps(),
            day_state={
                "cumulative_inr_today": Decimal("0"),
                "orders_count_today": 0,
            },
            account=_account(),
            strategy_risk={},
            last_price=Decimal("1000"),
            user_id=uuid4(),
        )
    assert decision.outcome == "accept"


@pytest.mark.asyncio
async def test_cap0_rejects_when_internal_exhausted():
    """allocated 100k, open 80k, reserved 15k → headroom 5k.
    Order 10 * 1000 = 10k → REJECT(LIVE_BUDGET_CAP)."""
    with (
        patch(
            "backend.algo.live.safety.load_user_budget",
            AsyncMock(
                return_value=UserBudget(
                    user_id=uuid4(),
                    allocated_inr=Decimal("100000"),
                )
            ),
        ),
        patch(
            "backend.algo.live.safety.sum_open_position_cost",
            AsyncMock(return_value=Decimal("80000")),
        ),
        patch(
            "backend.algo.live.safety.sum_active_reservations",
            AsyncMock(return_value=Decimal("15000")),
        ),
        patch(
            "backend.algo.live.safety.fetch_kite_available_cash",
            AsyncMock(return_value=Decimal("200000")),
        ),
    ):
        decision = await pre_trade_check(
            signal=_signal(qty=10),
            caps=_caps(),
            day_state={
                "cumulative_inr_today": Decimal("0"),
                "orders_count_today": 0,
            },
            account=_account(),
            strategy_risk={},
            last_price=Decimal("1000"),
            user_id=uuid4(),
        )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.LIVE_BUDGET_CAP
    assert decision.threshold == Decimal("5000")
    assert decision.observed_value == Decimal("10000")


@pytest.mark.asyncio
async def test_cap0_rejects_when_kite_exhausted():
    """internal 50k headroom, kite 8k available, order 10k →
    REJECT (kite is binding)."""
    with (
        patch(
            "backend.algo.live.safety.load_user_budget",
            AsyncMock(
                return_value=UserBudget(
                    user_id=uuid4(),
                    allocated_inr=Decimal("100000"),
                )
            ),
        ),
        patch(
            "backend.algo.live.safety.sum_open_position_cost",
            AsyncMock(return_value=Decimal("50000")),
        ),
        patch(
            "backend.algo.live.safety.sum_active_reservations",
            AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            "backend.algo.live.safety.fetch_kite_available_cash",
            AsyncMock(return_value=Decimal("8000")),
        ),
    ):
        decision = await pre_trade_check(
            signal=_signal(qty=10),
            caps=_caps(),
            day_state={
                "cumulative_inr_today": Decimal("0"),
                "orders_count_today": 0,
            },
            account=_account(),
            strategy_risk={},
            last_price=Decimal("1000"),
            user_id=uuid4(),
        )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.LIVE_BUDGET_CAP
    assert decision.threshold == Decimal("8000")


@pytest.mark.asyncio
async def test_cap0_sell_bypasses_gate():
    """SELL with allocated=0 — Cap 0 must NOT reject as
    LIVE_BUDGET_CAP. (Another cap may reject; that's fine.)"""
    with patch(
        "backend.algo.live.safety.load_user_budget",
        AsyncMock(
            return_value=UserBudget(
                user_id=uuid4(),
                allocated_inr=Decimal("0"),
            )
        ),
    ):
        decision = await pre_trade_check(
            signal=_signal(side="SELL", qty=100),
            caps=_caps(),
            day_state={
                "cumulative_inr_today": Decimal("0"),
                "orders_count_today": 0,
            },
            account=_account(),
            strategy_risk={},
            last_price=Decimal("1000"),
            user_id=uuid4(),
        )
    assert decision.reason != RejectReason.LIVE_BUDGET_CAP


@pytest.mark.asyncio
async def test_cap0_fail_open_when_kite_down():
    """Kite returns Decimal('inf') → headroom falls back to
    internal. Order under internal → ACCEPT."""
    with (
        patch(
            "backend.algo.live.safety.load_user_budget",
            AsyncMock(
                return_value=UserBudget(
                    user_id=uuid4(),
                    allocated_inr=Decimal("100000"),
                )
            ),
        ),
        patch(
            "backend.algo.live.safety.sum_open_position_cost",
            AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            "backend.algo.live.safety.sum_active_reservations",
            AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            "backend.algo.live.safety.fetch_kite_available_cash",
            AsyncMock(return_value=Decimal("inf")),
        ),
    ):
        decision = await pre_trade_check(
            signal=_signal(qty=10),
            caps=_caps(),
            day_state={
                "cumulative_inr_today": Decimal("0"),
                "orders_count_today": 0,
            },
            account=_account(),
            strategy_risk={},
            last_price=Decimal("1000"),
            user_id=uuid4(),
        )
    assert decision.outcome == "accept"


@pytest.mark.asyncio
async def test_cap0_blocks_when_allocation_zero():
    """allocated 0 → headroom 0 → BUY rejected."""
    with (
        patch(
            "backend.algo.live.safety.load_user_budget",
            AsyncMock(
                return_value=UserBudget(
                    user_id=uuid4(),
                    allocated_inr=Decimal("0"),
                )
            ),
        ),
        patch(
            "backend.algo.live.safety.sum_open_position_cost",
            AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            "backend.algo.live.safety.sum_active_reservations",
            AsyncMock(return_value=Decimal("0")),
        ),
        patch(
            "backend.algo.live.safety.fetch_kite_available_cash",
            AsyncMock(return_value=Decimal("100000")),
        ),
    ):
        decision = await pre_trade_check(
            signal=_signal(qty=10),
            caps=_caps(),
            day_state={
                "cumulative_inr_today": Decimal("0"),
                "orders_count_today": 0,
            },
            account=_account(),
            strategy_risk={},
            last_price=Decimal("1000"),
            user_id=uuid4(),
        )
    assert decision.outcome == "reject"
    assert decision.reason == RejectReason.LIVE_BUDGET_CAP
