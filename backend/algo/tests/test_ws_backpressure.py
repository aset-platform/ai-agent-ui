"""Tests: KiteWsMultiplexer backpressure — bounded queue, drop-oldest,
warning log, event recorded.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

import pytest

from backend.algo.broker.ws_multiplexer import (
    _BP_AGG_WINDOW_NS,
    _BP_AGG_WINDOW_S,
    QUEUE_MAX_SIZE,
    KiteWsMultiplexer,
)
from backend.algo.stream.types import Tick
from backend.algo.tests.fixtures.mock_kite_ws_server import (
    _ticker_to_token,
    patch_multiplexer_ticker,
)

import backend.algo.broker.ws_event_store as _store


def _capture_ws_events(monkeypatch):
    """Capture record_ws_event calls into a list of payload dicts."""
    captured: list[dict] = []

    def _fake(*, user_id, event_id, ts_ns, type_, strategy_id, payload):
        captured.append(
            {"type": type_, "payload": payload, "ts_ns": ts_ns}
        )
        return True

    monkeypatch.setattr(_store, "record_ws_event", _fake)
    return captured


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


def test_backpressure_aggregates_within_window(monkeypatch):
    mux = _make_mux()
    captured = _capture_ws_events(monkeypatch)
    sid = uuid4()
    for _ in range(500):
        mux._record_backpressure_event(strategy_id=sid, token=111)
    bp = [e for e in captured if e["type"] == "ws_backpressure_drop"]
    assert len(bp) == 1
    assert bp[0]["payload"]["dropped"] == 1
    assert mux._bp_drops[sid] == 499


def test_backpressure_summary_carries_count_on_window_roll(monkeypatch):
    mux = _make_mux()
    captured = _capture_ws_events(monkeypatch)
    sid = uuid4()
    for _ in range(500):
        mux._record_backpressure_event(strategy_id=sid, token=111)
    mux._bp_last_emit_ns[sid] -= _BP_AGG_WINDOW_NS + 1_000_000_000
    mux._record_backpressure_event(strategy_id=sid, token=111)
    bp = [e for e in captured if e["type"] == "ws_backpressure_drop"]
    assert len(bp) == 2
    assert bp[1]["payload"]["dropped"] == 500
    assert bp[1]["payload"]["window_s"] == _BP_AGG_WINDOW_S
    assert sid not in mux._bp_drops


def test_backpressure_residual_flushed_on_close(monkeypatch):
    mux = _make_mux()
    captured = _capture_ws_events(monkeypatch)
    sid = uuid4()
    for _ in range(10):
        mux._record_backpressure_event(strategy_id=sid, token=111)
    assert mux._bp_drops[sid] == 9
    mux._flush_backpressure_residual()
    bp = [e for e in captured if e["type"] == "ws_backpressure_drop"]
    assert len(bp) == 2
    assert bp[1]["payload"]["dropped"] == 9
    assert not mux._bp_drops


def test_emit_routes_to_store_not_iceberg(monkeypatch):
    """_emit_ws_event persists via record_ws_event; the multiplexer
    keeps no Iceberg buffer."""
    mux = _make_mux()
    captured = _capture_ws_events(monkeypatch)
    mux._emit_ws_event("ws_connected", {"strategy_id": None})
    assert len(captured) == 1
    assert captured[0]["type"] == "ws_connected"
    assert not hasattr(mux, "_ws_events")
