"""Integration tests for GTT trailing stop in live runtime (Task 9).

Covers:
- Redis serialisation/restoration roundtrip (TrailingStopManager)
- GTT placement on BUY fill (on_buy_fill_trailing)
- GTT ratchet on HWM advance to phase 2 (_ratchet_all_gtts)
- Time-stop: GTT cancelled before SELL, trailing state cleared
- SELL fill: _on_sell_fill_trailing clears all state
"""
import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.backtest.trailing_stop_manager import (
    TrailingPhase,
    TrailingStopManager,
)
from backend.algo.strategy.ast import RiskPerTrade


# ── shared risk fixture ──────────────────────────────────────────────────

def _v5_risk() -> RiskPerTrade:
    return RiskPerTrade(
        stop_loss_pct=5.0,
        max_qty=10000,
        phase1_ratchet_trigger_pct=2.0,
        phase1_ratchet_new_stop_pct=3.0,
        trailing_trigger_pct=5.0,
        trailing_atr_multiplier=1.5,
    )


# ── TrailingStateRoundtrip ────────────────────────────────────────────────

class TestTrailingStateRoundtrip:
    def test_to_dict_from_dict_preserves_hard_stop_phase(self):
        risk = _v5_risk()
        mgr = TrailingStopManager(risk, entry_price=5000.0, atr=125.0)
        d = mgr.to_dict()
        mgr2 = TrailingStopManager.from_dict(d, risk)
        assert mgr2.state.phase == TrailingPhase.HARD_STOP
        assert abs(mgr2.current_stop - mgr.current_stop) < 0.01

    def test_to_dict_from_dict_preserves_ratcheted_phase(self):
        risk = _v5_risk()
        mgr = TrailingStopManager(risk, entry_price=5000.0, atr=125.0)
        mgr.on_price_update(5110.0)   # 2.2% gain → phase1 ratchet
        d = mgr.to_dict()
        mgr2 = TrailingStopManager.from_dict(d, risk)
        assert mgr2.state.phase == TrailingPhase.RATCHETED
        assert abs(mgr2.current_stop - mgr.current_stop) < 0.01

    def test_to_dict_from_dict_preserves_atr_trail_phase(self):
        risk = _v5_risk()
        mgr = TrailingStopManager(risk, entry_price=5000.0, atr=125.0)
        mgr.on_price_update(5110.0)   # ratchet
        mgr.on_price_update(5260.0)   # 5.2% gain → ATR trail
        d = mgr.to_dict()
        mgr2 = TrailingStopManager.from_dict(d, risk)
        assert mgr2.state.phase == TrailingPhase.ATR_TRAIL
        assert abs(mgr2.current_stop - mgr.current_stop) < 0.01

    def test_redis_json_roundtrip(self):
        risk = _v5_risk()
        mgr = TrailingStopManager(risk, entry_price=5000.0, atr=125.0)
        mgr.on_price_update(5110.0)
        data = mgr.to_dict()
        data["gtt_id"] = 42
        serialised = json.dumps(data)
        restored = json.loads(serialised)
        gtt_id = int(restored.pop("gtt_id"))
        mgr2 = TrailingStopManager.from_dict(restored, risk)
        assert gtt_id == 42
        assert abs(mgr2.current_stop - mgr.current_stop) < 0.01


# ── GttPlacedOnBuyFill ────────────────────────────────────────────────────

def _make_runtime_with_trailing():
    """Minimal LiveRuntime with trailing enabled, heavy deps bypassed."""
    from backend.algo.live.runtime import LiveRuntime

    strategy = MagicMock()
    strategy.id = uuid4()
    strategy.product = "CNC"
    risk = _v5_risk()
    strategy.risk.per_trade = risk

    kite = MagicMock()
    kite._dry_run = False
    kite.place_gtt.return_value = 99999

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


class TestGttPlacedOnBuyFill:
    def test_manager_created_on_buy_fill(self):
        rt = _make_runtime_with_trailing()
        rt._factor_cache[("RELIANCE.NS", date.today())] = {
            "atr_14": Decimal("125"),
        }
        with patch.object(rt, "_save_trailing_state"):
            rt.on_buy_fill_trailing(
                ticker="RELIANCE.NS", fill_price=5000.0, qty=10,
            )
        assert "RELIANCE.NS" in rt._trailing_managers
        mgr = rt._trailing_managers["RELIANCE.NS"]
        # stop = 5000 * (1 - 0.05) = 4750
        assert abs(mgr.current_stop - 4750.0) < 0.01

    def test_gtt_placed_with_correct_trigger(self):
        rt = _make_runtime_with_trailing()
        rt._factor_cache[("RELIANCE.NS", date.today())] = {
            "atr_14": Decimal("125"),
        }
        with patch.object(rt, "_save_trailing_state"):
            rt.on_buy_fill_trailing(
                ticker="RELIANCE.NS", fill_price=5000.0, qty=10,
            )
        rt._kite.place_gtt.assert_called_once()
        call_kwargs = rt._kite.place_gtt.call_args[1]
        assert abs(call_kwargs["trigger_price"] - 4750.0) < 0.01
        assert call_kwargs["qty"] == 10

    def test_gtt_placed_event_emitted(self):
        rt = _make_runtime_with_trailing()
        rt._factor_cache[("RELIANCE.NS", date.today())] = {
            "atr_14": Decimal("125"),
        }
        with patch.object(rt, "_save_trailing_state"):
            rt.on_buy_fill_trailing(
                ticker="RELIANCE.NS", fill_price=5000.0, qty=10,
            )
        types = [e["type"] for e in rt._events]
        assert "gtt_placed" in types


