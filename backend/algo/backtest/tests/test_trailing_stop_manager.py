"""Unit tests for TrailingStopManager pure state machine."""
from backend.algo.strategy.ast import RiskPerTrade
from backend.algo.backtest.trailing_stop_manager import (
    TrailingPhase,
    TrailingStopManager,
)

_RISK_V5 = RiskPerTrade(
    stop_loss_pct=5.0,
    max_qty=10000,
    phase1_ratchet_trigger_pct=2.0,
    phase1_ratchet_new_stop_pct=3.0,
    trailing_trigger_pct=5.0,
    trailing_atr_multiplier=1.5,
)

_RISK_V3 = RiskPerTrade(stop_loss_pct=5.0, max_qty=10000)


def _mgr(
    entry: float = 5000.0,
    atr: float = 125.0,
    ticker: str = "",
) -> TrailingStopManager:
    return TrailingStopManager(
        _RISK_V5, entry_price=entry, atr=atr, ticker=ticker
    )


# ── Phase 1: hard stop ──────────────────────────────────────────


class TestPhase1HardStop:
    def test_initial_stop_is_entry_minus_stop_pct(self):
        mgr = _mgr(entry=5000.0)
        assert abs(mgr.current_stop - 4750.0) < 0.01

    def test_no_event_below_ratchet_trigger(self):
        mgr = _mgr(entry=5000.0)
        ev = mgr.on_price_update(5050.0)  # +1%, below +2% ratchet
        assert ev is None
        assert abs(mgr.current_stop - 4750.0) < 0.01

    def test_stop_hit_below_stop_level(self):
        mgr = _mgr(entry=5000.0)
        ev = mgr.on_price_update(4730.0)  # below 4750 stop
        assert ev is not None
        assert ev.event_type == "STOP_HIT"
        assert ev.phase == TrailingPhase.HARD_STOP

    def test_stop_hit_exactly_at_stop(self):
        mgr = _mgr(entry=5000.0)
        ev = mgr.on_price_update(4750.0)
        assert ev is not None
        assert ev.event_type == "STOP_HIT"

    def test_price_above_stop_no_event(self):
        mgr = _mgr(entry=5000.0)
        ev = mgr.on_price_update(4800.0)  # above 4750, below ratchet
        assert ev is None


# ── Phase 1.5: one-time ratchet ─────────────────────────────────


class TestPhase1Ratchet:
    def test_ratchet_fires_at_trigger(self):
        mgr = _mgr(entry=5000.0)
        ev = mgr.on_price_update(5100.0)  # +2%
        assert ev is not None
        assert ev.event_type == "STOP_UPDATED"
        assert ev.phase == TrailingPhase.RATCHETED
        # new stop = entry * (1 - 3/100) = 4850
        assert abs(ev.new_stop - 4850.0) < 0.01
        assert abs(mgr.current_stop - 4850.0) < 0.01

    def test_ratchet_fires_only_once(self):
        mgr = _mgr(entry=5000.0)
        mgr.on_price_update(5100.0)   # fires ratchet → RATCHETED
        ev2 = mgr.on_price_update(5120.0)  # still below phase-2 trigger
        # No additional STOP_UPDATED from ratchet logic
        assert ev2 is None

    def test_ratchet_stop_higher_than_phase1(self):
        mgr = _mgr(entry=5000.0)
        mgr.on_price_update(5100.0)
        assert mgr.current_stop > 4750.0   # 4850 > 4750

    def test_ratchet_phase_recorded(self):
        mgr = _mgr(entry=5000.0)
        mgr.on_price_update(5100.0)
        assert mgr.state.phase == TrailingPhase.RATCHETED
        assert mgr.state.phase1_ratcheted is True

    def test_ratcheted_stop_can_be_hit(self):
        mgr = _mgr(entry=5000.0)
        mgr.on_price_update(5100.0)   # ratchet to 4850
        ev = mgr.on_price_update(4840.0)  # below 4850
        assert ev is not None
        assert ev.event_type == "STOP_HIT"
        assert ev.phase == TrailingPhase.RATCHETED


# ── Phase 2: ATR trailing ────────────────────────────────────────


