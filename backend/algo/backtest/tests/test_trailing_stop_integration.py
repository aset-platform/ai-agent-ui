"""Backtest runner — trailing stop (v5) integration tests.

Exercises the three-phase trailing stop path in run_backtest():

  1. v3 strategy (no v5 fields) → flat stop path unchanged.
  2. v5 phase1_stop: price drops below initial hard stop.
  3. v5 phase1_ratchet: ratchet fires at +2 %, then price
     falls through the higher ratcheted stop.
  4. v5 trail_stop: ATR trail kicks in at +5 %, then price
     drops through the trailing stop.
  5. phase1_stop / phase1_ratchet trigger cooldown; trail_stop
     does not (verified via cooldown_monitor._FAILED_EXIT_REASONS).
  6. Backward compat: v3 strategy still fires plain stop_loss
     (flat %-stop path) even when trailing stop field is absent.

Fixture: 25 daily bars — first 20 are warmup so ``atr_14``
settles; bars 20-24 are the test period (period_start =
bar-20 date).  All warmup bars are flat at 100 with
high=101, low=99 so ATR converges to ≈2.0.

The strategy evaluates unconditional ``{type: buy, qty: {shares: 10}}``.
SimBroker T+1 semantics: signal at bar N fills at bar N+1 open.

Trailing parameters chosen so they fire cleanly within 4
test bars:
  stop_loss_pct=5        → initial hard stop = 95 (entry 100)
  phase1_ratchet_trigger_pct=2  → ratchets at 102
  phase1_ratchet_new_stop_pct=3 → ratcheted stop = 97
  trailing_trigger_pct=5        → ATR trail at 105
  trailing_atr_multiplier=1.5   → trail_width ≈ 3.0 (ATR≈2)
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.strategy.ast import parse_strategy

# ── Shared bar fixture ───────────────────────────────────────────

_BASE_DATE = date(2026, 3, 1)   # bar 0
_PERIOD_START = _BASE_DATE + timedelta(days=20)   # bar 20
_PERIOD_END = _BASE_DATE + timedelta(days=25)     # bar 25 (exclusive)

_ATR_WARMUP_CLOSE = Decimal("100")

# Scenario A — phase1_stop: price drops to 90 on bar 21 (first
# test bar after the T+1 BUY fill), below the 95 hard stop.
_CLOSES_PHASE1_STOP = [
    # bar 0-19 warmup flat at 100
    *([_ATR_WARMUP_CLOSE] * 20),
    # bar 20: BUY signals here; SimBroker fills at bar 21 open
    Decimal("100"),
    # bar 21: drop to 90, below stop at 95
    Decimal("90"),
    # bars 22-24 irrelevant
    Decimal("90"), Decimal("90"), Decimal("90"),
]

# Scenario B — phase1_ratchet: price rises to 102 (ratchet fires,
# stop→97), then drops to 95.8 (high=97.5, low=95.8 → low≤97).
_CLOSES_PHASE1_RATCHET = [
    *([_ATR_WARMUP_CLOSE] * 20),
    Decimal("100"),           # bar 20: BUY signal
    Decimal("102"),           # bar 21: +2 % → ratchet fires (stop 95→97)
    Decimal("96"),            # bar 22: low ≤ 97 → ratcheted stop hit
    Decimal("96"), Decimal("96"),
]

# Scenario C — trail_stop:
#   bar 21: +3 % → ratchet (stop 95→97)
#   bar 22: +6 % → phase 2 (stop = max(97, 106-3)=103)
#   bar 23: high=108 → HWM ratchet (stop=108-3=105)
#   bar 24: low=102 ≤ 105 → trail_stop fires
_CLOSES_TRAIL_STOP = [
    *([_ATR_WARMUP_CLOSE] * 20),
    Decimal("100"),   # bar 20: BUY signal
    Decimal("103"),   # bar 21: +3 % → ratchet (stop→97)
    Decimal("106"),   # bar 22: +6 % → phase 2 (stop→103)
    Decimal("108"),   # bar 23: HWM ratchet (stop→105)
    Decimal("102"),   # bar 24: low=100 ≤ 105 → trail_stop
]


_FIXED_OPEN = Decimal("100")


def _gen_bars(closes: list[Decimal], ticker: str = "FAKE.NS") -> list[BarData]:
    """Build BarData list.

    open=100 on every bar so SimBroker T+1 fills always
    price the BUY at 100, regardless of close.  ATR is
    seeded from the warmup bars' high/low spread (≈2).
    """
    bars = []
    for i, close in enumerate(closes):
        d = _BASE_DATE + timedelta(days=i)
        bars.append(BarData(
            ticker=ticker,
            date=d,
            open=_FIXED_OPEN,
            high=max(_FIXED_OPEN, close) + Decimal("1"),
            low=min(_FIXED_OPEN, close) - Decimal("1"),
            close=close,
            volume=10_000,
        ))
    return bars


def _v5_strategy() -> dict:
    return {
        "id": str(uuid4()),
        "name": "v5 trailing",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {"type": "bar_close", "interval": "1d", "time": "15:25 IST"},
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": 10}},
        "risk": {
            "per_trade": {
                "stop_loss_pct": 5.0,
                "max_qty": 1000,
                "phase1_ratchet_trigger_pct": 2.0,
                "phase1_ratchet_new_stop_pct": 3.0,
                "trailing_trigger_pct": 5.0,
                "trailing_atr_multiplier": 1.5,
            },
            "portfolio": {
                "max_exposure_pct": 100,
                "max_concentration_pct": 100,
            },
            "daily": {"max_loss_pct": 50, "max_open_positions": 10},
        },
    }


def _v3_strategy() -> dict:
    """Legacy strategy — no v5 fields; must still use flat stop."""
    return {
        "id": str(uuid4()),
        "name": "v3 flat stop",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {"type": "bar_close", "interval": "1d", "time": "15:25 IST"},
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": 10}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5.0, "max_qty": 1000},
            "portfolio": {
                "max_exposure_pct": 100,
                "max_concentration_pct": 100,
            },
            "daily": {"max_loss_pct": 50, "max_open_positions": 10},
        },
    }


def _run(closes: list[Decimal], strategy_dict: dict | None = None) -> object:
    bars = {"FAKE.NS": _gen_bars(closes)}
    strategy = parse_strategy(strategy_dict or _v5_strategy())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
    )
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=bars,
    ), patch("backend.algo.backtest.runner.flush_events"):
        return run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=["FAKE.NS"],
        )


# ── Tests ────────────────────────────────────────────────────────


class TestV3BackwardCompat:
    def test_v3_strategy_fires_flat_stop_loss(self):
        """v3 strategy has no v5 fields → trailing disabled →
        flat check_stop_loss_triggers path fires stop_loss."""
        summary = _run(_CLOSES_PHASE1_STOP, strategy_dict=_v3_strategy())
        exit_reasons = {t.exit_reason for t in summary.trade_list}
        assert "stop_loss" in exit_reasons, (
            "v3 strategy must still use flat stop_loss"
        )
        # No trailing-specific reasons
        assert "phase1_stop" not in exit_reasons
        assert "trail_stop" not in exit_reasons


class TestPhase1Stop:
    def test_phase1_stop_exit_reason(self):
        """Price drops to 90 (< 95 hard stop) → exit_reason='phase1_stop'."""
        summary = _run(_CLOSES_PHASE1_STOP)
        reasons = {t.exit_reason for t in summary.trade_list}
        assert "phase1_stop" in reasons, (
            f"expected phase1_stop in trade_list, got: {reasons}"
        )

    def test_phase1_stop_no_flat_stop_loss(self):
        """v5 path must not also fire stop_loss (flat path is bypassed)."""
        summary = _run(_CLOSES_PHASE1_STOP)
        reasons = {t.exit_reason for t in summary.trade_list}
        assert "stop_loss" not in reasons, (
            "v5 strategy must not fire flat stop_loss"
        )

    def test_phase1_stop_no_same_day_double_close(self):
        """stop_loss_skip must block AST re-eval on the stop bar.
        Two phase1_stop trades are fine (re-entry after a stop is
        expected), but no two phase1_stop trades may share the same
        closed_at date (that would mean double-close on one bar)."""
        summary = _run(_CLOSES_PHASE1_STOP)
        phase1 = [t for t in summary.trade_list if t.exit_reason == "phase1_stop"]
        assert phase1, "expected at least one phase1_stop trade"
        closed_dates = [t.closed_at for t in phase1]
        assert len(closed_dates) == len(set(closed_dates)), (
            f"double-close on same bar: {closed_dates}"
        )


class TestPhase1Ratchet:
    def test_phase1_ratchet_exit_reason(self):
        """Ratchets stop from 95→97 at +2 %, then low≤97 → phase1_ratchet."""
        summary = _run(_CLOSES_PHASE1_RATCHET)
        reasons = {t.exit_reason for t in summary.trade_list}
        assert "phase1_ratchet" in reasons, (
            f"expected phase1_ratchet, got: {reasons}"
        )

    def test_phase1_ratchet_not_trail_stop(self):
        """Must not emit trail_stop: price never crosses trailing_trigger_pct."""
        summary = _run(_CLOSES_PHASE1_RATCHET)
        reasons = {t.exit_reason for t in summary.trade_list}
        assert "trail_stop" not in reasons


class TestTrailStop:
    def test_trail_stop_exit_reason(self):
        """ATR trail fires after HWM ratchet; price drops through stop."""
        summary = _run(_CLOSES_TRAIL_STOP)
        reasons = {t.exit_reason for t in summary.trade_list}
        assert "trail_stop" in reasons, (
            f"expected trail_stop, got: {reasons}"
        )

    def test_trail_stop_not_phase1_stop(self):
        """Must not emit phase1_stop: stop was long since ratcheted past 95."""
        summary = _run(_CLOSES_TRAIL_STOP)
        reasons = {t.exit_reason for t in summary.trade_list}
        assert "phase1_stop" not in reasons

    def test_trail_stop_not_flat_stop_loss(self):
        """Flat stop_loss path must be bypassed for v5 strategies."""
        summary = _run(_CLOSES_TRAIL_STOP)
        reasons = {t.exit_reason for t in summary.trade_list}
        assert "stop_loss" not in reasons


class TestCooldownIntegration:
    def test_phase1_stop_in_failed_reasons(self):
        """phase1_stop must trigger the cooldown gate (thesis failed)."""
        from backend.algo.backtest.cooldown_monitor import (
            _FAILED_EXIT_REASONS,
        )
        assert "phase1_stop" in _FAILED_EXIT_REASONS

    def test_phase1_ratchet_in_failed_reasons(self):
        """phase1_ratchet must trigger the cooldown gate."""
        from backend.algo.backtest.cooldown_monitor import (
            _FAILED_EXIT_REASONS,
        )
        assert "phase1_ratchet" in _FAILED_EXIT_REASONS

    def test_trail_stop_not_in_failed_reasons(self):
        """trail_stop must NOT trigger cooldown — thesis worked."""
        from backend.algo.backtest.cooldown_monitor import (
            _FAILED_EXIT_REASONS,
        )
        assert "trail_stop" not in _FAILED_EXIT_REASONS
