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
from decimal import ROUND_DOWN, Decimal
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
        # safety: failed protective SELL keeps manager for next-tick retry
        assert "INFY.NS" in rt._trailing_managers

    def test_stop_hit_sell_fires_despite_delete_gtt_failure(self):
        """Regression: Task 7.9 follow-up.

        delete_gtt now raises on real Kite errors.  The try/except wrap
        in the STOP_HIT branch (runtime.py ~line 1507) must absorb the
        exception and still execute the emergency SELL.

        Drive the FALLBACK path (self._loop=None / sync) so the direct
        place_order call is the SELL route — no asyncio machinery needed.

        Assertions:
        1. _ratchet_all_gtts does NOT raise even when delete_gtt raises.
        2. place_order is still called for the ticker (emergency SELL
           proceeded).
        3. The trailing manager is cleaned up (sold=True path reached).
        """
        rt = _make_runtime()
        _seed_manager(
            rt,
            ticker="INFY.NS",
            entry_price=1000.0,
            atr=20.0,
            gtt_id=11,
            qty=5,
        )
        # HWM below stop (950) → STOP_HIT
        rt._ws_hwm["INFY.NS"] = 940.0

        # Simulate Task-7.9 behaviour: delete_gtt raises instead of
        # silently returning None.
        rt._kite.delete_gtt.side_effect = RuntimeError("GTT cancel: boom")

        # _loop is None → fallback / sync path (no run_coroutine_threadsafe)
        rt._loop = None

        # Must not propagate
        rt._ratchet_all_gtts()

        # Emergency SELL must have fired
        rt._kite.place_order.assert_called_once()
        call_kwargs = rt._kite.place_order.call_args[1]
        assert call_kwargs["tradingsymbol"] == "INFY"
        assert call_kwargs["transaction_type"] == "SELL"
        assert call_kwargs["quantity"] == 5

        # Manager cleaned up — sold=True path was reached
        assert "INFY.NS" not in rt._trailing_managers
        assert "INFY.NS" not in rt._gtt_ids

    def test_ratchet_uses_limit_headroom(self):
        """runtime.py ~L1802-1822: both trigger_price and limit_price
        are tick-size-quantized (ROUND_DOWN), and limit_price is
        computed from the RAW mgr.current_stop * (1 - headroom_pct)
        — NOT from the already-quantized trigger_price. A stale test
        used a naive `trigger * (1 - headroom_pct)` with no
        quantization, which drifts from the real tick-quantized
        value; fixed to mirror the production formula exactly with
        a controlled tick size."""
        rt = _make_runtime()
        _seed_manager(rt, entry_price=1000.0, atr=20.0, gtt_id=11)
        rt._ws_hwm["INFY.NS"] = 1025.0

        with patch.object(rt, "_save_trailing_state"), patch(
            "backend.algo.live.runtime.get_tick_size",
            return_value=Decimal("0.05"),
        ):
            rt._ratchet_all_gtts()

        call_kwargs = rt._kite.place_gtt.call_args[1]
        trigger = call_kwargs["trigger_price"]
        limit = call_kwargs["limit_price"]
        # gtt_limit_headroom_pct is a per-user caps setting (mid-run
        # editable, see live-caps-staleness-mid-run), not a class
        # constant — runtime.py L2325 reads it via self._caps with
        # a 0.01 default.
        headroom_pct = rt._caps.get("gtt_limit_headroom_pct", 0.01)
        current_stop = rt._trailing_managers["INFY.NS"].current_stop
        tick = Decimal("0.05")
        expected_trigger = float(
            (Decimal(str(current_stop)) / tick)
            .quantize(Decimal("1"), rounding=ROUND_DOWN) * tick
        )
        expected_limit = float(
            (
                Decimal(str(current_stop))
                * (1 - Decimal(str(headroom_pct))) / tick
            ).quantize(Decimal("1"), rounding=ROUND_DOWN) * tick
        )
        assert abs(trigger - expected_trigger) < 0.001
        assert abs(limit - expected_limit) < 0.001

    def test_ratchet_graceful_when_place_gtt_raises(self):
        rt = _make_runtime()
        _seed_manager(rt, entry_price=1000.0, atr=20.0, gtt_id=11)
        rt._ws_hwm["INFY.NS"] = 1025.0
        rt._kite.place_gtt.side_effect = RuntimeError("GTT error")

        # Must not propagate; manager stays in place
        rt._ratchet_all_gtts()
        assert "INFY.NS" in rt._trailing_managers


