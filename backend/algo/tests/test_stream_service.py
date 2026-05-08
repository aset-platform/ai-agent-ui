"""End-to-end orchestrator over the replay fixture."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.algo.stream.service import TickStreamService
from backend.algo.stream.sources import ReplayTickSource

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "ticks_sample.jsonl"
)


@pytest.mark.asyncio
async def test_service_resamples_fixture_and_flushes_bars():
    flush = MagicMock()
    src = ReplayTickSource(_FIXTURE, pace="fast")
    svc = TickStreamService(
        source=src, intervals=(60, 300), flush=flush,
    )
    await svc.run()
    flush.assert_called()
    # Concatenate all flushed bars across calls.
    all_bars = [b for c in flush.call_args_list for b in c.args[0]]
    one_m = [b for b in all_bars if b.interval_sec == 60]
    five_m = [b for b in all_bars if b.interval_sec == 300]
    # 30 ticks across 3m → full 1m bars at 0s, 60s, 120s; 5m
    # bar 0-300 closed at shutdown via close_partial_bars.
    assert len(one_m) >= 3
    assert len(five_m) >= 1
    # Open of the very first 1m bar must equal the first tick LTP.
    first_one_m = sorted(one_m, key=lambda b: b.bar_open_ts_ns)[0]
    assert first_one_m.open == 100.0


@pytest.mark.asyncio
async def test_service_no_flush_on_empty_source(tmp_path):
    fp = tmp_path / "empty.jsonl"
    fp.write_text("", encoding="utf-8")
    flush = MagicMock()
    svc = TickStreamService(
        source=ReplayTickSource(fp, pace="fast"),
        intervals=(60,),
        flush=flush,
    )
    await svc.run()
    flush.assert_not_called()
