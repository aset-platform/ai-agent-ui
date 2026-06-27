# backend/algo/backtest/tests/test_execution_simulator.py
from decimal import Decimal

from backend.algo.backtest.execution_simulator import (
    ExecutionSimulator,
    ExitDecision,
)
from backend.algo.strategy.ast import RiskPerTrade


def _risk():
    return RiskPerTrade(
        stop_loss_pct=5.0, max_qty=100,
        trailing_trigger_pct=3.0, trailing_atr_multiplier=2.0,
    )


def test_no_decision_until_stop_crossed():
    sim = ExecutionSimulator(_risk())
    sim.on_buy_fill("X.NS", entry_price=100.0, atr=1.0)
    # bar well above the 5% hard stop (95) -> no exit, HWM advances
    assert sim.evaluate_bar("X.NS", Decimal("99"), Decimal("104")) is None
    assert sim.has("X.NS")


def test_stop_hit_returns_decision_at_current_stop():
    sim = ExecutionSimulator(_risk())
    sim.on_buy_fill("X.NS", entry_price=100.0, atr=1.0)
    dec = sim.evaluate_bar("X.NS", Decimal("94"), Decimal("96"))
    assert isinstance(dec, ExitDecision)
    assert dec.exit_reason == "phase1_stop"
    assert dec.phase == 1
    # trigger is the manager's hard stop = 100 * (1 - 0.05) = 95.0
    assert dec.trigger_price == Decimal("95.0")


def test_unknown_ticker_returns_none():
    sim = ExecutionSimulator(_risk())
    assert sim.evaluate_bar("Z.NS", Decimal("1"), Decimal("2")) is None


def test_reason_for_phase_all_branches():
    from backend.algo.backtest.execution_simulator import (
        _reason_for_phase,
    )
    assert _reason_for_phase(2) == "trail_stop"
    assert _reason_for_phase(15) == "phase1_ratchet"
    assert _reason_for_phase(1) == "phase1_stop"
    assert _reason_for_phase(99) == "phase1_stop"
