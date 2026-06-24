"""Task 3.3: verify held-position GTTs against live Kite on hydration.

Kite's live GTT book is the SOURCE OF TRUTH for every held position
(``open_positions()`` with ``qty > 0``), regardless of whether the
ticker is already in ``_trailing_managers``. A Redis-restored manager
whose ``gtt_id`` is dead on Kite must get a fresh GTT; a position with a
live Kite GTT must be registered with the real id and never
double-placed.

Scenarios (mirrors the brief):
1. Held position, Redis-restored stale gtt_id NOT in Kite book ->
   place_gtt called once; _gtt_ids updated to the new id.
2. Held position whose gtt_id IS active on Kite -> place_gtt NOT called;
   manager registered with the Kite id.
3. Held position, no Redis manager, no Kite GTT -> place_gtt called.
4. Held position, no Redis manager, active Kite GTT exists -> registered
   with the Kite id, place_gtt NOT called.
5. get_gtts raises -> place_gtt NOT called; gtt_verification_failed
   event emitted; no crash.
6. avg_price <= 0 on an unprotected held position -> refused
   (gtt_skipped_no_entry_price), place_gtt NOT called.
7. Ticker in _trailing_managers/_ticker_locked but qty 0 (not held) ->
   place_gtt NOT called (scoped to held).
"""
import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from backend.algo.backtest.trailing_stop_manager import (
    TrailingStopManager,
)
from backend.algo.live.runtime import LiveRuntime
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
    strategy = MagicMock()
    strategy.id = uuid4()
    strategy.product = "CNC"
    strategy.risk.per_trade = _v5_risk()

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


def _pos(qty: int, avg_price: float) -> MagicMock:
    p = MagicMock()
    p.qty = qty
    p.avg_price = Decimal(str(avg_price))
    return p


def _active_gtt(symbol: str, gtt_id: int, trigger: float) -> dict:
    return {
        "id": gtt_id,
        "status": "active",
        "condition": {
            "tradingsymbol": symbol,
            "trigger_values": [trigger],
        },
    }


def _set_positions(rt: LiveRuntime, positions: dict) -> None:
    rt._positions.open_positions = MagicMock(return_value=positions)


def _seed_atr(rt: LiveRuntime, ticker: str) -> None:
    rt._factor_cache[(ticker, date.today())] = {"atr_14": Decimal("20")}


def _run(rt: LiveRuntime) -> None:
    with patch.object(rt, "_save_trailing_state"), patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[],
    ):
        asyncio.run(rt._ensure_gtts_for_hydrated_positions())


# ── 1. Redis-restored stale gtt_id NOT in Kite book -> re-place ──────

def test_stale_redis_gtt_not_on_kite_replaces():
    rt = _make_runtime()
    _seed_atr(rt, "IDEA.NS")
    _set_positions(rt, {"IDEA.NS": _pos(100, 12.0)})
    # Redis restored a manager + a stale gtt_id (dead on Kite).
    mgr = TrailingStopManager(
        rt._strategy.risk.per_trade, entry_price=12.0, atr=0.5,
    )
    rt._trailing_managers["IDEA.NS"] = mgr
    rt._gtt_ids["IDEA.NS"] = 324876082
    rt._kite.get_gtts.return_value = []  # Kite has NO GTT for IDEA
    rt._kite.place_gtt.return_value = 555

    _run(rt)

    rt._kite.place_gtt.assert_called_once()
    assert rt._gtt_ids["IDEA.NS"] == 555


# ── 2. gtt_id IS active on Kite -> no duplicate, register Kite id ────

def test_live_kite_gtt_registers_no_duplicate():
    rt = _make_runtime()
    _seed_atr(rt, "SHAILY.NS")
    _set_positions(rt, {"SHAILY.NS": _pos(50, 600.0)})
    mgr = TrailingStopManager(
        rt._strategy.risk.per_trade, entry_price=600.0, atr=5.0,
    )
    rt._trailing_managers["SHAILY.NS"] = mgr
    rt._gtt_ids["SHAILY.NS"] = 111  # Redis id differs from Kite
    rt._kite.get_gtts.return_value = [
        _active_gtt("SHAILY", 777, 570.0),
    ]

    _run(rt)

    rt._kite.place_gtt.assert_not_called()
    assert rt._gtt_ids["SHAILY.NS"] == 777
    assert "SHAILY.NS" in rt._trailing_managers


# ── 3. No Redis manager, no Kite GTT -> place fresh ─────────────────

def test_no_manager_no_kite_gtt_places():
    rt = _make_runtime()
    _seed_atr(rt, "HSCL.NS")
    _set_positions(rt, {"HSCL.NS": _pos(20, 300.0)})
    rt._kite.get_gtts.return_value = []
    rt._kite.place_gtt.return_value = 888

    _run(rt)

    rt._kite.place_gtt.assert_called_once()
    assert rt._gtt_ids["HSCL.NS"] == 888
    assert "HSCL.NS" in rt._trailing_managers


# ── 4. No Redis manager, active Kite GTT exists -> register only ────

def test_no_manager_but_kite_gtt_registers():
    rt = _make_runtime()
    _seed_atr(rt, "NSLNISP.NS")
    _set_positions(rt, {"NSLNISP.NS": _pos(10, 45.0)})
    rt._kite.get_gtts.return_value = [
        _active_gtt("NSLNISP", 444, 42.0),
    ]

    _run(rt)

    rt._kite.place_gtt.assert_not_called()
    assert rt._gtt_ids["NSLNISP.NS"] == 444
    assert "NSLNISP.NS" in rt._trailing_managers


# ── 5. get_gtts raises -> fail-visible, place nothing ───────────────

def test_get_gtts_failure_emits_event_no_place():
    rt = _make_runtime()
    _seed_atr(rt, "IDEA.NS")
    _set_positions(rt, {"IDEA.NS": _pos(100, 12.0)})
    mgr = TrailingStopManager(
        rt._strategy.risk.per_trade, entry_price=12.0, atr=0.5,
    )
    rt._trailing_managers["IDEA.NS"] = mgr
    rt._gtt_ids["IDEA.NS"] = 324876082
    rt._kite.get_gtts.side_effect = RuntimeError("Kite unreadable")

    _run(rt)

    rt._kite.place_gtt.assert_not_called()
    types = [e["type"] for e in rt._events]
    assert "gtt_verification_failed" in types
    # managers preserved
    assert "IDEA.NS" in rt._trailing_managers


# ── 6. avg_price <= 0 unprotected -> refuse (Task 3.2 preserved) ────

def test_zero_avg_price_refused():
    rt = _make_runtime()
    _seed_atr(rt, "BADP.NS")
    _set_positions(rt, {"BADP.NS": _pos(10, 0.0)})
    rt._kite.get_gtts.return_value = []  # unprotected

    _run(rt)

    rt._kite.place_gtt.assert_not_called()
    types = [e["type"] for e in rt._events]
    assert "gtt_skipped_no_entry_price" in types


# ── 7. qty 0 phantom in managers/locked -> scoped out ───────────────

def test_qty_zero_phantom_not_placed():
    rt = _make_runtime()
    _set_positions(rt, {})  # nothing actually held
    rt._ticker_locked.add("GHOST.NS")
    rt._trailing_managers["GHOST.NS"] = MagicMock()
    rt._gtt_ids["GHOST.NS"] = 12345
    rt._kite.get_gtts.return_value = []

    _run(rt)

    rt._kite.place_gtt.assert_not_called()
