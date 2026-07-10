"""Regression test — Piece A (``_ratchet_all_gtts``) MUST record a
cooldown-gate entry when a GTT-triggered exit closes a position, not
just the position tracker + event bookkeeping.

Found 2026-07-08: SUPRIYA.NS stopped out via GTT (phase 1 — hard
stop, a genuine thesis failure) at 13:15 IST, then re-entered the
SAME session at 13:16 IST on a fresh RSI(2) oversold signal — the
``cooldown_after_failed_exit_days=7`` gate configured on the
strategy never saw the exit because ``_ratchet_all_gtts`` (Piece A)
never appended to ``self._cooldown_history``; that only happened in
the separate direct ``stop_loss_pct`` check block. The re-entry then
stopped out again the next morning on a stale/blended hydrated
average price. ASETPLTFRM — see also ``test_ratchet_gtt_poll_emits_fill.py``
for the sibling order_filled_live regression this builds on.
"""
from __future__ import annotations

import importlib
import sys
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
    strategy.risk.per_trade.cooldown_after_failed_exit_days = 7
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
        "live_orders_enabled": True, "allowed_tickers": ["SUPRIYA.NS"],
    }

    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
        return_value={},
    ), patch(
        "backend.algo.live.runtime.load_recent_failed_exits",
        return_value=[],
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


def _trigger_gtt(rt, ticker: str, gtt_id: int, qty: int, price: float):
    from backend.algo.strategy.ast import RiskPerTrade
    from backend.algo.backtest.trailing_stop_manager import (
        TrailingStopManager,
    )

    mgr = TrailingStopManager(
        risk=RiskPerTrade(stop_loss_pct=5.0, max_qty=1000),
        entry_price=881.5,
        atr=15.0,
        ticker=ticker,
    )
    rt._trailing_managers[ticker] = mgr
    rt._gtt_ids[ticker] = gtt_id
    rt._ws_hwm[ticker] = 881.5
    rt._ticker_locked.add(ticker)
    rt._positions.open_positions = MagicMock(return_value={})

    rt._kite.get_gtts = MagicMock(return_value=[
        {
            "id": gtt_id,
            "status": "triggered",
            "orders": [
                {
                    "transaction_type": "SELL",
                    "quantity": qty,
                    "price": price,
                },
            ],
        },
    ])
    rt._kite._kc.orders = MagicMock(return_value=[])

    with patch.object(
        rt, "_sync_ticker_lock_to_redis",
    ), patch("backend.cache.get_cache"):
        rt._ratchet_all_gtts()


def test_phase1_gtt_trigger_appends_cooldown_entry():
    """A hard-stop (phase 1) GTT trigger is a thesis failure — the
    cooldown gate must see it immediately, in-process, without
    waiting for an algo.events round-trip through a restart."""
    rt = _make_runtime()
    ticker = "SUPRIYA.NS"

    assert rt._cooldown_history == []

    _trigger_gtt(rt, ticker, gtt_id=326299383, qty=3, price=846.24)

    matches = [
        c for c in rt._cooldown_history if c.ticker == ticker
    ]
    assert len(matches) == 1, (
        "Piece A must append a cooldown entry on every GTT trigger, "
        "not just the separate direct stop_loss_pct check path"
    )
    assert matches[0].exit_reason == "phase1_stop"


def test_phase1_gtt_trigger_blocks_same_day_reentry():
    """End-to-end: the strategy's own configured
    cooldown_after_failed_exit_days must now actually block a
    same-day BUY signal on a ticker that just hard-stopped via GTT
    -- reproducing (and fixing) the SUPRIYA.NS same-day
    stop-out -> re-entry -> stop-out sequence."""
    from datetime import date

    from backend.algo.backtest.cooldown_monitor import in_cooldown

    rt = _make_runtime()
    ticker = "SUPRIYA.NS"

    _trigger_gtt(rt, ticker, gtt_id=326299383, qty=3, price=846.24)

    assert in_cooldown(
        ticker=ticker,
        bar_date=date(2026, 7, 7),
        closed_positions=rt._cooldown_history,
        cooldown_days=7,
    ), (
        "same-day re-entry must be blocked by the existing "
        "cooldown_after_failed_exit_days gate once the GTT trigger "
        "is correctly tracked"
    )


def test_order_filled_live_payload_carries_phase():
    """Additive field -- downstream/hydration code needs the phase
    to distinguish a real thesis failure (1, 15) from a locked-in
    win (2, trail_stop) on restart. Must not remove/change any
    existing field (reason stays 'gtt_triggered' for backward
    compat with attribution/UI consumers)."""
    import json

    rt = _make_runtime()
    ticker = "SUPRIYA.NS"

    _trigger_gtt(rt, ticker, gtt_id=326299383, qty=3, price=846.24)

    fill_events = [
        e for e in rt._events if e["type"] == "order_filled_live"
    ]
    assert len(fill_events) == 1
    payload = json.loads(
        fill_events[0]["payload_json"],
    ) if "payload_json" in fill_events[0] else fill_events[0]["payload"]
    assert payload["reason"] == "gtt_triggered"
    assert payload["phase"] == 1


# ── Piece B (postback fallback, _apply_gtt_triggered_sell_fill) ──
# Same dual-path fix as ASETPLTFRM-466 / the GTT budget-release fix
# in test_gtt_exit_budget_release.py — "whichever path runs first
# wins" means BOTH must record the cooldown entry, not just Piece A.


def _make_runtime_async():
    from backend.algo.broker.kite_client import KiteClient
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.risk.per_trade.trailing_trigger_pct = 5.0
    strategy.risk.per_trade.trailing_atr_multiplier = 1.5
    strategy.risk.per_trade.stop_loss_pct = 5.0
    strategy.risk.per_trade.cooldown_after_failed_exit_days = 7
    strategy.root = MagicMock()
    strategy.root.model_dump.return_value = {"type": "hold"}
    strategy.schedule = MagicMock()
    strategy.schedule.interval = "1d"
    strategy.product = "CNC"

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {"live_orders_enabled": True}
    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

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
        "live_orders_enabled": True, "allowed_tickers": ["SUPRIYA.NS"],
    }

    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
        return_value={},
    ), patch(
        "backend.algo.live.runtime.load_recent_failed_exits",
        return_value=[],
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


class TestPieceBRecordsCooldownOnPostback:
    @pytest.mark.asyncio
    async def test_apply_gtt_triggered_sell_fill_appends_cooldown_entry(
        self,
    ):
        from backend.algo.strategy.ast import RiskPerTrade
        from backend.algo.backtest.trailing_stop_manager import (
            TrailingStopManager,
        )

        runtime = _make_runtime_async()
        ticker = "SUPRIYA.NS"
        runtime._trailing_managers[ticker] = TrailingStopManager(
            risk=RiskPerTrade(stop_loss_pct=5.0, max_qty=1000),
            entry_price=881.5,
            atr=15.0,
            ticker=ticker,
        )

        with patch(
            "backend.algo.live.runtime.budget_reserve",
            new_callable=AsyncMock,
            return_value=uuid4(),
        ), patch(
            "backend.algo.live.runtime.budget_transition",
            new_callable=AsyncMock,
        ):
            await runtime._apply_gtt_triggered_sell_fill(
                ticker=ticker, fill_price=846.5, qty=3,
            )

        matches = [
            c for c in runtime._cooldown_history if c.ticker == ticker
        ]
        assert len(matches) == 1, (
            "Piece B (postback fallback) must also append a cooldown "
            "entry — the dual-path GTT accounting is idempotent, "
            "whichever runs first wins, so both sides must record it"
        )
        assert matches[0].exit_reason == "phase1_stop"

    @pytest.mark.asyncio
    async def test_missing_trailing_manager_defaults_to_phase1_stop(
        self,
    ):
        """Fail-safe: if the trailing manager was already popped
        (e.g. Piece A won the race a split second earlier and this
        is a genuinely-idempotent second call), still record a
        cooldown-eligible entry rather than silently skipping it."""
        runtime = _make_runtime_async()
        ticker = "SUPRIYA.NS"
        assert ticker not in runtime._trailing_managers

        with patch(
            "backend.algo.live.runtime.budget_reserve",
            new_callable=AsyncMock,
            return_value=uuid4(),
        ), patch(
            "backend.algo.live.runtime.budget_transition",
            new_callable=AsyncMock,
        ):
            await runtime._apply_gtt_triggered_sell_fill(
                ticker=ticker, fill_price=846.5, qty=3,
            )

        matches = [
            c for c in runtime._cooldown_history if c.ticker == ticker
        ]
        assert len(matches) == 1
        assert matches[0].exit_reason == "phase1_stop"
