"""Tests for Task 3.2 — preserve real ``opened_at`` on restart and
refuse a GTT when the recovered entry price is missing/zero.

Two bugs this guards:

1. On restart/fill-sync a re-injected position used to get
   ``Fill.fill_date = today``. That reset ``opened_at`` so a
   ``max_holding_days`` time-stop could NEVER fire for any position
   that survived a restart. We now thread the ORIGINAL fill date
   (``filled_at`` / ``submitted_at`` from the in-flight entry) into
   ``Fill.fill_date`` in both injection paths
   (``_sync_fills_from_pg`` and ``_recover_unhydrated_positions``).

2. A recovered position with a missing / ``<= 0`` average entry price
   used to get a GTT trigger computed off ₹0 — a garbage protective
   stop. We now REFUSE the GTT and emit a loud
   ``gtt_skipped_no_entry_price`` event instead.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from backend.algo.backtest.time_stop_monitor import (
    check_time_stop_triggers,
)
from backend.algo.live.runtime import LiveRuntime


# ── Helpers ──────────────────────────────────────────────────────────


def _make_risk(*, trailing=True, max_holding_days=3):
    r = MagicMock()
    r.stop_loss_pct = 5.0
    r.trailing_trigger_pct = 5.0 if trailing else None
    r.trailing_atr_multiplier = 1.5 if trailing else None
    r.phase1_ratchet_trigger_pct = 2.0
    r.phase1_ratchet_new_stop_pct = 3.0
    r.max_holding_days = max_holding_days
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


def _make_runtime(*, trailing=True, max_holding_days=3) -> LiveRuntime:
    risk = _make_risk(
        trailing=trailing, max_holding_days=max_holding_days
    )
    strategy = _make_strategy(risk)

    kite = MagicMock()
    kite._dry_run = False
    kite.place_gtt.return_value = 42
    kite.delete_gtt.return_value = None
    kite.get_gtts.return_value = []

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


def _iso_days_ago(n: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=n)
    ).isoformat()


def _filled_buy(
    sym="INFY", qty=5, price=1000.0, filled_at=None, submitted_at=None
):
    e = {
        "status": "filled",
        "kite_order_id": "ko-1",
        "side": "BUY",
        "symbol": sym,
        "qty": qty,
        "fill_price": price,
        "reservation_id": None,
    }
    if filled_at is not None:
        e["filled_at"] = filled_at
    if submitted_at is not None:
        e["submitted_at"] = submitted_at
    return e


# ── Tests ────────────────────────────────────────────────────────────


class TestSyncFillsOpenedAt:
    def test_fill_sync_preserves_original_fill_date(self):
        """A fill that filled 5 days ago keeps opened_at = 5 days ago,
        so a max_holding_days=3 time-stop FIRES (not reset to today)."""
        rt = _make_runtime(trailing=True, max_holding_days=3)
        rt._factor_cache[("INFY.NS", date.today())] = {
            "atr_14": Decimal("20.0"),
        }
        five_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=5)
        ).date()
        rt._caps_repo.get_in_flight = AsyncMock(
            return_value=[
                _filled_buy(filled_at=_iso_days_ago(5)),
            ],
        )
        with patch.object(rt, "_save_trailing_state"), patch.object(
            rt, "_sync_ticker_lock_to_redis"
        ):
            asyncio.run(rt._sync_fills_from_pg())

        pos = rt._positions.open_positions()["INFY.NS"]
        assert pos.opened_at == five_days_ago

        triggers = check_time_stop_triggers(
            open_positions={
                t: {"qty": p.qty, "opened_at": p.opened_at}
                for t, p in rt._positions.open_positions().items()
            },
            current_date=date.today(),
            max_holding_days=3,
        )
        assert any(t.ticker == "INFY.NS" for t in triggers)

    def test_fill_sync_falls_back_to_submitted_at(self):
        rt = _make_runtime(trailing=True, max_holding_days=3)
        rt._factor_cache[("INFY.NS", date.today())] = {
            "atr_14": Decimal("20.0"),
        }
        four_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=4)
        ).date()
        rt._caps_repo.get_in_flight = AsyncMock(
            return_value=[
                _filled_buy(submitted_at=_iso_days_ago(4)),
            ],
        )
        with patch.object(rt, "_save_trailing_state"), patch.object(
            rt, "_sync_ticker_lock_to_redis"
        ):
            asyncio.run(rt._sync_fills_from_pg())

        pos = rt._positions.open_positions()["INFY.NS"]
        assert pos.opened_at == four_days_ago


class TestRecoverOpenedAt:
    def test_recovery_preserves_original_open_date(self):
        rt = _make_runtime(trailing=True, max_holding_days=3)
        rt._ticker_locked.add("INFY.NS")
        five_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=5)
        ).date()
        rt._caps_repo.get_filled_buys_from_previous_runs = AsyncMock(
            return_value={
                "INFY.NS": {
                    "fill_price": Decimal("1000.0"),
                    "qty": 5,
                    "fill_date": five_days_ago,
                },
            },
        )
        asyncio.run(rt._recover_unhydrated_positions())

        pos = rt._positions.open_positions()["INFY.NS"]
        assert pos.opened_at == five_days_ago

        triggers = check_time_stop_triggers(
            open_positions={
                "INFY.NS": {"qty": pos.qty, "opened_at": pos.opened_at},
            },
            current_date=date.today(),
            max_holding_days=3,
        )
        assert any(t.ticker == "INFY.NS" for t in triggers)


class TestZeroEntryGttRefusal:
    def test_ensure_gtt_skips_zero_avg_price(self):
        """avg_price <= 0 → no GTT placed, loud skip event emitted."""
        rt = _make_runtime(trailing=True)
        from backend.algo.backtest.types import Fill

        rt._positions.apply_fill(
            Fill(
                intent_id=uuid4(),
                ticker="INFY.NS",
                side="BUY",
                qty=5,
                fill_price=Decimal("0"),
                fill_date=date.today(),
                fees_inr=Decimal("0"),
                fee_rates_version="test",
            )
        )
        with patch.object(rt, "_save_trailing_state"):
            asyncio.run(rt._ensure_gtts_for_hydrated_positions())

        rt._kite.place_gtt.assert_not_called()
        assert "INFY.NS" not in rt._trailing_managers
        types = [e.get("type") for e in rt._events]
        assert "gtt_skipped_no_entry_price" in types

    def test_ensure_gtt_places_for_positive_avg_price(self):
        rt = _make_runtime(trailing=True)
        rt._factor_cache[("INFY.NS", date.today())] = {
            "atr_14": Decimal("20.0"),
        }
        from backend.algo.backtest.types import Fill

        rt._positions.apply_fill(
            Fill(
                intent_id=uuid4(),
                ticker="INFY.NS",
                side="BUY",
                qty=5,
                fill_price=Decimal("1000.0"),
                fill_date=date.today(),
                fees_inr=Decimal("0"),
                fee_rates_version="test",
            )
        )
        with patch.object(rt, "_save_trailing_state"):
            asyncio.run(rt._ensure_gtts_for_hydrated_positions())

        rt._kite.place_gtt.assert_called_once()
        assert "INFY.NS" in rt._trailing_managers
        types = [e.get("type") for e in rt._events]
        assert "gtt_skipped_no_entry_price" not in types
