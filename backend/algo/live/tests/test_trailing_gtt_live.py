"""Tests for LiveRuntime trailing-stop GTT init (Task 7).

Covers:
- on_buy_fill_trailing: places GTT and saves to Redis
- on_buy_fill_trailing: no-op when trailing disabled
- on_buy_fill_trailing: no-op when atr_14 missing
- _load_trailing_state_from_redis: restores managers on restart
- PaperSupervisor.get_live_runtime: returns None for non-live runs
- PaperSupervisor.get_live_runtime: returns None for completed tasks
"""
import asyncio
import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from backend.algo.live.runtime import LiveRuntime
from backend.algo.backtest.trailing_stop_manager import TrailingStopManager
from backend.algo.paper.supervisor import PaperSupervisor


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_risk(
    *,
    stop_loss_pct: float = 5.0,
    trailing_trigger_pct: float | None = 5.0,
    trailing_atr_multiplier: float | None = 1.5,
    phase1_ratchet_trigger_pct: float | None = 2.0,
    phase1_ratchet_new_stop_pct: float | None = 3.0,
):
    r = MagicMock()
    r.stop_loss_pct = stop_loss_pct
    r.trailing_trigger_pct = trailing_trigger_pct
    r.trailing_atr_multiplier = trailing_atr_multiplier
    r.phase1_ratchet_trigger_pct = phase1_ratchet_trigger_pct
    r.phase1_ratchet_new_stop_pct = phase1_ratchet_new_stop_pct
    r.max_holding_days = 5
    r.cooldown_after_failed_exit_days = 7
    return r


def _make_strategy(risk=None, product="CNC"):
    s = MagicMock()
    s.id = uuid4()
    s.product = product
    s.risk = MagicMock()
    s.risk.per_trade = risk or _make_risk()
    s.universe = MagicMock()
    s.schedule = MagicMock()
    s.schedule.interval = "1d"
    return s


def _make_runtime(*, trailing=True) -> LiveRuntime:
    """Construct a LiveRuntime with all heavy I/O bypassed."""
    risk = _make_risk(
        trailing_trigger_pct=5.0 if trailing else None,
        trailing_atr_multiplier=1.5 if trailing else None,
    )
    strategy = _make_strategy(risk=risk)
    user_id = uuid4()

    kite = MagicMock()
    kite._dry_run = False  # pass dry_run guard
    kite.place_gtt.return_value = 42
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
            user_id=user_id,
            initial_capital_inr=Decimal("100000"),
            fee_as_of=date.today(),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=MagicMock(),
            kill_switch_repo=MagicMock(),
        )
    return rt


# ── Tests: on_buy_fill_trailing ─────────────────────────────────────────────

class TestOnBuyFillTrailing:
    def test_no_op_when_trailing_disabled(self):
        rt = _make_runtime(trailing=False)
        assert not rt._trailing_enabled
        rt.on_buy_fill_trailing(ticker="INFY.NS", fill_price=1000.0, qty=5)
        # no manager created
        assert "INFY.NS" not in rt._trailing_managers
        rt._kite.place_gtt.assert_not_called()

    def test_no_op_when_atr_missing(self):
        rt = _make_runtime()
        # factor cache empty → atr_14 = 0
        rt.on_buy_fill_trailing(ticker="INFY.NS", fill_price=1000.0, qty=5)
        assert "INFY.NS" not in rt._trailing_managers
        rt._kite.place_gtt.assert_not_called()

    def test_places_gtt_and_creates_manager(self):
        rt = _make_runtime()
        today = date.today()
        rt._factor_cache[(("INFY.NS", today))] = {"atr_14": Decimal("20.0")}

        with patch.object(
            rt, "_save_trailing_state", return_value=None
        ) as mock_save:
            rt.on_buy_fill_trailing(
                ticker="INFY.NS", fill_price=1000.0, qty=5,
            )

        assert "INFY.NS" in rt._trailing_managers
        mgr = rt._trailing_managers["INFY.NS"]
        assert isinstance(mgr, TrailingStopManager)
        # stop = entry_price * (1 - stop_loss_pct/100) = 1000 * 0.95 = 950
        assert abs(mgr.current_stop - 950.0) < 0.01
        assert rt._gtt_ids["INFY.NS"] == 42
        assert rt._ws_hwm["INFY.NS"] == 1000.0
        mock_save.assert_called_once()

    def test_place_gtt_uses_limit_headroom(self):
        rt = _make_runtime()
        today = date.today()
        rt._factor_cache[(("RELIANCE.NS", today))] = {
            "atr_14": Decimal("50.0"),
        }

        with patch.object(rt, "_save_trailing_state"):
            rt.on_buy_fill_trailing(
                ticker="RELIANCE.NS", fill_price=2000.0, qty=2,
            )

        call_kwargs = rt._kite.place_gtt.call_args[1]
        trigger = call_kwargs["trigger_price"]
        limit = call_kwargs["limit_price"]
        # limit must be 1% below trigger
        expected_limit = trigger * (1.0 - LiveRuntime._GTT_LIMIT_HEADROOM_PCT)
        assert abs(limit - expected_limit) < 0.001

    def test_graceful_on_place_gtt_exception(self):
        rt = _make_runtime()
        today = date.today()
        rt._factor_cache[(("TCS.NS", today))] = {"atr_14": Decimal("30.0")}
        rt._kite.place_gtt.side_effect = RuntimeError("Kite error")

        # Must not propagate
        rt.on_buy_fill_trailing(
            ticker="TCS.NS", fill_price=3000.0, qty=1,
        )
        assert "TCS.NS" not in rt._trailing_managers

    def test_event_appended_on_success(self):
        rt = _make_runtime()
        today = date.today()
        rt._factor_cache[(("WIPRO.NS", today))] = {
            "atr_14": Decimal("15.0"),
        }

        with patch.object(rt, "_save_trailing_state"):
            rt.on_buy_fill_trailing(
                ticker="WIPRO.NS", fill_price=500.0, qty=10,
            )

        types = [e["type"] for e in rt._events]
        assert "gtt_placed" in types


