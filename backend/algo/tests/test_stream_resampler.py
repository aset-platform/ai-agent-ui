"""Resampler unit tests — pure logic, no I/O."""
from __future__ import annotations

from backend.algo.stream.resampler import Resampler
from backend.algo.stream.types import Tick


def _tick(ticker: str, ts_sec: int, ltp: float, vol: int) -> Tick:
    return Tick(
        ticker=ticker,
        ts_ns=ts_sec * 1_000_000_000,
        ltp=ltp,
        volume=vol,
    )


def test_single_minute_emits_one_1m_bar():
    r = Resampler(intervals=(60,))
    # 09:15:00 → 09:15:30 → 09:15:59 → 09:16:00 (boundary)
    r.feed(_tick("X", 0, 100.0, 10))
    r.feed(_tick("X", 30, 105.0, 5))
    r.feed(_tick("X", 59, 102.0, 3))
    r.feed(_tick("X", 60, 103.0, 1))  # rolls the 09:15 bar
    bars = r.pop_completed()
    assert len(bars) == 1
    bar = bars[0]
    assert bar.interval_sec == 60
    assert bar.open == 100.0
    assert bar.high == 105.0
    assert bar.low == 100.0
    assert bar.close == 102.0
    assert bar.volume == 18
    assert bar.bar_open_ts_ns == 0


def test_two_intervals_emit_per_minute_and_per_5m():
    r = Resampler(intervals=(60, 300))
    for sec in range(0, 300):
        r.feed(_tick("X", sec, 100.0 + (sec % 5), 1))
    # Boundary tick at 300 closes the 09:15-09:20 5m bar AND
    # rolls the final 1m bar (09:19).
    r.feed(_tick("X", 300, 110.0, 1))
    bars = r.pop_completed()
    one_m = [b for b in bars if b.interval_sec == 60]
    five_m = [b for b in bars if b.interval_sec == 300]
    assert len(one_m) == 5  # 09:15, 09:16, 09:17, 09:18, 09:19
    assert len(five_m) == 1
    assert five_m[0].volume == 300
    assert five_m[0].bar_open_ts_ns == 0


def test_pop_completed_drains():
    r = Resampler(intervals=(60,))
    r.feed(_tick("X", 0, 100.0, 1))
    r.feed(_tick("X", 60, 101.0, 1))  # closes 1st minute
    assert len(r.pop_completed()) == 1
    assert r.pop_completed() == []


def test_multiple_tickers_independent():
    r = Resampler(intervals=(60,))
    r.feed(_tick("A", 0, 100.0, 1))
    r.feed(_tick("B", 0, 200.0, 1))
    r.feed(_tick("A", 60, 105.0, 1))
    r.feed(_tick("B", 60, 210.0, 1))
    bars = r.pop_completed()
    assert {b.ticker for b in bars} == {"A", "B"}
    assert len(bars) == 2


def test_close_partial_bars_flushes_open_intervals():
    r = Resampler(intervals=(60, 300))
    r.feed(_tick("X", 0, 100.0, 5))
    r.feed(_tick("X", 30, 102.0, 2))
    # No boundary tick — caller signals shutdown.
    bars = r.close_partial_bars()
    assert len(bars) == 2  # one 1m + one 5m
    one_m = next(b for b in bars if b.interval_sec == 60)
    assert one_m.open == 100.0
    assert one_m.close == 102.0
    assert one_m.volume == 7
