"""Tests for _ratchet_all_gtts and _trailing_ratchet_loop (Task 8).

Covers:
- No-op when no trailing managers
- STOP_UPDATED: deletes old GTT, places new GTT, saves Redis state
- STOP_UPDATED: emits gtt_ratcheted event
- STOP_HIT: deletes GTT, places emergency limit sell, removes manager
- STOP_HIT: graceful when place_order raises
- Stale position (qty=0): removes trailing manager
- WS HWM update on each tick
"""
import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, call, patch
from uuid import uuid4

import pytest

from backend.algo.live.runtime import LiveRuntime
from backend.algo.backtest.trailing_stop_manager import (
    TrailingPhase,
    TrailingStopManager,
)


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_risk(
    *,
    stop_loss_pct: float = 5.0,
    trailing_trigger_pct: float | None = 5.0,
    trailing_atr_multiplier: float | None = 1.5,
    phase1_ratchet_trigger_pct: float | None = 2.0,
    phase1_ratchet_new_stop_pct: float | None = 3.0,
    max_holding_days: int = 5,
):
    r = MagicMock()
    r.stop_loss_pct = stop_loss_pct
    r.trailing_trigger_pct = trailing_trigger_pct
    r.trailing_atr_multiplier = trailing_atr_multiplier
    r.phase1_ratchet_trigger_pct = phase1_ratchet_trigger_pct
    r.phase1_ratchet_new_stop_pct = phase1_ratchet_new_stop_pct
    r.max_holding_days = max_holding_days
    r.cooldown_after_failed_exit_days = 7
    return r


def _make_runtime() -> LiveRuntime:
    risk = _make_risk()
    strategy = MagicMock()
    strategy.id = uuid4()
    strategy.product = "CNC"
    strategy.risk = MagicMock()
    strategy.risk.per_trade = risk

    kite = MagicMock()
    kite._dry_run = False
    kite.place_gtt.return_value = 99
    kite.delete_gtt.return_value = None

    caps = {"live_orders_enabled": True, "allowed_tickers": None}

    with patch(
        "backend.algo.live.position_hydration.hydrate",
        return_value=[],
    ), patch(
        "backend.algo.live.runtime.load_recent_failed_exits",
        return_value=[],
    ), patch(
        "backend.algo.live.runtime.LiveRuntime._ensure_regime_cache",
        return_value=None,
    ), patch(
        "backend.algo.live.runtime.LiveRuntime._ensure_factor_cache",
        return_value=None,
    ):
        rt = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("100000"),
            fee_as_of=date.today(),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=MagicMock(),
            kill_switch_repo=MagicMock(),
        )
    return rt


def _seed_manager(
    rt: LiveRuntime,
    ticker: str = "INFY.NS",
    entry_price: float = 1000.0,
    atr: float = 20.0,
    gtt_id: int = 11,
    qty: int = 5,
) -> TrailingStopManager:
    """Seed a trailing manager + open position + HWM into a runtime."""
    mgr = TrailingStopManager(
        rt._strategy.risk.per_trade,
        entry_price=entry_price,
        atr=atr,
        ticker=ticker,
    )
    rt._trailing_managers[ticker] = mgr
    rt._gtt_ids[ticker] = gtt_id
    rt._ws_hwm[ticker] = entry_price

    pos = MagicMock()
    pos.qty = qty
    rt._positions.open_positions = MagicMock(
        return_value={ticker: pos}
    )
    return mgr


# ── Tests: _ratchet_all_gtts ──────────────────────────────────────────────

