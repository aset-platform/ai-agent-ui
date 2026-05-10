"""Tests: KiteWsMultiplexer health snapshot — OBS-1.

Verifies the new ``last_tick_at`` / ``tick_count_today``
attributes plus the ``health_snapshot()`` and
``reset_tick_count()`` methods used by GET /v1/algo/live/ws-health
and the daily IST-midnight reset job.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.algo.broker.ws_multiplexer import KiteWsMultiplexer
from backend.algo.stream.types import Tick
from backend.algo.tests.fixtures.mock_kite_ws_server import (
    _ticker_to_token,
    patch_multiplexer_ticker,
)


def _make_mux() -> KiteWsMultiplexer:
    return KiteWsMultiplexer(
        user_id=uuid4(),
        api_key="test_api_key",
        access_token="test_access_token",
    )


def test_health_snapshot_initial():
    """Newly constructed mux: not connected, no ticks, count 0."""
    mux = _make_mux()
    assert mux.last_tick_at is None
    assert mux.tick_count_today == 0
    snap = mux.health_snapshot()
    assert snap == {
        "connected": False,
        "subscriber_count": 0,
        "subscribed_tokens": 0,
        "last_tick_at": None,
        "tick_count_today": 0,
    }


@pytest.mark.asyncio
async def test_health_snapshot_after_tick():
    """A delivered tick advances last_tick_at + tick_count_today."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        sid = uuid4()
        ticker = "INFY.NS"
        tok = _ticker_to_token(ticker)
        mux.subscribe(sid, [tok], {tok: ticker})

        before = datetime.now(timezone.utc).replace(tzinfo=None)
        tick = Tick(
            ticker=ticker, ts_ns=1_000_000, ltp=1500.0, volume=100,
        )
        shim.inject_ticks([tick])

        assert mux.tick_count_today == 1
        assert mux.last_tick_at is not None
        # Within a couple of seconds, no tzinfo (Iceberg-tz-naive).
        assert mux.last_tick_at.tzinfo is None
        delta = abs(mux.last_tick_at - before)
        assert delta < timedelta(seconds=5)

        snap = mux.health_snapshot()
        assert snap["connected"] is True
        assert snap["subscriber_count"] == 1
        assert snap["subscribed_tokens"] == 1
        assert snap["tick_count_today"] == 1
        assert snap["last_tick_at"] is not None


@pytest.mark.asyncio
async def test_tick_count_resets_at_midnight():
    """reset_tick_count() zeroes the counter, leaves last_tick_at."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        sid = uuid4()
        ticker = "TCS.NS"
        tok = _ticker_to_token(ticker)
        mux.subscribe(sid, [tok], {tok: ticker})

        shim.inject_ticks([
            Tick(ticker=ticker, ts_ns=1, ltp=1.0, volume=1),
            Tick(ticker=ticker, ts_ns=2, ltp=2.0, volume=2),
            Tick(ticker=ticker, ts_ns=3, ltp=3.0, volume=3),
        ])
        assert mux.tick_count_today == 3
        prior_last = mux.last_tick_at
        assert prior_last is not None

        mux.reset_tick_count()

        assert mux.tick_count_today == 0
        # last_tick_at preserved (only the counter resets).
        assert mux.last_tick_at == prior_last