# ── RatchetAllGtts (phase-2 HWM advance) ─────────────────────────────────

class TestRatchetAllGttsPhase2:
    def _make_runtime_in_phase2(
        self, entry: float = 5000.0, atr: float = 125.0,
    ):
        rt = _make_runtime_with_trailing()
        risk = rt._strategy.risk.per_trade

        mgr = TrailingStopManager(risk, entry_price=entry, atr=atr)
        mgr.on_price_update(5110.0)   # → RATCHETED (2.2% gain)
        mgr.on_price_update(5260.0)   # → ATR_TRAIL (5.2% gain)

        rt._trailing_managers["RELIANCE.NS"] = mgr
        rt._gtt_ids["RELIANCE.NS"] = 111
        rt._ws_hwm["RELIANCE.NS"] = 5400.0

        pos = MagicMock()
        pos.qty = 10
        rt._positions.open_positions = MagicMock(
            return_value={"RELIANCE.NS": pos}
        )
        rt._kite.place_gtt.return_value = 222
        return rt

    def test_gtt_ratcheted_on_hwm_advance(self):
        rt = self._make_runtime_in_phase2()
        with patch.object(rt, "_save_trailing_state"):
            rt._ratchet_all_gtts()
        rt._kite.delete_gtt.assert_called_once_with(111)
        rt._kite.place_gtt.assert_called_once()
        call_kwargs = rt._kite.place_gtt.call_args[1]
        # new stop = hwm - atr * multiplier = 5400 - 125*1.5 = 5212.5
        assert abs(call_kwargs["trigger_price"] - 5212.5) < 0.01

    def test_gtt_ratcheted_event_emitted(self):
        rt = self._make_runtime_in_phase2()
        with patch.object(rt, "_save_trailing_state"):
            rt._ratchet_all_gtts()
        types = [e["type"] for e in rt._events]
        assert "gtt_ratcheted" in types

    def test_gtt_id_updated_after_ratchet(self):
        rt = self._make_runtime_in_phase2()
        with patch.object(rt, "_save_trailing_state"):
            rt._ratchet_all_gtts()
        assert rt._gtt_ids["RELIANCE.NS"] == 222


# ── TimeStop GTT cleanup ──────────────────────────────────────────────────

class TestTimeStopGttCleanup:
    def test_on_sell_fill_clears_all_trailing_state(self):
        rt = _make_runtime_with_trailing()
        ticker = "INFY.NS"
        rt._trailing_managers[ticker] = MagicMock()
        rt._gtt_ids[ticker] = 55
        rt._ws_hwm[ticker] = 1100.0

        with patch("backend.cache.get_cache") as mock_gc:
            mock_cache = MagicMock()
            mock_gc.return_value = mock_cache
            rt._on_sell_fill_trailing(ticker)

        assert ticker not in rt._trailing_managers
        assert ticker not in rt._gtt_ids
        assert ticker not in rt._ws_hwm
        mock_cache.invalidate_exact.assert_called_once()

    def test_on_sell_fill_no_op_when_trailing_disabled(self):
        rt = _make_runtime_with_trailing()
        rt._trailing_enabled = False
        rt._gtt_ids["WIPRO.NS"] = 77
        rt._on_sell_fill_trailing("WIPRO.NS")
        # state untouched when trailing disabled
        assert rt._gtt_ids.get("WIPRO.NS") == 77

    def test_on_sell_fill_safe_when_no_state(self):
        rt = _make_runtime_with_trailing()
        # Must not raise even if ticker has no trailing state
        rt._on_sell_fill_trailing("HDFC.NS")

    def test_on_sell_fill_graceful_on_redis_error(self):
        rt = _make_runtime_with_trailing()
        ticker = "TCS.NS"
        rt._trailing_managers[ticker] = MagicMock()
        rt._gtt_ids[ticker] = 33

        with patch(
            "backend.cache.get_cache",
            side_effect=ConnectionError("Redis down"),
        ):
            rt._on_sell_fill_trailing(ticker)

        assert ticker not in rt._trailing_managers
        assert ticker not in rt._gtt_ids