class TestPhase2AttrTrailing:
    def test_phase2_kicks_in_at_trailing_trigger(self):
        # ATR=125, multiplier=1.5 → trail_width=187.5
        mgr = _mgr(entry=5000.0, atr=125.0)
        ev = mgr.on_price_update(5250.0)  # +5%
        assert ev is not None
        assert ev.phase == TrailingPhase.ATR_TRAIL
        # new_stop = max(4850, 5250-187.5) = max(4850, 5062.5) = 5062.5
        assert abs(mgr.current_stop - 5062.5) < 0.01

    def test_phase2_ratchets_up_with_hwm(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5250.0)    # enter phase 2, stop=5062.5
        ev = mgr.on_price_update(5400.0)  # new HWM
        assert ev is not None
        assert ev.event_type == "STOP_UPDATED"
        # new_stop = 5400 - 187.5 = 5212.5
        assert abs(mgr.current_stop - 5212.5) < 0.01

    def test_phase2_stop_never_decreases(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5250.0)   # phase 2, stop=5062.5
        mgr.on_price_update(5400.0)   # stop=5212.5
        ev = mgr.on_price_update(5300.0)  # below HWM — no new event
        assert ev is None
        assert abs(mgr.current_stop - 5212.5) < 0.01

    def test_phase2_stop_hit(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5250.0)   # stop=5062.5
        ev = mgr.on_price_update(5050.0)  # below 5062.5
        assert ev is not None
        assert ev.event_type == "STOP_HIT"
        assert ev.phase == TrailingPhase.ATR_TRAIL

    def test_phase2_can_enter_without_prior_ratchet(self):
        # Jumps straight past both +2% ratchet and +5% ATR trigger in one bar
        mgr = _mgr(entry=5000.0, atr=125.0)
        ev = mgr.on_price_update(5300.0)  # +6%, jumps past both triggers
        assert ev is not None
        assert ev.phase == TrailingPhase.ATR_TRAIL

    def test_hwm_tracked_correctly(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5250.0)
        mgr.on_price_update(5400.0)
        assert abs(mgr.state.hwm - 5400.0) < 0.01


# ── Enabled / disabled ───────────────────────────────────────────


class TestTrailingEnabled:
    def test_disabled_for_v3_risk(self):
        mgr = TrailingStopManager(
            _RISK_V3, entry_price=5000.0, atr=125.0
        )
        assert not mgr.is_trailing_enabled()

    def test_enabled_for_v5_risk(self):
        mgr = _mgr()
        assert mgr.is_trailing_enabled()

    def test_disabled_when_only_trigger_set(self):
        risk = RiskPerTrade(
            stop_loss_pct=5.0,
            max_qty=10000,
            trailing_trigger_pct=5.0,
            # trailing_atr_multiplier intentionally omitted
        )
        mgr = TrailingStopManager(risk, entry_price=5000.0, atr=125.0)
        assert not mgr.is_trailing_enabled()


# ── Redis serialisation ───────────────────────────────────────────


class TestSerialization:
    def test_roundtrip_phase1(self):
        mgr = _mgr(entry=5000.0, atr=125.0, ticker="RELIANCE.NS")
        d = mgr.to_dict()
        mgr2 = TrailingStopManager.from_dict(d, _RISK_V5)
        assert abs(mgr2.current_stop - mgr.current_stop) < 0.001
        assert mgr2.state.phase == mgr.state.phase
        assert mgr2.state.hwm == mgr.state.hwm
        assert mgr2.state.ticker == "RELIANCE.NS"

    def test_roundtrip_phase2(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5100.0)   # ratchet
        mgr.on_price_update(5250.0)   # phase 2
        mgr.on_price_update(5400.0)   # ratchet up
        d = mgr.to_dict()
        mgr2 = TrailingStopManager.from_dict(d, _RISK_V5)
        assert mgr2.state.phase == TrailingPhase.ATR_TRAIL
        assert abs(mgr2.current_stop - 5212.5) < 0.01

    def test_roundtrip_preserves_phase1_ratcheted_flag(self):
        mgr = _mgr(entry=5000.0, atr=125.0)
        mgr.on_price_update(5100.0)  # ratchet fires
        d = mgr.to_dict()
        mgr2 = TrailingStopManager.from_dict(d, _RISK_V5)
        # Ratchet must NOT fire again after restore
        ev = mgr2.on_price_update(5110.0)  # +2.2% — below phase-2 trigger
        # Only STOP_UPDATED would be ratchet; phase-2 hasn't triggered
        assert ev is None

    def test_to_dict_contains_required_keys(self):
        mgr = _mgr()
        d = mgr.to_dict()
        for key in (
            "ticker", "entry_price", "phase", "current_stop",
            "hwm", "phase1_ratcheted", "atr",
        ):
            assert key in d, f"missing key: {key}"

    def test_phase_value_is_int_in_dict(self):
        mgr = _mgr()
        d = mgr.to_dict()
        assert isinstance(d["phase"], int)