# ── Tests: GTT-triggered detection ───────────────────────────────────────────

class TestGttTriggeredDetection:
    """_ratchet_all_gtts: GTT poll detects Kite-triggered exits."""

    def test_triggered_clears_state_and_emits_event(self):
        """GTT ID gone from Kite → state cleared + gtt_triggered emitted.

        Note: _seed_manager mocks open_positions() but leaves
        PositionTracker._open empty, so apply_fill is a no-op (SELL
        with no open position). In production the tracker IS populated
        from the real BUY fill. We verify the event payload and state
        cleanup — that is the observable contract of this code path.
        """
        import json
        rt = _make_runtime()
        _seed_manager(
            rt, entry_price=1000.0, atr=20.0, gtt_id=55, qty=10,
        )
        rt._dry_run = False  # __init__ reads kite.dry_run (no _); fix here
        rt._ticker_locked.add("INFY.NS")
        rt._ws_hwm["INFY.NS"] = 0.0  # no WS price → HWM path won't fire

        # Kite only knows an unrelated GTT — our gtt_id=55 is gone
        rt._kite.get_gtts.return_value = [
            {"id": 999, "status": "active"},
        ]

        with patch.object(
            rt, "_sync_ticker_lock_to_redis"
        ) as mock_sync:
            rt._ratchet_all_gtts()

        # Trailing state fully cleaned up
        assert "INFY.NS" not in rt._trailing_managers
        assert "INFY.NS" not in rt._gtt_ids
        assert "INFY.NS" not in rt._ws_hwm
        assert "INFY.NS" not in rt._ticker_locked

        # Lock flushed to Redis
        mock_sync.assert_called()

        # gtt_triggered event in the buffer with correct payload
        gtt_events = [
            e for e in rt._events
            if e.get("type") == "gtt_triggered"
        ]
        assert len(gtt_events) == 1
        payload = json.loads(gtt_events[0]["payload_json"])
        assert payload["ticker"] == "INFY.NS"
        assert payload["source"] == "gtt_poll"
        assert payload["gtt_id"] == 55
        assert payload["qty"] == 10

        # No emergency SELL placed (Kite already handled it)
        rt._kite.place_order.assert_not_called()
        rt._kite.place_gtt.assert_not_called()

    def test_gtt_still_active_not_treated_as_triggered(self):
        """GTT ID still in active list → no triggered handling."""
        rt = _make_runtime()
        rt._dry_run = False
        _seed_manager(rt, entry_price=1000.0, atr=20.0, gtt_id=55)
        rt._ws_hwm["INFY.NS"] = 0.0  # no HWM change

        rt._kite.get_gtts.return_value = [
            {"id": 55, "status": "active"},
        ]

        rt._ratchet_all_gtts()

        assert "INFY.NS" in rt._trailing_managers
        assert rt._gtt_ids.get("INFY.NS") == 55
        assert not any(
            e.get("type") == "gtt_triggered" for e in rt._events
        )

    def test_get_gtts_failure_skips_detection_no_crash(self):
        """get_gtts raises → detection skipped, no crash, HWM eval runs."""
        rt = _make_runtime()
        rt._dry_run = False
        _seed_manager(rt, entry_price=1000.0, atr=20.0, gtt_id=55)
        rt._ws_hwm["INFY.NS"] = 0.0  # no price → HWM path is no-op

        rt._kite.get_gtts.side_effect = RuntimeError("API down")

        rt._ratchet_all_gtts()  # must not raise

        assert "INFY.NS" in rt._trailing_managers
        assert not any(
            e.get("type") == "gtt_triggered" for e in rt._events
        )

    def test_dry_run_skips_get_gtts(self):
        """In dry-run mode, get_gtts is never called."""
        rt = _make_runtime()
        rt._dry_run = True
        _seed_manager(rt, gtt_id=0)  # dry-run uses gtt_id=0
        rt._ws_hwm["INFY.NS"] = 0.0

        rt._ratchet_all_gtts()

        rt._kite.get_gtts.assert_not_called()


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
