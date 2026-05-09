"""TickStreamService — orchestrates a TickSource through the
Resampler and persists completed bars.

v1 = single-source-per-instance. Slice 8 (paper) will spawn one
service per active strategy. Multi-tenancy concerns (one Kite WS
per user fan-out across strategies) live there, not here.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable

from backend.algo.stream.bars_writer import flush_bars
from backend.algo.stream.resampler import Resampler
from backend.algo.stream.sources import TickSource
from backend.algo.stream.types import Bar

_logger = logging.getLogger(__name__)

# Type alias for the persistence hook so tests can substitute.
BarFlushFn = Callable[[list[Bar]], None]


class TickStreamService:
    def __init__(
        self,
        source: TickSource,
        intervals: Iterable[int] = (60, 300),
        flush: BarFlushFn = flush_bars,
        flush_threshold: int = 100,
    ) -> None:
        self._source = source
        self._resampler = Resampler(intervals=intervals)
        self._flush = flush
        self._threshold = flush_threshold
        self._buffer: list[Bar] = []
        self._total_flushed = 0

    async def run(self) -> int:
        """Drain the source through the resampler. Returns the
        total number of bars persisted.
        """
        try:
            async for tick in self._source:
                self._resampler.feed(tick)
                self._buffer.extend(
                    self._resampler.pop_completed(),
                )
                if len(self._buffer) >= self._threshold:
                    self._flush_now()
        finally:
            # Force-emit any in-flight bars on shutdown.
            self._buffer.extend(
                self._resampler.close_partial_bars(),
            )
            if self._buffer:
                self._flush_now()
        return self._total_flushed

    def _flush_now(self) -> None:
        if not self._buffer:
            return
        self._flush(self._buffer)
        _logger.info(
            "flushed batch (%d bars)", len(self._buffer),
        )
        self._total_flushed += len(self._buffer)
        self._buffer = []
