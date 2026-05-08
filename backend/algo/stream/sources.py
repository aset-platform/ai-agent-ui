"""Tick sources — Replay (CI fixture) and Live (KiteTicker, Task 7).

A TickSource is an async iterator yielding Tick. Implementations
encapsulate where ticks come from; the resampler doesn't care.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator, Protocol

from backend.algo.stream.types import Tick

_logger = logging.getLogger(__name__)


class TickSource(Protocol):
    def __aiter__(self) -> AsyncIterator[Tick]: ...


class ReplayTickSource:
    """Stream ticks from a JSONL fixture.

    Lines beginning with ``#`` or empty lines are skipped, so the
    fixture file can carry inline comments for clarity.

    ``pace`` controls emit rate:
      - ``"fast"`` → emit immediately (CI default)
      - ``"realtime"`` → sleep based on tick ts_ns deltas (manual demo)
    """

    def __init__(
        self, path: Path, pace: str = "fast",
    ) -> None:
        self._path = Path(path)
        self._pace = pace

    async def __aiter__(self) -> AsyncIterator[Tick]:
        prev_ts_ns: int | None = None
        with self._path.open(encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                payload = json.loads(stripped)
                tick = Tick.model_validate(payload)
                if (
                    self._pace == "realtime"
                    and prev_ts_ns is not None
                ):
                    delay_s = max(
                        0.0,
                        (tick.ts_ns - prev_ts_ns) / 1_000_000_000,
                    )
                    await asyncio.sleep(delay_s)
                prev_ts_ns = tick.ts_ns
                yield tick
