"""PR3 — the periodic event-flush task drains the buffer on a fixed
cadence and stops cleanly on cancel."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
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
    reason="Requires pyarrow + Python >=3.10 (Docker backend container)",
)


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "pr3 flush test strategy",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d", "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "hold"},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80, "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


def _make_runtime():
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {"live_orders_enabled": True}
    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False
    with patch(
        "backend.algo.live.position_hydration.hydrate", return_value=[]
    ):
        return LiveRuntime(
            strategy=parse_strategy(_strategy_payload()),
            user_id=uuid4(),
            initial_capital_inr=Decimal("3000"),
            fee_as_of=date(2026, 4, 1),
            kite=MagicMock(dry_run=True),
            caps={"live_orders_enabled": True, "allowed_tickers": []},
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )


@pytest.mark.asyncio
async def test_periodic_flush_drains_then_stops(monkeypatch):
    import backend.algo.live.runtime as rt

    runtime = _make_runtime()
    calls = []

    async def _fake_flush():
        calls.append(1)

    monkeypatch.setattr(runtime, "_flush_events_now", _fake_flush)
    monkeypatch.setattr(rt, "_EVENT_FLUSH_INTERVAL_S", 0.01)

    task = asyncio.create_task(runtime._periodic_event_flush())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(calls) >= 2  # flushed repeatedly on the fast cadence
