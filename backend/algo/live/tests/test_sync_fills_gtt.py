"""Tests for protective GTT/trailing placement on fill-synced BUYs.

Task 3.1 (Critical C5): when a BUY is applied via ``_sync_fills_from_pg``
(postback-miss / mid-session restart path) the position used to be left
NAKED — no trailing manager, no protective GTT. This mirrors the webhook
postback's ``on_buy_fill_trailing`` protection into the fill-sync loop.

Covers:
- fill-synced BUY (trailing enabled) → on_buy_fill_trailing invoked,
  trailing manager + GTT created.
- idempotency: a second sync of the same fill does not double-place.
- trailing disabled → no GTT attempted (mirrors webhook gate).
- SELL fill → no trailing manager created.
"""
import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from backend.algo.live.runtime import LiveRuntime


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_risk(*, trailing=True):
    r = MagicMock()
    r.stop_loss_pct = 5.0
    r.trailing_trigger_pct = 5.0 if trailing else None
    r.trailing_atr_multiplier = 1.5 if trailing else None
    r.phase1_ratchet_trigger_pct = 2.0
    r.phase1_ratchet_new_stop_pct = 3.0
    r.max_holding_days = 5
    r.cooldown_after_failed_exit_days = 7
    return r


def _make_strategy(risk):
    s = MagicMock()
    s.id = uuid4()
    s.product = "CNC"
    s.risk = MagicMock()
    s.risk.per_trade = risk
    s.universe = MagicMock()
    s.schedule = MagicMock()
    s.schedule.interval = "1d"
    return s


def _make_runtime(*, trailing=True) -> LiveRuntime:
    """Construct a LiveRuntime with all heavy I/O bypassed."""
    risk = _make_risk(trailing=trailing)
    strategy = _make_strategy(risk)

    kite = MagicMock()
    kite._dry_run = False
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


def _filled_buy(sym="INFY", qty=5, price=1000.0):
    return {
        "status": "filled",
        "kite_order_id": "ko-1",
        "side": "BUY",
        "symbol": sym,
        "qty": qty,
        "fill_price": price,
        "reservation_id": None,
    }


# ── Tests ────────────────────────────────────────────────────────────────────

class TestSyncFillsGtt:
    def test_buy_fill_sync_places_protective_gtt(self):
        rt = _make_runtime(trailing=True)
        rt._factor_cache[("INFY.NS", date.today())] = {
            "atr_14": Decimal("20.0"),
        }
        rt._caps_repo.get_in_flight = AsyncMock(
            return_value=[_filled_buy()],
        )
        with patch.object(rt, "_save_trailing_state"), patch.object(
            rt, "_sync_ticker_lock_to_redis"
        ):
            asyncio.run(rt._sync_fills_from_pg())

        assert "INFY.NS" in rt._trailing_managers
        rt._kite.place_gtt.assert_called_once()
        assert rt._gtt_ids["INFY.NS"] == 42

    def test_idempotent_no_double_place(self):
        rt = _make_runtime(trailing=True)
        rt._factor_cache[("INFY.NS", date.today())] = {
            "atr_14": Decimal("20.0"),
        }
        rt._caps_repo.get_in_flight = AsyncMock(
            return_value=[_filled_buy()],
        )
        with patch.object(rt, "_save_trailing_state"), patch.object(
            rt, "_sync_ticker_lock_to_redis"
        ):
            asyncio.run(rt._sync_fills_from_pg())
            asyncio.run(rt._sync_fills_from_pg())

        # GTT placed exactly once across two syncs of the same fill.
        rt._kite.place_gtt.assert_called_once()

    def test_no_gtt_when_trailing_disabled(self):
        rt = _make_runtime(trailing=False)
        assert not rt._trailing_enabled
        rt._caps_repo.get_in_flight = AsyncMock(
            return_value=[_filled_buy()],
        )
        with patch.object(rt, "_sync_ticker_lock_to_redis"):
            asyncio.run(rt._sync_fills_from_pg())

        assert "INFY.NS" not in rt._trailing_managers
        rt._kite.place_gtt.assert_not_called()

    def test_sell_fill_no_trailing_manager(self):
        rt = _make_runtime(trailing=True)
        sell = _filled_buy()
        sell["side"] = "SELL"
        rt._caps_repo.get_in_flight = AsyncMock(return_value=[sell])
        with patch.object(rt, "_sync_ticker_lock_to_redis"):
            asyncio.run(rt._sync_fills_from_pg())

        assert "INFY.NS" not in rt._trailing_managers
        rt._kite.place_gtt.assert_not_called()
