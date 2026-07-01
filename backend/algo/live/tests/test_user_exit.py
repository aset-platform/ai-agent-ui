"""Tests for LiveRuntime.user_exit_position (user-initiated exit).

Covers:
- Happy path: GTT cancelled, trailing state cleared, SELL submitted
- No GTT exists: SELL still submitted (GTT cancel skipped)
- No open position in runtime or override: raises ValueError
- qty_override used when _positions has no entry
- Dry-run: _submit_order called, event emitted
- LTP fallback to ws_hwm when kite.ltp() raises
"""

import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.live.runtime import LiveRuntime
from backend.algo.backtest.trailing_stop_manager import (
    TrailingStopManager,
)


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_runtime(*, dry_run: bool = False) -> LiveRuntime:
    strategy = MagicMock()
    strategy.id = uuid4()
    strategy.product = "CNC"
    risk = MagicMock()
    risk.stop_loss_pct = 5.0
    risk.trailing_trigger_pct = 5.0
    risk.trailing_atr_multiplier = 1.5
    risk.phase1_ratchet_trigger_pct = 2.0
    risk.phase1_ratchet_new_stop_pct = 3.0
    risk.max_holding_days = 5
    risk.cooldown_after_failed_exit_days = 7
    strategy.risk = MagicMock()
    strategy.risk.per_trade = risk

    kite = MagicMock()
    kite.dry_run = dry_run
    kite.place_gtt.return_value = 99
    kite.delete_gtt.return_value = None
    # kite.ltp returns NSE:BARE keyed dict by default
    kite.ltp.return_value = {"NSE:INFY": {"last_price": 1850.0}}

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
    rt._dry_run = dry_run
    return rt


def _seed_position(
    rt: LiveRuntime,
    ticker: str = "INFY.NS",
    entry_price: float = 1500.0,
    gtt_id: int = 77,
    qty: int = 10,
) -> None:
    """Seed trailing state + open position into a runtime."""
    mgr = TrailingStopManager(
        rt._strategy.risk.per_trade,
        entry_price=entry_price,
        atr=30.0,
        ticker=ticker,
    )
    rt._trailing_managers[ticker] = mgr
    rt._gtt_ids[ticker] = gtt_id
    rt._ws_hwm[ticker] = entry_price

    pos = MagicMock()
    pos.qty = qty
    rt._positions.open_positions = MagicMock(
        return_value={ticker: pos},
    )


# ── Tests ─────────────────────────────────────────────────────────────────

class TestUserExitPositionHappyPath:
    def test_cancels_gtt_and_submits_sell(self):
        rt = _make_runtime()
        _seed_position(rt, ticker="INFY.NS", gtt_id=77, qty=10)
        rt._kite.ltp.return_value = {"NSE:INFY": {"last_price": 1850.0}}

        submitted_signals = []

        async def fake_submit(*, signal, last_price, **kw):
            submitted_signals.append(signal)
            rt._in_flight.append({
                "kite_order_id": "KITE_123",
                "reason": signal.reason,
            })
            return 1

        async def run():
            with patch.object(rt, "_submit_order", side_effect=fake_submit), \
                 patch.object(rt, "_flush_events_now", new_callable=AsyncMock), \
                 patch("asyncio.to_thread", side_effect=_async_kite_shim(rt)):
                return await rt.user_exit_position(ticker="INFY.NS")

        result = asyncio.run(run())

        assert result["submitted"] is True
        assert result["ticker"] == "INFY.NS"
        assert result["qty"] == 10
        assert result["gtt_id_cancelled"] == 77
        assert result["kite_order_id"] == "KITE_123"

        # Trailing state must be cleared.
        assert "INFY.NS" not in rt._trailing_managers
        assert "INFY.NS" not in rt._gtt_ids
        assert "INFY.NS" not in rt._ws_hwm

        # Signal must be a SELL with reason=user_exit.
        assert len(submitted_signals) == 1
        sig = submitted_signals[0]
        assert sig.side == "SELL"
        assert sig.qty == 10
        assert sig.reason == "user_exit"

    def test_event_emitted_with_correct_payload(self):
        rt = _make_runtime()
        _seed_position(rt, ticker="INFY.NS", gtt_id=42, qty=5)

        async def fake_submit(*, signal, last_price, **kw):
            rt._in_flight.append({
                "kite_order_id": "K999",
                "reason": signal.reason,
            })
            return 1

        async def run():
            with patch.object(rt, "_submit_order", side_effect=fake_submit), \
                 patch.object(rt, "_flush_events_now", new_callable=AsyncMock), \
                 patch("asyncio.to_thread", side_effect=_async_kite_shim(rt)):
                return await rt.user_exit_position(ticker="INFY.NS")

        asyncio.run(run())

        evt = next(
            (e for e in rt._events if e["type"] == "user_exit_initiated"),
            None,
        )
        assert evt is not None
        p = evt["payload_json"]
        import json
        payload = json.loads(p)
        assert payload["ticker"] == "INFY.NS"
        assert payload["qty"] == 5
        assert payload["gtt_id_cancelled"] == 42
        assert payload["source"] == "user_action"
        assert payload["kite_order_id"] == "K999"


