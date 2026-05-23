"""Tests for the pure time-stop trigger function (ASETPLTFRM-430 Exp.3)."""

from __future__ import annotations

from datetime import date

import pytest

from backend.algo.backtest.time_stop_monitor import (
    TimeStopTrigger,
    check_time_stop_triggers,
)


def _open_position(opened_at: date, qty: int = 100) -> dict:
    return {"qty": qty, "opened_at": opened_at}


def test_trigger_when_holding_days_meets_threshold():
    triggers = check_time_stop_triggers(
        open_positions={
            "AAA.NS": _open_position(opened_at=date(2025, 1, 1)),
        },
        current_date=date(2025, 1, 6),
        max_holding_days=5,
    )
    assert len(triggers) == 1
    t = triggers[0]
    assert t.ticker == "AAA.NS"
    assert t.holding_days == 5
    assert t.max_holding_days == 5


def test_no_trigger_when_below_threshold():
    triggers = check_time_stop_triggers(
        open_positions={
            "AAA.NS": _open_position(opened_at=date(2025, 1, 1)),
        },
        current_date=date(2025, 1, 4),
        max_holding_days=5,
    )
    assert triggers == []


def test_disabled_when_max_holding_days_none():
    triggers = check_time_stop_triggers(
        open_positions={
            "AAA.NS": _open_position(opened_at=date(2025, 1, 1)),
        },
        current_date=date(2030, 1, 1),
        max_holding_days=None,
    )
    assert triggers == []


def test_disabled_when_max_holding_days_zero():
    triggers = check_time_stop_triggers(
        open_positions={
            "AAA.NS": _open_position(opened_at=date(2025, 1, 1)),
        },
        current_date=date(2025, 1, 2),
        max_holding_days=0,
    )
    assert triggers == []


def test_skip_position_missing_opened_at():
    triggers = check_time_stop_triggers(
        open_positions={"AAA.NS": {"qty": 100}},
        current_date=date(2025, 1, 10),
        max_holding_days=5,
    )
    assert triggers == []


def test_multi_position_independence():
    triggers = check_time_stop_triggers(
        open_positions={
            "OLD.NS": _open_position(opened_at=date(2025, 1, 1)),
            "MID.NS": _open_position(opened_at=date(2025, 1, 4)),
            "NEW.NS": _open_position(opened_at=date(2025, 1, 6)),
        },
        current_date=date(2025, 1, 7),
        max_holding_days=5,
    )
    assert len(triggers) == 1
    assert triggers[0].ticker == "OLD.NS"
