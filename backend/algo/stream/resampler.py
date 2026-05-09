"""Pure tick → OHLCV bar resampler.

State: per (ticker, interval) we hold an in-progress bar with
``open``, ``high``, ``low``, ``last_ltp``, ``volume``,
``bar_open_ts_ns``. On every fed tick:

  1. For each configured interval, compute the bar-open ns that
     this tick belongs to (``ts_ns - (ts_ns % interval_ns)``).
  2. If we have an in-progress bar at a different bar-open, that
     bar has just closed — emit it into the pending queue and
     reset state for the new bar.
  3. Update high / low / last_ltp / volume on the current bar.

``pop_completed()`` drains the queue. ``close_partial_bars()``
forces all in-progress bars to be emitted (used at shutdown).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from backend.algo.stream.types import Bar, Tick

_logger = logging.getLogger(__name__)


class Resampler:
    def __init__(self, intervals: Iterable[int] = (60, 300)) -> None:
        self._intervals = tuple(intervals)
        # key = (ticker, interval) → in-progress bar dict.
        self._open: dict[tuple[str, int], dict] = {}
        self._completed: list[Bar] = []

    @staticmethod
    def _bar_open(ts_ns: int, interval_sec: int) -> int:
        interval_ns = interval_sec * 1_000_000_000
        return ts_ns - (ts_ns % interval_ns)

    def feed(self, tick: Tick) -> None:
        for interval_sec in self._intervals:
            self._feed_one(tick, interval_sec)

    def _feed_one(self, tick: Tick, interval_sec: int) -> None:
        key = (tick.ticker, interval_sec)
        bar_open = self._bar_open(tick.ts_ns, interval_sec)
        existing = self._open.get(key)

        if existing is not None and existing["bar_open"] != bar_open:
            # The new tick belongs to a later bar — close the open one.
            self._completed.append(self._finalize(
                tick.ticker, interval_sec, existing,
            ))
            existing = None

        if existing is None:
            self._open[key] = {
                "bar_open": bar_open,
                "open": tick.ltp,
                "high": tick.ltp,
                "low": tick.ltp,
                "close": tick.ltp,
                "volume": tick.volume,
            }
            return

        if tick.ltp > existing["high"]:
            existing["high"] = tick.ltp
        if tick.ltp < existing["low"]:
            existing["low"] = tick.ltp
        existing["close"] = tick.ltp
        existing["volume"] += tick.volume

    def pop_completed(self) -> list[Bar]:
        out = self._completed
        self._completed = []
        return out

    def close_partial_bars(self) -> list[Bar]:
        """Force-emit any in-progress bars (e.g. at shutdown)."""
        flushed: list[Bar] = []
        for (ticker, interval_sec), state in list(self._open.items()):
            flushed.append(
                self._finalize(ticker, interval_sec, state),
            )
        self._open.clear()
        return flushed

    def _finalize(
        self, ticker: str, interval_sec: int, state: dict,
    ) -> Bar:
        return Bar(
            ticker=ticker,
            interval_sec=interval_sec,
            bar_open_ts_ns=state["bar_open"],
            open=state["open"],
            high=state["high"],
            low=state["low"],
            close=state["close"],
            volume=state["volume"],
            written_at=datetime.now(timezone.utc),
        )