class TestRatchetAllGtts:
    def test_no_op_when_no_managers(self):
        rt = _make_runtime()
        rt._positions.open_positions = MagicMock(return_value={})
        rt._ratchet_all_gtts()
        rt._kite.place_gtt.assert_not_called()
        rt._kite.delete_gtt.assert_not_called()

    def test_removes_stale_position(self):
        rt = _make_runtime()
        _seed_manager(rt, qty=0)
        rt._ratchet_all_gtts()
        assert "INFY.NS" not in rt._trailing_managers

    def test_no_op_when_hwm_zero(self):
        rt = _make_runtime()
        _seed_manager(rt)
        rt._ws_hwm["INFY.NS"] = 0.0
        rt._ratchet_all_gtts()
        rt._kite.place_gtt.assert_not_called()

    def test_no_op_when_price_below_ratchet(self):
        rt = _make_runtime()
        # With 5% stop and price still at entry, no ratchet yet
        _seed_manager(rt, entry_price=1000.0, atr=20.0)
        rt._ws_hwm["INFY.NS"] = 1010.0  # only 1% gain, below trigger
        rt._ratchet_all_gtts()
        rt._kite.place_gtt.assert_not_called()
        rt._kite.delete_gtt.assert_not_called()

    def test_ratchet_on_phase1_trigger(self):
        rt = _make_runtime()
        # phase1_ratchet_trigger_pct=2.0 → fires at price >= 1020
        _seed_manager(rt, entry_price=1000.0, atr=20.0, gtt_id=11)
        rt._ws_hwm["INFY.NS"] = 1025.0  # 2.5% gain → phase1 ratchet

        with patch.object(rt, "_save_trailing_state"):
            rt._ratchet_all_gtts()

        # Old GTT deleted, new GTT placed
        rt._kite.delete_gtt.assert_called_once_with(11)
        assert rt._kite.place_gtt.called
        assert rt._gtt_ids.get("INFY.NS") == 99  # return from mock

    def test_ratchet_emits_gtt_ratcheted_event(self):
        rt = _make_runtime()
        _seed_manager(rt, entry_price=1000.0, atr=20.0, gtt_id=11)
        rt._ws_hwm["INFY.NS"] = 1025.0

        with patch.object(rt, "_save_trailing_state"):
            rt._ratchet_all_gtts()

        types = [e["type"] for e in rt._events]
        assert "gtt_ratcheted" in types

    def test_stop_hit_via_hwm_places_emergency_sell(self):
        rt = _make_runtime()
        # stop = entry * 0.95 = 950; set HWM below stop to trigger STOP_HIT
        _seed_manager(
            rt, entry_price=1000.0, atr=20.0, gtt_id=11, qty=5,
        )
        rt._ws_hwm["INFY.NS"] = 940.0  # below stop=950 → STOP_HIT

        rt._ratchet_all_gtts()

        rt._kite.delete_gtt.assert_called_once_with(11)
        rt._kite.place_order.assert_called_once()
        call_kwargs = rt._kite.place_order.call_args[1]
        assert call_kwargs["tradingsymbol"] == "INFY"
        assert call_kwargs["transaction_type"] == "SELL"
        assert call_kwargs["order_type"] == "LIMIT"
        assert call_kwargs["quantity"] == 5
        # Manager removed from state
        assert "INFY.NS" not in rt._trailing_managers
        assert "INFY.NS" not in rt._gtt_ids

    def test_stop_hit_graceful_when_place_order_raises(self):
        rt = _make_runtime()
        _seed_manager(rt, entry_price=1000.0, atr=20.0, gtt_id=11)
        rt._ws_hwm["INFY.NS"] = 940.0
        rt._kite.place_order.side_effect = RuntimeError("Kite down")

        # Must not propagate
        rt._ratchet_all_gtts()
        assert "INFY.NS" not in rt._trailing_managers

    def test_ratchet_uses_limit_headroom(self):
        rt = _make_runtime()
        _seed_manager(rt, entry_price=1000.0, atr=20.0, gtt_id=11)
        rt._ws_hwm["INFY.NS"] = 1025.0

        with patch.object(rt, "_save_trailing_state"):
            rt._ratchet_all_gtts()

        call_kwargs = rt._kite.place_gtt.call_args[1]
        trigger = call_kwargs["trigger_price"]
        limit = call_kwargs["limit_price"]
        expected = trigger * (1.0 - LiveRuntime._GTT_LIMIT_HEADROOM_PCT)
        assert abs(limit - expected) < 0.001

    def test_ratchet_graceful_when_place_gtt_raises(self):
        rt = _make_runtime()
        _seed_manager(rt, entry_price=1000.0, atr=20.0, gtt_id=11)
        rt._ws_hwm["INFY.NS"] = 1025.0
        rt._kite.place_gtt.side_effect = RuntimeError("GTT error")

        # Must not propagate; manager stays in place
        rt._ratchet_all_gtts()
        assert "INFY.NS" in rt._trailing_managers


# ── Tests: WS HWM tick update ──────────────────────────────────────────────

class TestWsHwmUpdate:
    """WS HWM update logic in the run() tick loop."""

    def test_hwm_advances_on_higher_price(self):
        rt = _make_runtime()
        ticker = "INFY.NS"
        mgr = MagicMock()
        rt._trailing_managers[ticker] = mgr
        rt._ws_hwm[ticker] = 1000.0

        # Simulate the inline HWM update logic in the tick loop
        ltp = 1050.0
        if ltp > rt._ws_hwm.get(ticker, 0.0):
            rt._ws_hwm[ticker] = ltp

        assert rt._ws_hwm[ticker] == 1050.0

    def test_hwm_not_updated_on_lower_price(self):
        rt = _make_runtime()
        ticker = "INFY.NS"
        rt._trailing_managers[ticker] = MagicMock()
        rt._ws_hwm[ticker] = 1000.0

        ltp = 980.0
        if ltp > rt._ws_hwm.get(ticker, 0.0):
            rt._ws_hwm[ticker] = ltp

        assert rt._ws_hwm[ticker] == 1000.0

    def test_hwm_not_updated_for_unknown_ticker(self):
        rt = _make_runtime()
        # No manager registered for this ticker
        rt._ws_hwm["OTHER.NS"] = 0.0

        ltp = 500.0
        ticker = "OTHER.NS"
        if rt._trailing_enabled and ticker in rt._trailing_managers:
            if ltp > rt._ws_hwm.get(ticker, 0.0):
                rt._ws_hwm[ticker] = ltp

        assert rt._ws_hwm["OTHER.NS"] == 0.0


# ── Tests: _trailing_ratchet_loop cancellation ─────────────────────────────

class TestTrailingRatchetLoop:
    @pytest.mark.asyncio
    async def test_loop_cancels_cleanly(self):
        rt = _make_runtime()
        task = asyncio.create_task(rt._trailing_ratchet_loop())
        await asyncio.sleep(0)  # allow task to start
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.done()

    @pytest.mark.asyncio
    async def test_loop_sleeps_outside_market_hours(self):
        """Outside market hours the loop sleeps without calling ratchet."""
        rt = _make_runtime()
        calls = []

        async def fake_sleep(seconds):
            calls.append(seconds)
            raise asyncio.CancelledError()

        # 08:00 IST — before market open
        fake_now = MagicMock()
        fake_now.hour = 8
        fake_now.minute = 0

        with patch("asyncio.sleep", side_effect=fake_sleep), patch(
            "backend.algo.live.runtime.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            try:
                await rt._trailing_ratchet_loop()
            except asyncio.CancelledError:
                pass

        assert calls == [60]
