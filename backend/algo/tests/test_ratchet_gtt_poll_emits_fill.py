"""Regression test — Piece A (GTT-poll detection in
``_ratchet_all_gtts``) MUST emit an ``order_filled_live`` event
when it detects a GTT-triggered SELL, not just a ``gtt_triggered``
breadcrumb.

Found 2026-07-02: HSCL (RSI(2) Connors Daily v5 + 5% price stop)
was sold via GTT on 2026-07-01. Kite's postback never arrived (or
was lost), so Piece B never ran; Piece A caught it 15 minutes
later, closed the position in-memory, and emitted only
``gtt_triggered`` -- no durable fill event, so every downstream
consumer of algo.events (Attribution panel, Strategy Performance
page) silently missed the trade. Separately, the in-memory
position tracker had already lost the qty by the time Piece A
ran (drifted from _trailing_managers across a runtime restart),
so qty must come from Kite's own GTT order definition, not
``self._positions``, when the two disagree.
"""
from __future__ import annotations

import importlib
import json
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="Requires the backend Docker stack (pyarrow + py>=3.10)",
)


def _make_runtime():
    from backend.algo.broker.kite_client import KiteClient
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.risk.per_trade.trailing_trigger_pct = 5.0
    strategy.risk.per_trade.trailing_atr_multiplier = 1.5
    strategy.root = MagicMock()
    strategy.root.model_dump.return_value = {"type": "hold"}
    strategy.schedule = MagicMock()
    strategy.schedule.interval = "1d"
    strategy.product = "CNC"

    caps_repo = MagicMock()
    caps_repo.get = MagicMock(
        return_value={"live_orders_enabled": True},
    )
    kill_switch_repo = MagicMock()
    kill_switch_repo.is_active = MagicMock(return_value=False)

    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        kite = KiteClient(
            api_key="k", access_token="tok", dry_run=False,
        )
        kite._kc = kc_instance

    caps: dict = {
        "live_orders_enabled": True, "allowed_tickers": ["HSCL.NS"],
    }

    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
        return_value={},
    ):
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


def test_gtt_poll_trigger_emits_order_filled_live_with_kite_qty():
    """The reported bug: Piece A must emit order_filled_live, and
    must source qty from Kite's GTT order definition (not the
    in-memory position tracker) when the two disagree."""
    from backend.algo.strategy.ast import RiskPerTrade
    from backend.algo.backtest.trailing_stop_manager import (
        TrailingStopManager,
    )

    rt = _make_runtime()
    ticker = "HSCL.NS"

    mgr = TrailingStopManager(
        risk=RiskPerTrade(stop_loss_pct=5.0, max_qty=1000),
        entry_price=642.6,
        atr=15.0,
        ticker=ticker,
    )
    rt._trailing_managers[ticker] = mgr
    rt._gtt_ids[ticker] = 325479574
    rt._ws_hwm[ticker] = 693.0
    rt._ticker_locked.add(ticker)

    # Reproduces the exact bug scenario: the in-memory position
    # tracker has ALREADY lost this ticker (drifted after a
    # restart), even though _trailing_managers still has it.
    rt._positions.open_positions = MagicMock(return_value={})

    # Kite's GTT book: our gtt_id is no longer "active" (triggered)
    # -- and Kite's own order definition carries the real qty.
    rt._kite.get_gtts = MagicMock(return_value=[
        {
            "id": 325479574,
            "status": "triggered",
            "orders": [
                {
                    "transaction_type": "SELL",
                    "quantity": 4,
                    "price": 673.722,
                },
            ],
        },
    ])

    with patch.object(
        rt, "_sync_ticker_lock_to_redis",
    ), patch("backend.cache.get_cache"):
        rt._ratchet_all_gtts()

    fill_events = [
        e for e in rt._events if e["type"] == "order_filled_live"
    ]
    assert len(fill_events) == 1, (
        "Piece A must emit order_filled_live, not just "
        "gtt_triggered -- otherwise every downstream consumer of "
        "algo.events (Attribution panel, Strategy Performance "
        "page) silently misses the trade."
    )
    payload = json.loads(
        fill_events[0]["payload_json"],
    ) if "payload_json" in fill_events[0] else fill_events[0]["payload"]
    assert payload["symbol"] == "HSCL"
    assert payload["side"] == "SELL"
    assert payload["qty"] == 4, (
        "qty must come from Kite's GTT order definition when the "
        "in-memory position tracker has lost the ticker -- not "
        "silently default to 0."
    )
    assert float(payload["price"]) == pytest.approx(673.722)
    assert payload["reason"] == "gtt_triggered"

    triggered_events = [
        e for e in rt._events if e["type"] == "gtt_triggered"
    ]
    assert len(triggered_events) == 1
    assert ticker not in rt._trailing_managers
    assert ticker not in rt._gtt_ids
