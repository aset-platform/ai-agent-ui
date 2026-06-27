"""Three-phase GTT trailing stop state machine.

Pure module — no I/O, no side effects. Shared by backtest,
paper, and live runtimes.

Phase numbering
---------------
1   Hard stop — flat % below entry (mirrors v3 ``stop_loss_pct``).
15  Ratcheted (Phase 1.5) — one-time stop raise after the position
    crosses ``phase1_ratchet_trigger_pct`` unrealised gain.
    Reduces max loss from −stop_loss_pct to −phase1_ratchet_new_stop_pct.
2   ATR trail — stop ratchets up with the high-water mark once
    unrealised gain crosses ``trailing_trigger_pct``.

All four threshold fields in ``RiskPerTrade`` default to None,
which leaves v1/v2/v3 behaviour completely unchanged.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from backend.algo.strategy.ast import RiskPerTrade


class TrailingPhase(int, enum.Enum):
    HARD_STOP = 1
    RATCHETED = 15
    ATR_TRAIL = 2


@dataclass
class TrailingEvent:
    """Returned by ``on_price_update`` when something changes."""

    event_type: str    # "STOP_UPDATED" | "STOP_HIT"
    new_stop: float
    phase: TrailingPhase
    hwm: float


@dataclass
class _State:
    ticker: str
    entry_price: float
    phase: TrailingPhase
    current_stop: float
    hwm: float
    phase1_ratcheted: bool
    atr: float


class TrailingStopManager:
    """Per-position trailing stop state machine.

    Usage::

        mgr = TrailingStopManager(risk, fill_price, atr_14)

        # On each subsequent bar/tick — call with bar.low first
        # (stop-hit check) then bar.high (HWM update) in backtest.
        event = mgr.on_price_update(price)
        if event and event.event_type == "STOP_HIT":
            # close position at event.new_stop (or bar.low if gapped)
        elif event and event.event_type == "STOP_UPDATED":
            # ratchet GTT: new trigger = event.new_stop
    """

    def __init__(
        self,
        risk: RiskPerTrade,
        entry_price: float,
        atr: float,
        ticker: str = "",
    ) -> None:
        self._risk = risk
        initial_stop = entry_price * (
            1.0 - risk.stop_loss_pct / 100.0
        )
        self._state = _State(
            ticker=ticker,
            entry_price=entry_price,
            phase=TrailingPhase.HARD_STOP,
            current_stop=initial_stop,
            hwm=entry_price,
            phase1_ratcheted=False,
            atr=atr,
        )

    # ── public API ──────────────────────────────────────────────

    @property
    def state(self) -> _State:
        return self._state

    @property
    def current_stop(self) -> float:
        return self._state.current_stop

    def is_trailing_enabled(self) -> bool:
        """True when both ATR-trail fields are populated (v5+)."""
        r = self._risk
        return (
            r.trailing_trigger_pct is not None
            and r.trailing_atr_multiplier is not None
        )

    def on_price_update(self, price: float) -> TrailingEvent | None:
        """Process one price tick/bar.

        Call with bar.low first (stop-hit check), then bar.high
        (HWM update) in backtest. In paper/live call with current LTP.

        Returns a ``TrailingEvent`` when the stop level changes or is
        hit; returns ``None`` when nothing changes.
        """
        s = self._state
        r = self._risk

        old_stop = s.current_stop
        old_phase = s.phase
        old_hwm = s.hwm

        # Advance HWM
        if price > s.hwm:
            s.hwm = price

        unrealised_pct = (
            (price - s.entry_price) / s.entry_price * 100.0
        )

        # Phase 1 → 1.5: one-time ratchet
        if (
            s.phase == TrailingPhase.HARD_STOP
            and not s.phase1_ratcheted
            and r.phase1_ratchet_trigger_pct is not None
            and r.phase1_ratchet_new_stop_pct is not None
            and unrealised_pct >= r.phase1_ratchet_trigger_pct
        ):
            s.phase = TrailingPhase.RATCHETED
            s.phase1_ratcheted = True
            s.current_stop = s.entry_price * (
                1.0 - r.phase1_ratchet_new_stop_pct / 100.0
            )

        # Phase 1 or 1.5 → 2: ATR trail kicks in
        if (
            s.phase in (TrailingPhase.HARD_STOP, TrailingPhase.RATCHETED)
            and r.trailing_trigger_pct is not None
            and r.trailing_atr_multiplier is not None
            and unrealised_pct >= r.trailing_trigger_pct
        ):
            s.phase = TrailingPhase.ATR_TRAIL
            trail_width = s.atr * r.trailing_atr_multiplier
            candidate = s.hwm - trail_width
            s.current_stop = max(s.current_stop, candidate)

        # Phase 2: ratchet stop up whenever HWM advances
        elif (
            s.phase == TrailingPhase.ATR_TRAIL
            and s.hwm > old_hwm
            and r.trailing_atr_multiplier is not None
        ):
            trail_width = s.atr * r.trailing_atr_multiplier
            candidate = s.hwm - trail_width
            if candidate > s.current_stop:
                s.current_stop = candidate

        # Stop-hit check AFTER all ratchet logic
        if price <= s.current_stop:
            return TrailingEvent(
                event_type="STOP_HIT",
                new_stop=s.current_stop,
                phase=s.phase,
                hwm=s.hwm,
            )

        # Return STOP_UPDATED when stop or phase changed
        if s.current_stop != old_stop or s.phase != old_phase:
            return TrailingEvent(
                event_type="STOP_UPDATED",
                new_stop=s.current_stop,
                phase=s.phase,
                hwm=s.hwm,
            )

        return None

    # ── Redis serialisation ─────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialise state to a JSON-safe dict for Redis storage."""
        s = self._state
        return {
            "ticker": s.ticker,
            "entry_price": s.entry_price,
            "phase": int(s.phase.value),
            "current_stop": s.current_stop,
            "hwm": s.hwm,
            "phase1_ratcheted": s.phase1_ratcheted,
            "atr": s.atr,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict,
        risk: RiskPerTrade,
    ) -> "TrailingStopManager":
        """Restore from a dict previously returned by ``to_dict``."""
        mgr: TrailingStopManager = cls.__new__(cls)
        mgr._risk = risk
        mgr._state = _State(
            ticker=data.get("ticker", ""),
            entry_price=float(data["entry_price"]),
            phase=TrailingPhase(int(data["phase"])),
            current_stop=float(data["current_stop"]),
            hwm=float(data["hwm"]),
            phase1_ratcheted=bool(data["phase1_ratcheted"]),
            atr=float(data["atr"]),
        )
        return mgr
