"""Task 4.0a — anti-churn guard on rebalance/entry orders.

The live runtime re-issued an identical ``set_target_weight`` SELL
every eval (~60s) while prior ones were still in-flight or had just
been cancelled by the order-timeout watcher → place→cancel→re-place
churn that became real fills. ``_submit_order`` must now suppress a
non-protective (rebalance/entry) order when either:

  * a NON-terminal in-flight entry already exists for the same
    (ticker, side) — ``suppress_kind="inflight"``; or
  * the (ticker, side) was placed within ``ALGO_ORDER_COOLDOWN_S`` —
    ``suppress_kind="cooldown"``.

THE load-bearing safety property: a PROTECTIVE exit (``stop_loss`` /
``time_stop`` / ``mis_auto_square_off`` / any reason containing
``exit``) must NEVER be suppressed — it always reaches place_order.

Kite + budget + all I/O deps are mocked; no SDK calls leak out.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from contextlib import ExitStack
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
        "name": "churn-guard test strategy",
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
        "allowed_tickers": ["KTKBANK.NS"],
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


def _make_signal(runtime, *, side="SELL", reason="set_target_weight"):
    from backend.algo.paper.types import Signal

    return Signal(
        strategy_id=runtime._strategy.id,
        user_id=runtime._user_id,
        ticker="KTKBANK.NS",
        side=side,
        qty=2,
        emitted_at_ns=time.time_ns(),
        reason=reason,
    )


def _inflight_entry(*, side="SELL", status="submitted"):
    return {
        "kite_order_id": "OID_PRIOR",
        "internal_order_id": str(uuid4()),
        "symbol": "KTKBANK",
        "side": side,
        "qty": 2,
        "submitted_at": "2026-06-25T09:16:00+05:30",
        "status": status,
        "reason": "set_target_weight",
        "product": "CNC",
        "reservation_id": str(uuid4()),
    }


def _patched(runtime, *, reservation_id):
    """Patch every budget/kite I/O dep so the place path runs clean."""
    async def _fake_reserve(**_kw):
        return reservation_id

    async def _fake_transition(**_kw):
        return None

    async def _fake_load_user(_uid):
        from backend.algo.live.budget_types import UserBudget

        return UserBudget(
            user_id=_uid,
            allocated_inr=Decimal("100000000"),
        )

    runtime._kite._get_redis.return_value = MagicMock()
    runtime._kite.place_order.return_value = "KITE_OID_NEW"

    return [
        patch(
            "backend.algo.live.runtime.budget_reserve_if_headroom",
            new=_fake_reserve,
        ),
        patch(
            "backend.algo.live.runtime.budget_reserve",
            new=_fake_reserve,
        ),
        patch(
            "backend.algo.live.runtime.budget_load_user",
            new=_fake_load_user,
        ),
        patch(
            "backend.algo.live.runtime.budget_transition",
            new=_fake_transition,
        ),
        patch(
            "backend.algo.live.runtime.get_tick_size",
            return_value=Decimal("0.05"),
        ),
    ]


async def _run_submit(runtime, signal, reservation_id):
    with ExitStack() as stack:
        for cm in _patched(runtime, reservation_id=reservation_id):
            stack.enter_context(cm)
        return await runtime._submit_order(
            signal=signal,
            last_price=Decimal("100.00"),
            last_price_ts=None,
            daily_cap_remaining=10,
        )


def _suppress_events(runtime):
    return [
        e for e in runtime._events
        if e["type"] == "order_suppressed_churn"
    ]


# ----------------------------------------------------------------
# 1. in-flight guard suppresses a rebalance SELL
# ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_inflight_suppresses_rebalance_sell():
    runtime = _make_runtime()
    runtime._in_flight.append(_inflight_entry(side="SELL"))
    signal = _make_signal(runtime, side="SELL")

    result = await _run_submit(runtime, signal, uuid4())

    runtime._kite.place_order.assert_not_called()
    assert result == 0
    ev = _suppress_events(runtime)
    assert len(ev) == 1
    payload = json.loads(ev[0]["payload_json"])
    assert payload["suppress_kind"] == "inflight"
    assert payload["ticker"] == "KTKBANK.NS"
    assert payload["side"] == "SELL"


# A terminal in-flight entry must NOT trigger the in-flight guard.
@pytest.mark.asyncio
async def test_terminal_inflight_does_not_suppress():
    runtime = _make_runtime()
    runtime._in_flight.append(
        _inflight_entry(side="SELL", status="cancelled")
    )
    signal = _make_signal(runtime, side="SELL")

    result = await _run_submit(runtime, signal, uuid4())

    runtime._kite.place_order.assert_called_once()
    assert result == 1
    assert _suppress_events(runtime) == []


# ----------------------------------------------------------------
# 2. cooldown guard suppresses a rebalance SELL
# ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_cooldown_suppresses_rebalance_sell():
    runtime = _make_runtime()
    signal = _make_signal(runtime, side="SELL")
    # Last placement 10s ago — inside the default 300s cooldown.
    runtime._last_submit_ts[("KTKBANK.NS", "SELL")] = time.time() - 10

    result = await _run_submit(runtime, signal, uuid4())

    runtime._kite.place_order.assert_not_called()
    assert result == 0
    ev = _suppress_events(runtime)
    assert len(ev) == 1
    payload = json.loads(ev[0]["payload_json"])
    assert payload["suppress_kind"] == "cooldown"


# ----------------------------------------------------------------
# 3. SAFETY — protective exits NEVER suppressed (most important)
# ----------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["stop_loss", "time_stop", "mis_auto_square_off", "trail_exit"],
)
async def test_protective_exit_never_suppressed(reason):
    """Both an in-flight SELL AND an active cooldown exist for the
    ticker, yet a protective exit MUST reach place_order."""
    runtime = _make_runtime()
    runtime._in_flight.append(_inflight_entry(side="SELL"))
    runtime._last_submit_ts[("KTKBANK.NS", "SELL")] = time.time()
    signal = _make_signal(runtime, side="SELL", reason=reason)

    result = await _run_submit(runtime, signal, uuid4())

    runtime._kite.place_order.assert_called_once()
    assert result == 1
    assert _suppress_events(runtime) == []


# ----------------------------------------------------------------
# 4. clean path — no in-flight, outside cooldown → placed; ts updated
# ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_clean_path_places_and_records_ts():
    runtime = _make_runtime()
    signal = _make_signal(runtime, side="SELL")
    assert ("KTKBANK.NS", "SELL") not in runtime._last_submit_ts

    before = time.time()
    result = await _run_submit(runtime, signal, uuid4())

    runtime._kite.place_order.assert_called_once()
    assert result == 1
    assert _suppress_events(runtime) == []
    ts = runtime._last_submit_ts[("KTKBANK.NS", "SELL")]
    assert ts >= before


# ----------------------------------------------------------------
# 5. cooldown elapsed → next same-direction order is placed
# ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_cooldown_elapsed_allows_next_order():
    runtime = _make_runtime()
    signal = _make_signal(runtime, side="SELL")
    # Last placement well outside the 300s default cooldown.
    runtime._last_submit_ts[("KTKBANK.NS", "SELL")] = (
        time.time() - 10_000
    )

    result = await _run_submit(runtime, signal, uuid4())

    runtime._kite.place_order.assert_called_once()
    assert result == 1
    assert _suppress_events(runtime) == []
