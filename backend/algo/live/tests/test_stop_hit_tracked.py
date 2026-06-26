"""Task 7.6: STOP_HIT emergency SELL routed through tracked _submit_order.

When _ratchet_all_gtts runs on a WORKER thread (production path via
asyncio.to_thread) and self._loop is set, the emergency SELL must go
through _submit_order — which records an _in_flight entry, applies
tick-rounding, and wires up reconciliation correlation — rather than
calling place_order directly.

The test uses the _in_flight assertion (the task's acceptance criterion).
We spy _submit_order as an AsyncMock with a side-effect that appends a
synthetic _in_flight entry, then after the thread finishes assert that
_submit_order was awaited once with a SELL Signal whose
reason=="stop_loss" and qty==pos.qty, and that an _in_flight entry is
present for the ticker.

Existing sync tests (test_trailing_ratchet.py::test_stop_hit_via_hwm_*
) exercise the fallback path (self._loop is None) and must still pass
unchanged.
"""
import asyncio
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.algo.backtest.trailing_stop_manager import TrailingStopManager
from backend.algo.live.runtime import LiveRuntime
from backend.algo.paper.types import Signal


# ── Helpers (mirrors test_trailing_ratchet._make_runtime) ─────────────────


def _make_runtime() -> LiveRuntime:
    risk = MagicMock()
    risk.stop_loss_pct = 5.0
    risk.trailing_trigger_pct = 5.0
    risk.trailing_atr_multiplier = 1.5
    risk.phase1_ratchet_trigger_pct = 2.0
    risk.phase1_ratchet_new_stop_pct = 3.0
    risk.max_holding_days = 5
    risk.cooldown_after_failed_exit_days = 7

    strategy = MagicMock()
    strategy.id = uuid.uuid4()
    strategy.product = "CNC"
    strategy.risk = MagicMock()
    strategy.risk.per_trade = risk

    kite = MagicMock()
    kite._dry_run = False
    kite.place_gtt.return_value = 99
    kite.delete_gtt.return_value = None
    kite.place_order.return_value = "ORDER123"

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
            user_id=uuid.uuid4(),
            initial_capital_inr=Decimal("100000"),
            fee_as_of=date.today(),
            kite=kite,
            caps=caps,
            run_id=uuid.uuid4(),
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


# ── Task 7.6: tracked-path test ───────────────────────────────────────────


class TestStopHitTrackedPath:
    """Emergency SELL is routed through _submit_order on the worker-thread
    path (production), recording an _in_flight entry."""

    @pytest.mark.asyncio
    async def test_stop_hit_worker_thread_routes_via_submit_order(
        self,
    ):
        """Running _ratchet_all_gtts via asyncio.to_thread (worker thread)
        with self._loop set causes the STOP_HIT branch to call
        _submit_order rather than place_order directly.

        Acceptance criterion (task 7.6): _submit_order is awaited once
        with a SELL Signal whose reason=="stop_loss" and qty==pos.qty;
        an _in_flight entry is recorded; the GTT is deleted; the trailing
        manager is removed from state.

        We spy _submit_order as an AsyncMock whose side-effect appends a
        synthetic _in_flight entry, mirroring what the real method does.
        """
        rt = _make_runtime()

        ticker = "INFY.NS"
        qty = 5
        _seed_manager(rt, ticker=ticker, qty=qty, gtt_id=11)
        # Set HWM below stop to trigger STOP_HIT (stop = entry*0.95=950)
        rt._ws_hwm[ticker] = 940.0

        # Wire up the event loop so use_tracked=True on the worker thread
        rt._loop = asyncio.get_running_loop()

        # Captured arguments from _submit_order
        captured_signals: list[Signal] = []

        async def fake_submit_order(
            *, signal: Signal, last_price: Decimal, **kwargs
        ) -> int:
            captured_signals.append(signal)
            # Simulate the _in_flight append the real method does
            rt._in_flight.append(
                {
                    "kite_order_id": "FAKE999",
                    "internal_order_id": str(uuid.uuid4()),
                    "symbol": ticker.removesuffix(".NS"),
                    "side": signal.side,
                    "qty": signal.qty,
                    "submitted_at": "2026-06-27T10:00:00+05:30",
                    "status": "submitted",
                }
            )
            return 1

        submit_mock = AsyncMock(side_effect=fake_submit_order)
        rt._submit_order = submit_mock  # type: ignore[assignment]

        # Run _ratchet_all_gtts on a WORKER THREAD — this is the
        # production path (asyncio.to_thread) so run_coroutine_threadsafe
        # targets the current test loop and the coro can complete.
        await asyncio.to_thread(rt._ratchet_all_gtts)

        # ── Assertions ────────────────────────────────────────────────

        # 1. _submit_order was called exactly once
        submit_mock.assert_awaited_once()

        # 2. Signal had correct side, qty, and reason
        assert len(captured_signals) == 1
        sig = captured_signals[0]
        assert sig.side == "SELL"
        assert sig.qty == qty
        assert sig.reason == "stop_loss"
        assert sig.ticker == ticker

        # 3. ACCEPTANCE CRITERION: an _in_flight entry was recorded
        in_flight_symbols = [e.get("symbol") for e in rt._in_flight]
        assert "INFY" in in_flight_symbols

        # 4. The old GTT was deleted
        rt._kite.delete_gtt.assert_called_once_with(11)

        # 5. Direct place_order was NOT called (tracked path replaces it)
        rt._kite.place_order.assert_not_called()

        # 6. Trailing manager was cleaned up
        assert ticker not in rt._trailing_managers
        assert ticker not in rt._gtt_ids


class TestStopHitTrackedTimeout:
    """Timeout on run_coroutine_threadsafe.result() must NOT pop the
    manager (no abandon) and must NOT call place_order directly
    (no double-sell risk when the order may already be in flight).
    """

    @pytest.mark.asyncio
    async def test_stop_hit_timeout_retains_manager_no_double_sell(
        self,
        monkeypatch,
    ):
        """When _submit_order hangs beyond _EMERGENCY_SUBMIT_TIMEOUT_S
        the ratchet must:
        1. Not propagate any exception.
        2. Retain the trailing manager for next-tick retry.
        3. Not call place_order directly (order may be in flight).
        """
        import backend.algo.live.runtime as _rt_mod

        # Tiny timeout so the test completes quickly.
        monkeypatch.setattr(_rt_mod, "_EMERGENCY_SUBMIT_TIMEOUT_S", 0.05)

        rt = _make_runtime()
        ticker = "INFY.NS"
        qty = 5
        _seed_manager(rt, ticker=ticker, qty=qty, gtt_id=11)
        rt._ws_hwm[ticker] = 940.0  # STOP_HIT condition

        rt._loop = asyncio.get_running_loop()

        # _submit_order sleeps long enough to exceed the tiny timeout.
        async def _slow_submit(**_kwargs):
            await asyncio.sleep(5)
            return 1

        rt._submit_order = AsyncMock(  # type: ignore[assignment]
            side_effect=_slow_submit
        )

        # Run on worker thread — must complete without raising.
        await asyncio.to_thread(rt._ratchet_all_gtts)

        # Manager RETAINED — next ratchet tick (~30s) will retry.
        assert ticker in rt._trailing_managers

        # No direct place_order called — prevents double-sell when
        # the order may already be in flight on the event loop.
        rt._kite.place_order.assert_not_called()
