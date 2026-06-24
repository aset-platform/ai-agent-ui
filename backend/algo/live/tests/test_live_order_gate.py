"""Critical C4 — live order path is gated on the ATOMIC reserve.

Task 2.2 makes ``reserve_if_headroom`` (not the cached safety.py
Cap-0 pre-check) the authoritative gate for a LIVE BUY inside
``LiveRuntime._submit_order``:

  * a BUY whose atomic reserve returns ``None`` ABORTS — no
    ``place_order`` call, a ``signal_rejected`` /
    ``insufficient_balance`` event is emitted, return 0;
  * two BUYs in one tick whose combined cost exceeds the remaining
    allocation → the SECOND is rejected (the first's PENDING
    reservation shrinks headroom inside the atomic primitive);
  * SELL is NEVER gated (frees capital) — keeps the audit reserve;
  * dry-run BUY does NOT consume real budget — keeps the audit
    reserve, gate skipped.

Kite + budget I/O are mocked; no SDK calls leak out.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason=(
        "Requires pyarrow + Python ≥3.10 "
        "(run inside Docker backend container)"
    ),
)


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "live-order-gate test strategy",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close",
            "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 5},
        "root": {
            "type": "if",
            "cond": {
                "type": "compare",
                "op": "<=",
                "left": {"feature": "rsi_2"},
                "right": {"literal": 5},
            },
            "then": {"type": "set_target_weight", "weight": 0.2},
            "else": {"type": "hold"},
        },
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 1000},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


def _make_runtime(*, dry_run: bool = False):
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(_strategy_payload())
    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "allowed_tickers": ["ITC.NS"],
    }
    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False
    kite = MagicMock()
    kite.dry_run = dry_run

    with patch(
        "backend.algo.live.position_hydration.hydrate",
        return_value=[],
    ):
        return LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("1000000"),
            fee_as_of=date(2026, 4, 1),
            kite=kite,
            caps={
                "live_orders_enabled": True,
                "allowed_tickers": [],
            },
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )


def _buy(runtime, *, qty: int):
    from backend.algo.paper.types import Signal

    return Signal(
        strategy_id=runtime._strategy.id,
        user_id=runtime._user_id,
        ticker="ITC.NS",
        side="BUY",
        qty=qty,
        emitted_at_ns=time.time_ns(),
        reason="set_target_weight",
    )


def _budget_with_alloc(alloc: Decimal):
    from backend.algo.live.budget_types import UserBudget

    async def _load(_uid):
        return UserBudget(user_id=uuid4(), allocated_inr=alloc)

    return _load


@pytest.mark.asyncio
async def test_live_buy_rejected_when_atomic_reserve_returns_none():
    """ERROR PATH — atomic reserve None → abort, no place_order,
    one ``signal_rejected``/``insufficient_balance`` event."""
    runtime = _make_runtime()
    signal = _buy(runtime, qty=100)
    runtime._kite._get_redis.return_value = MagicMock()

    async def _no_headroom(**_kw):
        return None

    with patch(
        "backend.algo.live.runtime.budget_load_user",
        new=_budget_with_alloc(Decimal("1000")),
    ), patch(
        "backend.algo.live.runtime.budget_reserve_if_headroom",
        new=_no_headroom,
    ), patch(
        "backend.algo.live.runtime.get_tick_size",
        return_value=Decimal("0.05"),
    ):
        result = await runtime._submit_order(
            signal=signal,
            last_price=Decimal("300"),
            last_price_ts=None,
            daily_cap_remaining=10,
        )

    assert result == 0
    runtime._kite.place_order.assert_not_called()
    rejected = [
        e for e in runtime._events if e["type"] == "signal_rejected"
    ]
    assert len(rejected) == 1
    payload = json.loads(rejected[0]["payload_json"])
    assert payload["reason"] == "insufficient_balance"
    assert payload["ticker"] == "ITC.NS"


@pytest.mark.asyncio
async def test_two_buys_one_tick_second_rejected():
    """Critical C4 — two BUYs in one tick whose combined cost
    exceeds the allocation: the first reserves, the second is
    rejected by the atomic gate (the first's PENDING reservation
    consumed the headroom). place_order fires exactly once."""
    runtime = _make_runtime()
    runtime._kite._get_redis.return_value = MagicMock()
    runtime._kite.place_order.return_value = "OID-1"

    # allocated ₹150k; each BUY costs ₹100k → only one fits.
    first_rid = uuid4()
    calls = {"n": 0}

    async def _reserve(**_kw):
        calls["n"] += 1
        return first_rid if calls["n"] == 1 else None

    async def _fake_transition(**_kw):
        return None

    with patch(
        "backend.algo.live.runtime.budget_load_user",
        new=_budget_with_alloc(Decimal("150000")),
    ), patch(
        "backend.algo.live.runtime.budget_reserve_if_headroom",
        new=_reserve,
    ), patch(
        "backend.algo.live.runtime.budget_transition",
        new=_fake_transition,
    ), patch(
        "backend.algo.live.runtime.get_tick_size",
        return_value=Decimal("0.05"),
    ):
        r1 = await runtime._submit_order(
            signal=_buy(runtime, qty=1000),
            last_price=Decimal("100"),
            last_price_ts=None,
            daily_cap_remaining=10,
        )
        r2 = await runtime._submit_order(
            signal=_buy(runtime, qty=1000),
            last_price=Decimal("100"),
            last_price_ts=None,
            daily_cap_remaining=10,
        )

    assert r1 == 1
    assert r2 == 0
    assert runtime._kite.place_order.call_count == 1
    rejected = [
        e for e in runtime._events if e["type"] == "signal_rejected"
    ]
    assert len(rejected) == 1
    assert (
        json.loads(rejected[0]["payload_json"])["reason"]
        == "insufficient_balance"
    )


@pytest.mark.asyncio
async def test_sell_not_gated_uses_audit_reserve():
    """SELL frees capital → never routed through the atomic gate;
    keeps the plain audit reservation and places the order."""
    from backend.algo.paper.types import Signal

    runtime = _make_runtime()
    runtime._kite._get_redis.return_value = MagicMock()
    runtime._kite.place_order.return_value = "OID-SELL"
    sell = Signal(
        strategy_id=runtime._strategy.id,
        user_id=runtime._user_id,
        ticker="ITC.NS",
        side="SELL",
        qty=10,
        emitted_at_ns=time.time_ns(),
        reason="stop_loss",
    )

    gate_calls = {"n": 0}
    audit_calls = {"n": 0}

    async def _gate(**_kw):
        gate_calls["n"] += 1
        return None

    async def _audit(**_kw):
        audit_calls["n"] += 1
        return uuid4()

    async def _fake_transition(**_kw):
        return None

    with patch(
        "backend.algo.live.runtime.budget_reserve_if_headroom",
        new=_gate,
    ), patch(
        "backend.algo.live.runtime.budget_reserve",
        new=_audit,
    ), patch(
        "backend.algo.live.runtime.budget_transition",
        new=_fake_transition,
    ), patch(
        "backend.algo.live.runtime.get_tick_size",
        return_value=Decimal("0.05"),
    ):
        result = await runtime._submit_order(
            signal=sell,
            last_price=Decimal("300"),
            last_price_ts=None,
            daily_cap_remaining=10,
        )

    assert result == 1
    assert gate_calls["n"] == 0
    assert audit_calls["n"] == 1
    runtime._kite.place_order.assert_called_once()


@pytest.mark.asyncio
async def test_dry_run_buy_not_gated():
    """Dry-run BUY is a rehearsal → must NOT consume real budget;
    the atomic gate is skipped and the audit reserve is used."""
    runtime = _make_runtime(dry_run=True)
    runtime._kite._get_redis.return_value = MagicMock()
    runtime._kite.place_order.return_value = "OID-DRY"

    gate_calls = {"n": 0}
    audit_calls = {"n": 0}

    async def _gate(**_kw):
        gate_calls["n"] += 1
        return None

    async def _audit(**_kw):
        audit_calls["n"] += 1
        return uuid4()

    async def _fake_transition(**_kw):
        return None

    with patch(
        "backend.algo.live.runtime.budget_reserve_if_headroom",
        new=_gate,
    ), patch(
        "backend.algo.live.runtime.budget_reserve",
        new=_audit,
    ), patch(
        "backend.algo.live.runtime.budget_transition",
        new=_fake_transition,
    ), patch(
        "backend.algo.live.runtime.get_tick_size",
        return_value=Decimal("0.05"),
    ):
        result = await runtime._submit_order(
            signal=_buy(runtime, qty=10),
            last_price=Decimal("300"),
            last_price_ts=None,
            daily_cap_remaining=10,
        )

    assert result == 1
    assert gate_calls["n"] == 0
    assert audit_calls["n"] == 1
    runtime._kite.place_order.assert_called_once()
