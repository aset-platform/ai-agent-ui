"""Shared execution-clock simulator for backtest/walkforward (and,
later, paper). Owns per-ticker TrailingStopManager lifecycle and
returns a pure ExitDecision when an exit triggers on a bar. The same
TrailingStopManager class drives live, so behaviour cannot diverge.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.algo.backtest.trailing_stop_manager import (
    TrailingStopManager,
)
from backend.algo.strategy.ast import RiskPerTrade


@dataclass(frozen=True)
class ExitDecision:
    ticker: str
    exit_reason: str
    trigger_price: Decimal
    phase: int
    hwm: float


def _reason_for_phase(phase_value: int) -> str:
    if phase_value == 2:
        return "trail_stop"
    if phase_value == 15:
        return "phase1_ratchet"
    return "phase1_stop"


class ExecutionSimulator:
    def __init__(self, risk: RiskPerTrade) -> None:
        self._risk = risk
        self._mgrs: dict[str, TrailingStopManager] = {}

    def on_buy_fill(
        self, ticker: str, entry_price: float, atr: float
    ) -> None:
        self._mgrs[ticker] = TrailingStopManager(
            self._risk, entry_price, atr, ticker
        )

    def drop(self, ticker: str) -> None:
        self._mgrs.pop(ticker, None)

    def has(self, ticker: str) -> bool:
        return ticker in self._mgrs

    def evaluate_bar(
        self, ticker: str, low: Decimal, high: Decimal
    ) -> ExitDecision | None:
        """LOW-then-HIGH per bar. Returns an ExitDecision on STOP_HIT."""
        mgr = self._mgrs.get(ticker)
        if mgr is None:
            return None
        if float(low) <= mgr.current_stop:
            ev = mgr.on_price_update(float(low))
            if ev is None or ev.event_type != "STOP_HIT":
                mgr.on_price_update(float(high))
                return None
        else:
            mgr.on_price_update(float(high))
            return None
        return ExitDecision(
            ticker=ticker,
            exit_reason=_reason_for_phase(ev.phase.value),
            trigger_price=Decimal(str(ev.new_stop)),
            phase=ev.phase.value,
            hwm=ev.hwm,
        )