# ── Tests: _load_trailing_state_from_redis ──────────────────────────────────

class TestLoadTrailingStateFromRedis:
    def test_no_op_when_trailing_disabled(self):
        rt = _make_runtime(trailing=False)
        # Should not raise and leave dicts empty
        rt._load_trailing_state_from_redis()
        assert rt._trailing_managers == {}

    def test_restores_manager_from_redis(self):
        rt = _make_runtime()
        ticker = "INFY.NS"

        # Seed: build a manager state to simulate what _save puts in Redis
        mgr_data = {
            "phase": 1,
            "hwm": 1020.0,
            "current_stop": 950.0,
            "entry_price": 1000.0,
            "atr": 20.0,
        }
        redis_data = dict(mgr_data)
        redis_data["gtt_id"] = 77

        mock_pos = MagicMock()
        mock_pos.qty = 10
        rt._positions.open_positions = MagicMock(
            return_value={ticker: mock_pos}
        )

        mock_cache = MagicMock()
        mock_cache.get.return_value = json.dumps(redis_data)

        with patch(
            "backend.algo.live.runtime.TrailingStopManager.from_dict",
        ) as mock_from_dict:
            mock_mgr = MagicMock()
            mock_mgr.state = MagicMock()
            mock_mgr.state.phase = MagicMock()
            mock_mgr.state.phase.value = 1
            mock_mgr.state.hwm = 1020.0
            mock_mgr.current_stop = 950.0
            mock_from_dict.return_value = mock_mgr

            with patch(
                "backend.cache.get_cache",
                return_value=mock_cache,
            ):
                rt._load_trailing_state_from_redis()

        assert rt._trailing_managers[ticker] is mock_mgr
        assert rt._gtt_ids[ticker] == 77
        assert rt._ws_hwm[ticker] == 1020.0

    def test_skips_ticker_with_no_redis_entry(self):
        rt = _make_runtime()
        mock_pos = MagicMock()
        mock_pos.qty = 5
        rt._positions.open_positions = MagicMock(
            return_value={"WIPRO.NS": mock_pos}
        )
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch(
            "backend.cache.get_cache",
            return_value=mock_cache,
        ):
            rt._load_trailing_state_from_redis()

        assert rt._trailing_managers == {}

    def test_graceful_on_redis_exception(self):
        rt = _make_runtime()
        mock_pos = MagicMock()
        mock_pos.qty = 5
        rt._positions.open_positions = MagicMock(
            return_value={"HDFC.NS": mock_pos}
        )
        with patch(
            "backend.cache.get_cache",
            side_effect=ConnectionError("Redis down"),
        ):
            # Must not raise
            rt._load_trailing_state_from_redis()

        assert rt._trailing_managers == {}


# ── Tests: PaperSupervisor.get_live_runtime ──────────────────────────────────

class TestGetLiveRuntime:
    def _make_entry(self, *, mode: str = "live", done: bool = False):
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = done
        runtime = MagicMock()
        return {
            "user_id": uuid4(),
            "strategy_id": uuid4(),
            "strategy_name": "test",
            "started_at": MagicMock(),
            "task": task,
            "runtime": runtime,
            "mode": mode,
            "dry_run": False,
        }

    def test_returns_none_when_no_run(self):
        sv = PaperSupervisor()
        result = sv.get_live_runtime(
            user_id=uuid4(), strategy_id=uuid4(),
        )
        assert result is None

    def test_returns_none_for_paper_mode(self):
        sv = PaperSupervisor()
        entry = self._make_entry(mode="paper")
        uid = entry["user_id"]
        sid = entry["strategy_id"]
        sv._runs[(uid, sid)] = entry
        result = sv.get_live_runtime(user_id=uid, strategy_id=sid)
        assert result is None

    def test_returns_none_for_completed_task(self):
        sv = PaperSupervisor()
        entry = self._make_entry(mode="live", done=True)
        uid = entry["user_id"]
        sid = entry["strategy_id"]
        sv._runs[(uid, sid)] = entry
        result = sv.get_live_runtime(user_id=uid, strategy_id=sid)
        assert result is None

    def test_returns_runtime_for_active_live_run(self):
        sv = PaperSupervisor()
        entry = self._make_entry(mode="live", done=False)
        uid = entry["user_id"]
        sid = entry["strategy_id"]
        sv._runs[(uid, sid)] = entry
        result = sv.get_live_runtime(user_id=uid, strategy_id=sid)
        assert result is entry["runtime"]
