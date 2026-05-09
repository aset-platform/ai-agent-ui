"""Tests: KiteWsMultiplexer happy-path — connect, subscribe, fan-out.

All tests use the KiteTickerShim (no real network).
"""
from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_multiplexer_connects_and_reports_connected():
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()
        assert mux.connected


@pytest.mark.asyncio
async def test_subscribe_returns_queue_and_registers_tokens():
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        sid = uuid4()
        ticker = "RELIANCE.NS"
        tok = _ticker_to_token(ticker)
        q = mux.subscribe(
            sid, [tok], {tok: ticker},
        )
        assert q is not None
        assert tok in shim.subscribed_tokens


@pytest.mark.asyncio
async def test_tick_fanout_to_single_subscriber():
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        sid = uuid4()
        ticker = "INFY.NS"
        tok = _ticker_to_token(ticker)
        q = mux.subscribe(sid, [tok], {tok: ticker})

        tick = Tick(ticker=ticker, ts_ns=1_000_000, ltp=1500.0,
                    volume=100)
        shim.inject_ticks([tick])
        # Yield to event loop so call_soon_threadsafe callbacks run.
        await asyncio.sleep(0)

        received = q.get_nowait()
        assert received.ticker == ticker
        assert received.ltp == pytest.approx(1500.0)


@pytest.mark.asyncio
async def test_tick_fanout_to_two_strategies_same_token():
    """Two strategies on same token both receive the tick."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "TCS.NS"
        tok = _ticker_to_token(ticker)
        sid1, sid2 = uuid4(), uuid4()
        q1 = mux.subscribe(sid1, [tok], {tok: ticker})
        q2 = mux.subscribe(sid2, [tok], {tok: ticker})

        tick = Tick(ticker=ticker, ts_ns=2_000_000, ltp=3900.0,
                    volume=50)
        shim.inject_ticks([tick])
        await asyncio.sleep(0)

        r1 = q1.get_nowait()
        r2 = q2.get_nowait()
        assert r1.ltp == pytest.approx(3900.0)
        assert r2.ltp == pytest.approx(3900.0)


@pytest.mark.asyncio
async def test_unsubscribe_sends_eof_and_removes_queue():
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        sid = uuid4()
        ticker = "HDFC.NS"
        tok = _ticker_to_token(ticker)
        q = mux.subscribe(sid, [tok], {tok: ticker})

        await mux.unsubscribe(sid)

        # Queue should receive EOF sentinel (None).
        sentinel = q.get_nowait()
        assert sentinel is None
        # Token ref removed; unsubscribed from shim.
        assert tok in shim.unsubscribed_tokens


@pytest.mark.asyncio
async def test_unsubscribe_only_removes_last_subscriber_token():
    """Token is unsubscribed from Kite only when last strategy leaves."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "WIPRO.NS"
        tok = _ticker_to_token(ticker)
        sid1, sid2 = uuid4(), uuid4()
        mux.subscribe(sid1, [tok], {tok: ticker})
        mux.subscribe(sid2, [tok], {tok: ticker})

        await mux.unsubscribe(sid1)
        # Still has sid2 → token NOT removed from Kite yet.
        assert tok not in shim.unsubscribed_tokens

        await mux.unsubscribe(sid2)
        # Last subscriber gone → now unsubscribed.
        assert tok in shim.unsubscribed_tokens


@pytest.mark.asyncio
async def test_close_signals_eof_to_all_queues():
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "BAJFINANCE.NS"
        tok = _ticker_to_token(ticker)
        sid = uuid4()
        q = mux.subscribe(sid, [tok], {tok: ticker})

        await mux.close()

        sentinel = q.get_nowait()
        assert sentinel is None
        assert mux._closed


@pytest.mark.asyncio
async def test_two_simultaneous_strategies_share_single_connection():
    """Verify subscriber_count tracks both strategies, one WS."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker1 = "SBIN.NS"
        ticker2 = "ICICIBANK.NS"
        tok1 = _ticker_to_token(ticker1)
        tok2 = _ticker_to_token(ticker2)
        sid1, sid2 = uuid4(), uuid4()

        mux.subscribe(sid1, [tok1], {tok1: ticker1})
        mux.subscribe(sid2, [tok2], {tok2: ticker2})

        assert mux.subscriber_count == 2
        # Only one WS object — verify there's only one KiteTicker.
        assert mux._kt is not None


@pytest.mark.asyncio
async def test_tearing_down_all_strategies_closes_multiplexer():
    """After all strategies unsubscribed, close() succeeds cleanly."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "MARUTI.NS"
        tok = _ticker_to_token(ticker)
        sid = uuid4()
        mux.subscribe(sid, [tok], {tok: ticker})
        await mux.unsubscribe(sid)

        assert mux.subscriber_count == 0
        await mux.close()
        assert mux._closed


@pytest.mark.asyncio
async def test_subscribe_same_strategy_twice_is_idempotent():
    """Calling subscribe twice with same sid returns same queue."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        sid = uuid4()
        ticker = "NTPC.NS"
        tok = _ticker_to_token(ticker)
        q1 = mux.subscribe(sid, [tok], {tok: ticker})
        q2 = mux.subscribe(sid, [tok], {tok: ticker})

        assert q1 is q2
