"""Replay-from-fixture roundtrip."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.algo.stream.sources import ReplayTickSource

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "ticks_sample.jsonl"
)


@pytest.mark.asyncio
async def test_replay_yields_all_30_ticks():
    src = ReplayTickSource(_FIXTURE, pace="fast")
    ticks = [t async for t in src]
    assert len(ticks) == 30
    assert ticks[0].ticker == "FAKE.NS"
    assert ticks[0].ts_ns == 0
    assert ticks[0].ltp == 100.0
    assert ticks[-1].ts_ns == 180_000_000_000  # 180s


@pytest.mark.asyncio
async def test_replay_skips_blank_and_comment_lines(tmp_path):
    fp = tmp_path / "f.jsonl"
    fp.write_text(
        "# header comment\n"
        "\n"
        '{"ticker":"X","ts_ns":0,"ltp":1.0,"volume":1}\n'
        "# trailing comment\n"
        '{"ticker":"X","ts_ns":1000000000,"ltp":2.0,"volume":1}\n',
        encoding="utf-8",
    )
    ticks = [t async for t in ReplayTickSource(fp, pace="fast")]
    assert len(ticks) == 2
