"""Tests for the repeat-offender cooldown gate (ASETPLTFRM-434 Exp.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from backend.algo.backtest.cooldown_monitor import in_cooldown


@dataclass
class _Pos:
    """Minimal stand-in matching the _ClosedPositionLike protocol."""

    ticker: str
    exit_reason: str
    closed_at: date | None


def test_disabled_when_cooldown_none():
    closed = [_Pos("AAA.NS", "time_stop", date(2025, 1, 1))]
    assert not in_cooldown(
        ticker="AAA.NS",
        bar_date=date(2025, 1, 5),
        closed_positions=closed,
        cooldown_days=None,
    )


def test_disabled_when_cooldown_zero():
    closed = [_Pos("AAA.NS", "time_stop", date(2025, 1, 1))]
    assert not in_cooldown(
        ticker="AAA.NS",
        bar_date=date(2025, 1, 5),
        closed_positions=closed,
        cooldown_days=0,
    )


def test_triggers_within_window_on_time_stop():
    closed = [_Pos("AAA.NS", "time_stop", date(2025, 1, 1))]
    assert in_cooldown(
        ticker="AAA.NS",
        bar_date=date(2025, 1, 15),
        closed_positions=closed,
        cooldown_days=30,
    )


def test_triggers_within_window_on_stop_loss():
    closed = [_Pos("AAA.NS", "stop_loss", date(2025, 1, 1))]
    assert in_cooldown(
        ticker="AAA.NS",
        bar_date=date(2025, 1, 15),
        closed_positions=closed,
        cooldown_days=30,
    )


def test_clears_after_window_expires():
    closed = [_Pos("AAA.NS", "time_stop", date(2025, 1, 1))]
    assert not in_cooldown(
        ticker="AAA.NS",
        bar_date=date(2025, 3, 1),  # 59 days later
        closed_positions=closed,
        cooldown_days=30,
    )


def test_signal_exits_dont_trigger_cooldown():
    # Clean reversion exits are NOT failed thesis — no cooldown.
    closed = [_Pos("AAA.NS", "signal", date(2025, 1, 1))]
    assert not in_cooldown(
        ticker="AAA.NS",
        bar_date=date(2025, 1, 15),
        closed_positions=closed,
        cooldown_days=30,
    )


def test_different_ticker_doesnt_trigger():
    closed = [_Pos("AAA.NS", "time_stop", date(2025, 1, 1))]
    assert not in_cooldown(
        ticker="BBB.NS",
        bar_date=date(2025, 1, 15),
        closed_positions=closed,
        cooldown_days=30,
    )


def test_multi_position_picks_up_any_match():
    closed = [
        _Pos("AAA.NS", "signal", date(2024, 1, 1)),
        _Pos("AAA.NS", "time_stop", date(2025, 1, 1)),
        _Pos("AAA.NS", "signal", date(2025, 1, 10)),
    ]
    # Recent time_stop within window — gate fires even if a
    # later signal-exit happened too.
    assert in_cooldown(
        ticker="AAA.NS",
        bar_date=date(2025, 1, 20),
        closed_positions=closed,
        cooldown_days=30,
    )


def test_closed_at_none_is_skipped():
    closed = [_Pos("AAA.NS", "time_stop", None)]
    assert not in_cooldown(
        ticker="AAA.NS",
        bar_date=date(2025, 1, 15),
        closed_positions=closed,
        cooldown_days=30,
    )


def test_exact_boundary_is_inclusive():
    # cutoff = 2025-01-31 - 30d = 2025-01-01.
    # closed_at == cutoff → cooldown still fires.
    closed = [_Pos("AAA.NS", "time_stop", date(2025, 1, 1))]
    assert in_cooldown(
        ticker="AAA.NS",
        bar_date=date(2025, 1, 31),
        closed_positions=closed,
        cooldown_days=30,
    )
