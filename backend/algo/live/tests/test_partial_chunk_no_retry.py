"""Critical C1 — runtime does NOT blind-retry a partial-chunk fail.

When ``KiteClient.place_order`` raises ``PartialChunkPlacementError``
(some freeze-split chunks already live on the exchange, a later chunk
failed), ``LiveRuntime._submit_order`` must:
  * NOT call ``place_order`` a second time (no full-qty re-submit →
    no doubled real exposure);
  * record each already-live order id into ``_in_flight`` so the
    postback / order-timeout reconciler tracks them;
  * transition the budget reservation to ``PARTIAL`` (active, non-
    terminal) so its capital stays held for reconciliation.

Kite + budget + all I/O deps are mocked; no SDK calls leak out.
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
        "name": "partial-chunk no-retry test strategy",
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
        "rebalance": {"type": "daily", "max_positions": 1},
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
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


def _make_runtime():
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
    kite.dry_run = False

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


def _make_signal(runtime):
    from backend.algo.paper.types import Signal

    return Signal(
        strategy_id=runtime._strategy.id,
        user_id=runtime._user_id,
        ticker="ITC.NS",
        side="BUY",
        qty=3500,
        emitted_at_ns=time.time_ns(),
        reason="set_target_weight",
    )


@pytest.mark.asyncio
async def test_partial_chunk_failure_no_blind_retry():
    """Chunks OID0/OID1 live, later chunk failed → runtime records
    both ids, sets reservation PARTIAL, calls place_order ONCE."""
    from backend.algo.broker.exceptions import (
        PartialChunkPlacementError,
    )
    from backend.algo.live.budget_types import ReservationState

    runtime = _make_runtime()
    signal = _make_signal(runtime)
    reservation_id = uuid4()

    place_order_calls = {"n": 0}

    def _raise_partial(*_a, **_kw):
        place_order_calls["n"] += 1
        raise PartialChunkPlacementError(
            ["OID0", "OID1"],
            2,
            RuntimeError("kite rejected chunk 2"),
        )

    runtime._kite.place_order.side_effect = _raise_partial
    runtime._kite._get_redis.return_value = MagicMock()

    transitions: list = []

    async def _fake_reserve(**_kw):
        return reservation_id

    async def _fake_transition(**kw):
        transitions.append(kw)

    async def _fake_load_user(_uid):
        from backend.algo.live.budget_types import UserBudget

        return UserBudget(
            user_id=_uid,
            allocated_inr=Decimal("100000000"),
        )

    # Task 2.2 — a LIVE BUY now routes through the atomic gate
    # (reserve_if_headroom + budget_load_user), not the plain audit
    # reserve. Patch both so the partial-chunk path under test runs.
    with patch(
        "backend.algo.live.runtime.budget_reserve_if_headroom",
        new=_fake_reserve,
    ), patch(
        "backend.algo.live.runtime.budget_load_user",
        new=_fake_load_user,
    ), patch(
        "backend.algo.live.runtime.budget_transition",
        new=_fake_transition,
    ), patch(
        "backend.algo.live.runtime.get_tick_size",
        return_value=Decimal("0.05"),
    ):
        result = await runtime._submit_order(
            signal=signal,
            last_price=Decimal("307.30"),
            last_price_ts=None,
            daily_cap_remaining=10,
        )

    # place_order called EXACTLY once — no full-qty blind retry.
    assert place_order_calls["n"] == 1
    # Both live chunk ids recorded in _in_flight.
    oids = [e["kite_order_id"] for e in runtime._in_flight]
    assert oids == ["OID0", "OID1"]
    for e in runtime._in_flight:
        assert e["status"] == "submitted"
        assert e["reservation_id"] == str(reservation_id)
    # Reservation transitioned to PARTIAL (active, not terminal).
    assert len(transitions) == 1
    assert transitions[0]["new_state"] == ReservationState.PARTIAL
    # Return = count of live chunks (>0) — not a clean 1, not 0.
    assert result == 2
    # Partial-failure event emitted.
    partial_events = [
        e for e in runtime._events
        if e["type"] == "order_partial_chunk_failure"
    ]
    assert len(partial_events) == 1
    payload = json.loads(partial_events[0]["payload_json"])
    assert payload["placed_order_ids"] == ["OID0", "OID1"]
    assert payload["failed_chunk"] == 2
