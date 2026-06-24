"""Task 3.4 — close-time GTT + Redis cleanup (provably-gone gate).

Safety property under test: NEVER cancel/clean a ticker that still
has ANY real position. The "provably gone" gate must consult ALL
broker sources (positions().net + holdings quantity + t1_quantity)
so today's CNC buys (net>0, holdings=0) are correctly treated as
HELD. If the broker read fails -> clean NOTHING (fail safe).

Covers:
- Part A: _on_sell_fill_trailing cancels the GTT + drops the lock.
- Part B: _cleanup_stale_protection cleans only provably-gone
  tickers, never held ones, and bails on broker-unreadable.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.strategy.ast import RiskPerTrade


def _v5_risk() -> RiskPerTrade:
    return RiskPerTrade(
        stop_loss_pct=5.0,
        max_qty=10000,
        phase1_ratchet_trigger_pct=2.0,
        phase1_ratchet_new_stop_pct=3.0,
        trailing_trigger_pct=5.0,
        trailing_atr_multiplier=1.5,
    )


def _make_runtime():
    """Minimal LiveRuntime, trailing enabled, heavy deps bypassed."""
    from backend.algo.live.runtime import LiveRuntime

    strategy = MagicMock()
    strategy.id = uuid4()
    strategy.product = "CNC"
    strategy.risk.per_trade = _v5_risk()

    kite = MagicMock()
    kite._dry_run = False

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


def _set_broker(rt, *, net=None, holdings=None, raises=False):
    """Wire kite._kc.positions()/holdings() return values."""
    kc = MagicMock()
    if raises:
        kc.positions.side_effect = RuntimeError("broker down")
        kc.holdings.side_effect = RuntimeError("broker down")
    else:
        kc.positions.return_value = {"net": net or []}
        kc.holdings.return_value = holdings or []
    rt._kite._kc = kc


def _gtt(symbol, gid):
    return {
        "id": gid,
        "status": "active",
        "condition": {
            "tradingsymbol": symbol,
            "trigger_values": [100.0],
        },
    }


# ── Part A: _on_sell_fill_trailing ───────────────────────────────

class TestClearTrailingStatePartA:
    def test_cancels_gtt_clears_lock_and_state(self):
        rt = _make_runtime()
        ticker = "INFY.NS"
        rt._trailing_managers[ticker] = MagicMock()
        rt._gtt_ids[ticker] = 55
        rt._ws_hwm[ticker] = 1100.0
        rt._ticker_locked.add(ticker)

        with patch("backend.cache.get_cache") as mock_gc, \
                patch.object(rt, "_sync_ticker_lock_to_redis") as sync:
            mock_gc.return_value = MagicMock()
            rt._on_sell_fill_trailing(ticker)

        rt._kite.delete_gtt.assert_called_once_with(55)
        assert ticker not in rt._trailing_managers
        assert ticker not in rt._gtt_ids
        assert ticker not in rt._ws_hwm
        assert ticker not in rt._ticker_locked
        sync.assert_called_once()

    def test_no_gtt_still_clears_lock_and_state(self):
        rt = _make_runtime()
        ticker = "TCS.NS"
        rt._trailing_managers[ticker] = MagicMock()
        rt._ticker_locked.add(ticker)

        with patch("backend.cache.get_cache") as mock_gc, \
                patch.object(rt, "_sync_ticker_lock_to_redis") as sync:
            mock_gc.return_value = MagicMock()
            rt._on_sell_fill_trailing(ticker)

        rt._kite.delete_gtt.assert_not_called()
        assert ticker not in rt._trailing_managers
        assert ticker not in rt._ticker_locked
        sync.assert_called_once()


# ── Part B: _cleanup_stale_protection ────────────────────────────

class TestCleanupStaleProtection:
    @pytest.mark.asyncio
    async def test_provably_gone_with_gtt_is_cleaned(self):
        rt = _make_runtime()
        ticker = "WIPRO.NS"
        rt._ticker_locked.add(ticker)
        rt._trailing_managers[ticker] = MagicMock()
        rt._gtt_ids[ticker] = 321
        rt._ws_hwm[ticker] = 500.0
        _set_broker(rt, net=[], holdings=[])
        rt._kite.get_gtts.return_value = [_gtt("WIPRO", 321)]

        with patch("backend.cache.get_cache") as mock_gc, \
                patch.object(rt, "_sync_ticker_lock_to_redis"):
            mock_gc.return_value = MagicMock()
            await rt._cleanup_stale_protection()

        rt._kite.delete_gtt.assert_called_once_with(321)
        assert ticker not in rt._ticker_locked
        assert ticker not in rt._trailing_managers
        assert ticker not in rt._gtt_ids
        assert ticker not in rt._ws_hwm
        types = [e["type"] for e in rt._events]
        assert "stale_protection_cleaned" in types

    @pytest.mark.asyncio
    async def test_held_with_gtt_is_not_touched(self):
        # SAFETY: net != 0 -> HELD -> never cancel.
        rt = _make_runtime()
        ticker = "RELIANCE.NS"
        rt._ticker_locked.add(ticker)
        rt._trailing_managers[ticker] = MagicMock()
        rt._gtt_ids[ticker] = 777
        _set_broker(
            rt,
            net=[{"tradingsymbol": "RELIANCE", "quantity": 10}],
            holdings=[],
        )
        rt._kite.get_gtts.return_value = [_gtt("RELIANCE", 777)]

        with patch("backend.cache.get_cache") as mock_gc, \
                patch.object(rt, "_sync_ticker_lock_to_redis"):
            mock_gc.return_value = MagicMock()
            await rt._cleanup_stale_protection()

        rt._kite.delete_gtt.assert_not_called()
        assert ticker in rt._ticker_locked
        assert ticker in rt._trailing_managers
        assert ticker in rt._gtt_ids

    @pytest.mark.asyncio
    async def test_broker_unreadable_cleans_nothing(self):
        # FAIL-SAFE: positions/holdings read raises -> bail.
        rt = _make_runtime()
        ticker = "HDFC.NS"
        rt._ticker_locked.add(ticker)
        rt._trailing_managers[ticker] = MagicMock()
        rt._gtt_ids[ticker] = 9
        _set_broker(rt, raises=True)
        rt._kite.get_gtts.return_value = [_gtt("HDFC", 9)]

        with patch("backend.cache.get_cache") as mock_gc, \
                patch.object(rt, "_sync_ticker_lock_to_redis"):
            mock_gc.return_value = MagicMock()
            await rt._cleanup_stale_protection()

        rt._kite.delete_gtt.assert_not_called()
        assert ticker in rt._ticker_locked
        assert ticker in rt._trailing_managers
        types = [e["type"] for e in rt._events]
        assert "cleanup_skipped_broker_unreadable" in types

    @pytest.mark.asyncio
    async def test_provably_gone_no_gtt_clears_state_only(self):
        rt = _make_runtime()
        ticker = "SBIN.NS"
        rt._ticker_locked.add(ticker)
        rt._trailing_managers[ticker] = MagicMock()
        _set_broker(rt, net=[], holdings=[])
        rt._kite.get_gtts.return_value = []

        with patch("backend.cache.get_cache") as mock_gc, \
                patch.object(rt, "_sync_ticker_lock_to_redis"):
            mock_cache = MagicMock()
            mock_gc.return_value = mock_cache
            await rt._cleanup_stale_protection()

        rt._kite.delete_gtt.assert_not_called()
        assert ticker not in rt._ticker_locked
        assert ticker not in rt._trailing_managers
        mock_cache.invalidate_exact.assert_called()

    @pytest.mark.asyncio
    async def test_todays_cnc_buy_net_positive_treated_held(self):
        # REGRESSION GUARD (today's bug): net>0 but holdings=0 (a
        # CNC buy made today) MUST be treated as HELD.
        rt = _make_runtime()
        ticker = "ITC.NS"
        rt._ticker_locked.add(ticker)
        rt._trailing_managers[ticker] = MagicMock()
        rt._gtt_ids[ticker] = 4242
        _set_broker(
            rt,
            net=[{
                "tradingsymbol": "ITC",
                "quantity": 50,
                "product": "CNC",
            }],
            holdings=[{
                "tradingsymbol": "ITC",
                "quantity": 0,
                "t1_quantity": 0,
            }],
        )
        rt._kite.get_gtts.return_value = [_gtt("ITC", 4242)]

        with patch("backend.cache.get_cache") as mock_gc, \
                patch.object(rt, "_sync_ticker_lock_to_redis"):
            mock_gc.return_value = MagicMock()
            await rt._cleanup_stale_protection()

        rt._kite.delete_gtt.assert_not_called()
        assert ticker in rt._ticker_locked
        assert ticker in rt._gtt_ids

    @pytest.mark.asyncio
    async def test_t1_quantity_only_treated_held(self):
        # holdings quantity=0 but t1_quantity>0 -> HELD.
        rt = _make_runtime()
        ticker = "LT.NS"
        rt._ticker_locked.add(ticker)
        rt._gtt_ids[ticker] = 1212
        _set_broker(
            rt,
            net=[],
            holdings=[{
                "tradingsymbol": "LT",
                "quantity": 0,
                "t1_quantity": 5,
            }],
        )
        rt._kite.get_gtts.return_value = [_gtt("LT", 1212)]

        with patch("backend.cache.get_cache") as mock_gc, \
                patch.object(rt, "_sync_ticker_lock_to_redis"):
            mock_gc.return_value = MagicMock()
            await rt._cleanup_stale_protection()

        rt._kite.delete_gtt.assert_not_called()
        assert ticker in rt._ticker_locked
