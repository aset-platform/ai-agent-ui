"""Tests: KiteWsMultiplexer backpressure — bounded queue, drop-oldest,
warning log, event recorded.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

import pytest

from backend.algo.broker.ws_multiplexer import (
    QUEUE_MAX_SIZE,
    KiteWsMultiplexer,
)
from backend.algo.stream.types import Tick
from backend.algo.tests.fixtures.mock_kite_ws_server import (
    _ticker_to_token,
    patch_multiplexer_ticker,
)


def _make_mux() -> KiteWsMultiplexer:
    return KiteWsMultiplexer(
        user_id=uuid4(),
        api_key="test_key",
        access_token="test_token",
    )


@pytest.mark.asyncio
async def test_backpressure_drop_oldest_on_overflow(caplog):
    """When queue is full the oldest tick is dropped (not newest)."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "ADANIENT.NS"
        tok = _ticker_to_token(ticker)
        sid = uuid4()
        q = mux.subscribe(sid, [tok], {tok: ticker})

        # Fill the queue directly to capacity with ltp=1.0 ticks.
        for i in range(QUEUE_MAX_SIZE):
            q.put_nowait(
                Tick(ticker=ticker, ts_ns=i * 1000,
                     ltp=1.0, volume=1),
            )

        assert q.full()

        # Now inject one more via the shim (overflow) with ltp=999.0.
        # The on_ticks handler will detect q.full(), drop oldest, then put new.
        shim.inject_raw([{
            "instrument_token": tok,
            "last_price": 999.0,
            "last_traded_quantity": 1,
        }])
        # Yield to process the call_soon_threadsafe callbacks.
        await asyncio.sleep(0)

        # Queue still at QUEUE_MAX_SIZE.
        assert q.qsize() == QUEUE_MAX_SIZE
        # Newest item (ltp=999.0) is present.
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert any(i.ltp == pytest.approx(999.0) for i in items)


@pytest.mark.asyncio
async def test_backpressure_emits_warning_log(caplog):
    """WARNING is logged when backpressure drop occurs."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "COALINDIA.NS"
        tok = _ticker_to_token(ticker)
        sid = uuid4()
        q = mux.subscribe(sid, [tok], {tok: ticker})

        # Fill queue directly to capacity.
        for i in range(QUEUE_MAX_SIZE):
            q.put_nowait(
                Tick(ticker=ticker, ts_ns=i * 1000,
                     ltp=1.0, volume=1),
            )

        with caplog.at_level(logging.WARNING):
            # Overflow via shim.
            shim.inject_raw([{
                "instrument_token": tok,
                "last_price": 2.0,
                "last_traded_quantity": 1,
            }])
            await asyncio.sleep(0)

        assert any(
            "ws_backpressure_drop" in rec.message
            for rec in caplog.records
        )


@pytest.mark.asyncio
async def test_backpressure_records_event():
    """ws_backpressure_drop event is recorded in _ws_events."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "ONGC.NS"
        tok = _ticker_to_token(ticker)
        sid = uuid4()
        q = mux.subscribe(sid, [tok], {tok: ticker})

        # Fill queue directly to capacity.
        for i in range(QUEUE_MAX_SIZE):
            q.put_nowait(
                Tick(ticker=ticker, ts_ns=i * 1000,
                     ltp=1.0, volume=1),
            )

        # Overflow via shim.
        shim.inject_raw([{
            "instrument_token": tok,
            "last_price": 2.0,
            "last_traded_quantity": 1,
        }])
        # Allow event loop to process call_soon_threadsafe callbacks.
        await asyncio.sleep(0)

        event_types = [e["type"] for e in mux._ws_events]
        assert "ws_backpressure_drop" in event_types


@pytest.mark.asyncio
async def test_normal_throughput_does_not_drop():
    """1000 ticks via shim → all land in queue with no drops."""
    async with patch_multiplexer_ticker() as shim:
        mux = _make_mux()
        await mux.start()

        ticker = "POWERGRID.NS"
        tok = _ticker_to_token(ticker)
        sid = uuid4()
        q = mux.subscribe(sid, [tok], {tok: ticker})

        # Inject QUEUE_MAX_SIZE ticks, each with ltp > 0.
        shim.inject_raw([
            {
                "instrument_token": tok,
                "last_price": float(i + 1),
                "last_traded_quantity": 1,
            }
            for i in range(QUEUE_MAX_SIZE)
        ])
        # Yield to let call_soon_threadsafe callbacks run.
        await asyncio.sleep(0)

        assert q.qsize() == QUEUE_MAX_SIZE
