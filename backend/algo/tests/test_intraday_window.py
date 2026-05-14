"""Unit tests for the shared intraday-window helpers."""
from __future__ import annotations

from datetime import time

import pytest

from backend.algo.runtime.intraday_window import (
    default_entry_cutoff,
    is_entry_allowed,
    is_past_square_off,
    ist_time_from_ns,
    parse_ist_time,
)


def test_parse_ist_time_variants():
    assert parse_ist_time("15:10") == time(15, 10)
    assert parse_ist_time("15:10 IST") == time(15, 10)
    assert parse_ist_time("15:10:30 IST") == time(15, 10, 30)
    assert parse_ist_time(None) is None


def test_parse_ist_time_rejects_garbage():
    with pytest.raises(ValueError):
        parse_ist_time("not a time")


def test_default_entry_cutoff_subtracts_60min():
    assert default_entry_cutoff("15:10 IST") == "14:10 IST"
    assert default_entry_cutoff("15:14") == "14:14 IST"


def test_default_entry_cutoff_falls_back_to_15_14():
    """When the strategy doesn't pin a square-off, fall back to
    the LiveRuntime default (15:14 IST) → cutoff 14:14 IST."""
    assert default_entry_cutoff(None) == "14:14 IST"


def test_is_entry_allowed_mis_blocks_after_cutoff():
    assert is_entry_allowed(
        product="MIS",
        entry_cutoff_raw="14:00 IST",
        bar_time_ist=time(13, 45),
    )
    assert not is_entry_allowed(
        product="MIS",
        entry_cutoff_raw="14:00 IST",
        bar_time_ist=time(14, 0),
    )
    assert not is_entry_allowed(
        product="MIS",
        entry_cutoff_raw="14:00 IST",
        bar_time_ist=time(15, 0),
    )


def test_is_entry_allowed_cnc_always_passes():
    assert is_entry_allowed(
        product="CNC",
        entry_cutoff_raw=None,
        bar_time_ist=time(15, 10),
    )
    # Even with a cutoff set (legacy AST shape), CNC ignores it.
    assert is_entry_allowed(
        product="CNC",
        entry_cutoff_raw="14:00",
        bar_time_ist=time(15, 10),
    )


def test_is_past_square_off():
    assert is_past_square_off(
        product="MIS",
        square_off_raw="15:10 IST",
        bar_time_ist=time(15, 10),
    )
    assert is_past_square_off(
        product="MIS",
        square_off_raw="15:10 IST",
        bar_time_ist=time(15, 12),
    )
    assert not is_past_square_off(
        product="MIS",
        square_off_raw="15:10 IST",
        bar_time_ist=time(15, 9),
    )
    # CNC never has a forced square-off.
    assert not is_past_square_off(
        product="CNC",
        square_off_raw=None,
        bar_time_ist=time(23, 59),
    )


def test_ist_time_from_ns_converts_utc_to_ist():
    # Pick a known UTC instant. 2026-05-14 09:45:00 UTC = 15:15 IST.
    # Using ts_ns derived from datetime:
    from datetime import datetime, timezone
    dt = datetime(2026, 5, 14, 9, 45, tzinfo=timezone.utc)
    ts_ns = int(dt.timestamp() * 1_000_000_000)
    assert ist_time_from_ns(ts_ns) == time(15, 15)


def test_ist_time_from_ns_none_passthrough():
    assert ist_time_from_ns(None) is None
