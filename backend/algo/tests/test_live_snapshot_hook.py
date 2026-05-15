"""FE-5 hook test: live runtime writes one
``stocks.trade_feature_snapshots`` row per executed fill.

Live fills (real Kite postback + dry-run synthetic fills)
both flow through ``_synthetic_fill``; the hook fires once
per fill with ``mode='live'``. Uses the dry-run path
(no real Kite SDK call) to keep the test minimal.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.broker.kite_client import KiteClient

_RUNTIME_AVAILABLE = importlib.util.find_spec(
    "pyarrow"
) is not None and sys.version_info >= (3, 10)


def _make_runtime():
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.product = "CNC"
    strategy.schedule = MagicMock()
    strategy.schedule.interval = "1d"
    strategy.entry_cutoff_time = None

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "max_inr": Decimal("100000"),
        "max_orders_per_day": 10,
        "allowed_tickers": ["RELIANCE.NS"],
        "cumulative_inr_today": Decimal("0"),
        "orders_count_today": 0,
    }
    caps_repo.update_in_flight = AsyncMock()
    caps_repo.increment_daily_counters = AsyncMock()

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        kite = KiteClient(
            api_key="k",
            access_token="tok",
            dry_run=True,
        )
        kite._kc = kc_instance

    caps = {"live_orders_enabled": True}
    runtime = LiveRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("500000"),
        fee_as_of=None,
        kite=kite,
        caps=caps,
        run_id=uuid4(),
        caps_repo=caps_repo,
        kill_switch_repo=kill_switch_repo,
    )
    return runtime


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="LiveRuntime requires pyarrow + py3.10+",
)
async def test_live_runtime_calls_snapshot_on_fill() -> None:
    """One synthetic fill → one snapshot write with
    ``mode='live'`` + the right symbol-as-ticker."""
    from backend.algo.paper.types import Signal

    runtime = _make_runtime()
    runtime._DRY_FILL_DELAY_S = 0.02

    signal = Signal(
        strategy_id=runtime._strategy.id,
        user_id=runtime._user_id,
        ticker="RELIANCE.NS",
        side="BUY",
        qty=5,
        emitted_at_ns=1_000_000_000,
    )

    with (
        patch(
            "backend.algo.live.runtime.flush_events",
        ),
        patch(
            "backend.algo.features.snapshots." "write_trade_feature_snapshot",
        ) as snap_mock,
    ):
        await runtime._submit_order(
            signal=signal,
            last_price=Decimal("2500"),
        )
        # The synthetic fill runs in a background task; wait
        # for the snapshot hook to be observed instead of
        # polling runtime._events (which the in-session
        # flush clears).
        for _ in range(100):
            await asyncio.sleep(0.01)
            if snap_mock.call_count >= 1:
                break

    assert snap_mock.call_count == 1
    kw = snap_mock.call_args.kwargs
    assert kw["mode"] == "live"
    assert kw["ticker"] == "RELIANCE.NS"
    assert kw["side"] == "BUY"
    assert kw["qty"] == 5
    # Live fills carry no in-scope features at fill time
    # (the decision-time features rode the prior
    # signal_generated event). Empty/None features → "{}".
    assert kw["features"] is None or kw["features"] == {}


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="LiveRuntime requires pyarrow + py3.10+",
)
async def test_live_snapshot_failure_does_not_break_fill() -> None:
    """Writer raise MUST NOT propagate — the
    ``order_filled_live`` event remains emitted + the
    in-flight entry still flips to ``filled``."""
    from backend.algo.paper.types import Signal

    runtime = _make_runtime()
    runtime._DRY_FILL_DELAY_S = 0.02

    signal = Signal(
        strategy_id=runtime._strategy.id,
        user_id=runtime._user_id,
        ticker="RELIANCE.NS",
        side="BUY",
        qty=2,
        emitted_at_ns=1_000_000_000,
    )

    flushed: list[list[dict]] = []

    def _capture_flush(events_list: list[dict]) -> None:
        flushed.append(list(events_list))

    with (
        patch(
            "backend.algo.live.runtime.flush_events",
            side_effect=_capture_flush,
        ),
        patch(
            "backend.algo.features.snapshots." "write_trade_feature_snapshot",
            side_effect=RuntimeError("simulated outage"),
        ) as snap_mock,
    ):
        await runtime._submit_order(
            signal=signal,
            last_price=Decimal("2500"),
        )
        for _ in range(100):
            await asyncio.sleep(0.01)
            if snap_mock.call_count >= 1:
                break

    # Hook fired despite the writer raising — contract:
    # snapshot failure NEVER blocks the fill or the
    # order_filled_live event.
    assert snap_mock.call_count == 1
    all_events = [e for batch in flushed for e in batch]
    fills = [e for e in all_events if e["type"] == "order_filled_live"]
    assert len(fills) == 1