class TestUserExitPositionNoGtt:
    def test_no_gtt_skips_cancel_but_submits_sell(self):
        rt = _make_runtime()
        _seed_position(rt, ticker="INFY.NS", gtt_id=0, qty=8)
        # Ensure no GTT is tracked
        rt._gtt_ids.pop("INFY.NS", None)

        submitted = []

        async def fake_submit(*, signal, last_price, **kw):
            submitted.append(1)
            rt._in_flight.append({
                "kite_order_id": "DRY_ABC",
                "reason": signal.reason,
            })
            return 1

        async def run():
            with patch.object(rt, "_submit_order", side_effect=fake_submit), \
                 patch.object(rt, "_flush_events_now", new_callable=AsyncMock), \
                 patch("asyncio.to_thread", side_effect=_async_kite_shim(rt)):
                return await rt.user_exit_position(ticker="INFY.NS")

        result = asyncio.run(run())

        assert result["submitted"] is True
        assert result["gtt_id_cancelled"] == 0
        assert len(submitted) == 1
        # delete_gtt should NOT have been called (no gtt_id)
        rt._kite.delete_gtt.assert_not_called()


class TestUserExitPositionNoPosition:
    def test_no_position_and_no_override_raises(self):
        rt = _make_runtime()
        rt._positions.open_positions = MagicMock(return_value={})

        async def run():
            await rt.user_exit_position(ticker="INFY.NS")

        with pytest.raises(ValueError, match="no open position"):
            asyncio.run(run())

    def test_qty_override_used_when_runtime_has_no_position(self):
        rt = _make_runtime()
        rt._positions.open_positions = MagicMock(return_value={})
        rt._ws_hwm["INFY.NS"] = 1800.0

        submitted_signals = []

        async def fake_submit(*, signal, last_price, **kw):
            submitted_signals.append(signal)
            rt._in_flight.append({
                "kite_order_id": "K_OVR",
                "reason": signal.reason,
            })
            return 1

        async def run():
            with patch.object(rt, "_submit_order", side_effect=fake_submit), \
                 patch.object(rt, "_flush_events_now", new_callable=AsyncMock), \
                 patch("asyncio.to_thread", side_effect=_async_kite_shim(rt)):
                return await rt.user_exit_position(
                    ticker="INFY.NS",
                    qty_override=7,
                )

        result = asyncio.run(run())

        assert result["submitted"] is True
        assert result["qty"] == 7
        assert submitted_signals[0].qty == 7


class TestUserExitPositionLtpFallback:
    def test_falls_back_to_ws_hwm_on_ltp_error(self):
        rt = _make_runtime()
        _seed_position(rt, ticker="INFY.NS", entry_price=1600.0)

        prices_used = []

        async def fake_submit(*, signal, last_price, **kw):
            prices_used.append(last_price)
            rt._in_flight.append({
                "kite_order_id": "K_HWM",
                "reason": signal.reason,
            })
            return 1

        def shim(fn, *args, **kw):
            # Make ltp() raise; delete_gtt returns None
            if fn == rt._kite.ltp or (
                hasattr(fn, "__self__") and fn.__self__ is rt._kite
                and fn.__name__ == "ltp"
            ):
                raise RuntimeError("kite.ltp timeout")
            return fn(*args, **kw)

        async def run():
            with patch.object(rt, "_submit_order", side_effect=fake_submit), \
                 patch.object(rt, "_flush_events_now", new_callable=AsyncMock), \
                 patch(
                     "asyncio.to_thread",
                     side_effect=lambda fn, *a, **k:
                         _raise_if_ltp(fn, rt) or fn(*a, **k),
                 ):
                return await rt.user_exit_position(ticker="INFY.NS")

        result = asyncio.run(run())
        assert result["submitted"] is True
        # Price should be the ws_hwm (1600.0) since ltp raised
        assert Decimal("1600.0") == Decimal(result["price"])


class TestUserExitPositionDryRun:
    def test_dry_run_still_submits_and_emits_event(self):
        rt = _make_runtime(dry_run=True)
        _seed_position(rt, ticker="INFY.NS", gtt_id=55, qty=3)

        submitted_signals = []

        async def fake_submit(*, signal, last_price, **kw):
            submitted_signals.append(signal)
            rt._in_flight.append({
                "kite_order_id": "DRY_XYZ",
                "reason": signal.reason,
            })
            return 1

        async def run():
            with patch.object(rt, "_submit_order", side_effect=fake_submit), \
                 patch.object(rt, "_flush_events_now", new_callable=AsyncMock), \
                 patch("asyncio.to_thread", side_effect=_async_kite_shim(rt)):
                return await rt.user_exit_position(ticker="INFY.NS")

        result = asyncio.run(run())

        assert result["submitted"] is True
        assert submitted_signals[0].reason == "user_exit"
        evt = next(
            (e for e in rt._events if e["type"] == "user_exit_initiated"),
            None,
        )
        assert evt is not None
        import json
        payload = json.loads(evt["payload_json"])
        assert payload["dry_run"] is True


# ── Shared shims ──────────────────────────────────────────────────────────

def _async_kite_shim(rt: LiveRuntime):
    """asyncio.to_thread side_effect that routes kite calls correctly."""
    def _shim(fn, *args, **kw):
        # For kite.ltp, return the mock's return_value directly
        if fn is rt._kite.ltp or (
            callable(fn) and getattr(fn, "__self__", None) is rt._kite
            and getattr(fn, "__name__", "") == "ltp"
        ):
            return rt._kite.ltp.return_value
        # For delete_gtt, call the mock normally
        return fn(*args, **kw)
    return _shim


def _raise_if_ltp(fn, rt: LiveRuntime):
    """Raise RuntimeError if fn is kite.ltp; return None otherwise."""
    if fn is rt._kite.ltp or (
        callable(fn) and getattr(fn, "__self__", None) is rt._kite
        and getattr(fn, "__name__", "") == "ltp"
    ):
        raise RuntimeError("kite.ltp timeout")
    return None
