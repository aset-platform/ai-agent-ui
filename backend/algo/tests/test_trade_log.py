"""Trade-log builder tests — entry/exit join + reason text."""
from __future__ import annotations

from datetime import date

import pytest

from backend.algo.attribution.trade_log import build_trade_reason


def _trade(**overrides):
    base = dict(
        ticker="RELIANCE.NS",
        opened_at=date(2026, 5, 1),
        closed_at=date(2026, 5, 9),
        qty=10,
        avg_entry_price=1234.5,
        avg_exit_price=1456.0,
        realised_pnl_inr=2215.0,
    )
    base.update(overrides)
    return base


def test_compose_reason_with_full_context() -> None:
    """Entry event has regime + stress + factor exposures; exit
    event has exit reason. Reason text mentions both regime and
    exit reason."""
    entry_event = {
        "type": "signal_generated",
        "payload_json": (
            '{"side": "BUY", "qty": 10, "price": 1234.5, '
            '"regime_label": "BULL", "stress_prob": 0.18, '
            '"factor_exposures": {"mom_12_1": 0.85}, '
            '"feature_snapshot": {"rsi": 62}}'
        ),
    }
    exit_event = {
        "type": "signal_generated",
        "payload_json": (
            '{"side": "SELL", "qty": 10, "price": 1456.0, '
            '"reason": "trailing_stop"}'
        ),
    }
    reason = build_trade_reason(_trade(), entry_event, exit_event)
    assert reason.ticker == "RELIANCE.NS"
    assert reason.entry_regime == "BULL"
    assert reason.stress_prob == pytest.approx(0.18)
    assert reason.entry_factor_exposures == {"mom_12_1": 0.85}
    assert reason.pnl_pct == pytest.approx(
        (1456.0 - 1234.5) / 1234.5 * 100.0, rel=1e-3,
    )
    assert "BULL" in reason.reason_text
    assert "trailing_stop" in reason.reason_text
    assert "mom_12_1" in reason.reason_text


def test_compose_reason_legacy_event_no_attribution_context(
) -> None:
    """Pre-REGIME-6 entry event without regime/factor keys still
    produces a TradeReason with None defaults rather than raising.
    """
    entry_event = {
        "type": "signal_generated",
        "payload_json": (
            '{"side": "BUY", "qty": 5, "price": 100.0}'
        ),
    }
    exit_event = {
        "type": "signal_generated",
        "payload_json": (
            '{"side": "SELL", "qty": 5, "price": 110.0, '
            '"reason": "manual"}'
        ),
    }
    trade = _trade(
        ticker="INFY.NS",
        opened_at=date(2026, 5, 1),
        closed_at=date(2026, 5, 5),
        qty=5,
        avg_entry_price=100.0,
        avg_exit_price=110.0,
        realised_pnl_inr=50.0,
    )
    reason = build_trade_reason(trade, entry_event, exit_event)
    assert reason.entry_regime is None
    assert reason.stress_prob is None
    assert reason.entry_factor_exposures == {}
    assert reason.exit_reason == "manual"
    assert reason.pnl_pct == pytest.approx(10.0, rel=1e-3)


def test_handles_missing_entry_or_exit_event() -> None:
    """If entry/exit events are absent the builder returns a
    sensible row instead of raising."""
    reason = build_trade_reason(_trade(), None, None)
    assert reason.entry_regime is None
    assert reason.exit_reason is None
    assert reason.reason_text.startswith("BUY @")


def test_zero_entry_price_does_not_divide_by_zero() -> None:
    trade = _trade(
        avg_entry_price=0.0,
        avg_exit_price=10.0,
        realised_pnl_inr=10.0,
    )
    reason = build_trade_reason(trade, None, None)
    assert reason.pnl_pct == 0.0


def test_factor_exposures_drop_non_numeric() -> None:
    """A poisoned factor_exposures dict (e.g. via legacy data
    migration) drops bad values rather than raising."""
    entry_event = {
        "payload_json": (
            '{"factor_exposures": '
            '{"mom_12_1": 0.5, "f_score": "n/a"}}'
        ),
    }
    reason = build_trade_reason(_trade(), entry_event, None)
    assert reason.entry_factor_exposures == {"mom_12_1": 0.5}
