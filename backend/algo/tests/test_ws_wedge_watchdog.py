"""Tests: KiteWsMultiplexer connect-timeout watchdog, fast-rebuild,
and wedge escalation (ASETPLTFRM-470).

A wedged Kite WS connection never fires on_connect/on_close/on_error
— every EXISTING reconnect mechanism in ws_multiplexer.py is
callback-triggered, so none of it can help. These tests drive the
NEW timer-based watchdog instead.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

import backend.algo.broker.ws_event_store as _store
from backend.algo.broker.ws_multiplexer import (
    KiteWsMultiplexer,
)
from backend.algo.tests.fixtures.mock_kite_ws_server import (
    patch_multiplexer_ticker,
    set_wedge_mode,
)


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
async def test_connect_timeout_triggers_rebuild(monkeypatch):
    """A wedged first connect (no callback ever fires) must be
    detected by the timeout watchdog and trigger a fresh connect()
    attempt — the shim's second attempt succeeds normally."""
    import backend.algo.broker.ws_multiplexer as _mux_mod

    monkeypatch.setattr(_mux_mod, "_CONNECT_TIMEOUT_S", 0.05)
    _capture_ws_events(monkeypatch)

    async with patch_multiplexer_ticker():
        set_wedge_mode(True)
        mux = _make_mux()

        start_task = asyncio.ensure_future(mux.start())
        # Un-wedge WHILE the first attempt is still waiting on its
        # timeout (0.05s window) — the fast-rebuild it triggers
        # constructs a brand new shim that reads the (now-clear)
        # flag at construction time, so it connects normally. A
        # longer pre-unwedge sleep would let the default
        # _MAX_FAST_REBUILD_ATTEMPTS=2 exhaust and escalate into the
        # (unpatched, 1s) backoff loop before un-wedging ever lands
        # on a shim that gets a chance to connect — this test only
        # exercises the single-rebuild happy path; escalation is
        # covered separately by test_wedge_escalates_after_max_fast_
        # attempts.
        await asyncio.sleep(0.02)
        set_wedge_mode(False)
        await asyncio.sleep(0.2)
        await start_task

        assert mux.connected is True
        assert mux._rebuild_attempts == 0  # reset by on_connect


@pytest.mark.asyncio
async def test_wedge_escalates_after_max_fast_attempts(monkeypatch):
    """A connection that stays wedged across every fast-rebuild
    attempt must set wedge_escalated=True and emit a
    ws_wedge_escalated event, WITHOUT giving up on retrying."""
    import backend.algo.broker.ws_multiplexer as _mux_mod

    monkeypatch.setattr(_mux_mod, "_CONNECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(_mux_mod, "_MAX_FAST_REBUILD_ATTEMPTS", 2)
    monkeypatch.setattr(_mux_mod, "_MIN_BACKOFF_S", 0.05)
    events = _capture_ws_events(monkeypatch)

    async with patch_multiplexer_ticker():
        set_wedge_mode(True)  # left True for the whole test — every
        # rebuild-constructed shim reads this flag fresh, so it
        # stays wedged across every attempt, unlike a per-instance
        # flag which would reset on each new shim.
        mux = _make_mux()

        start_task = asyncio.ensure_future(mux.start())
        # 2 fast-rebuild attempts (0.05s timeout each) + fall into
        # the backoff loop (0.05s MIN_BACKOFF) + one more wedge.
        await asyncio.sleep(1.0)
        start_task.cancel()
        try:
            await start_task
        except asyncio.CancelledError:
            pass

        assert mux._wedge_escalated is True
        assert mux._rebuild_attempts > 2
        escalated = [
            e for e in events if e["type"] == "ws_wedge_escalated"
        ]
        assert len(escalated) == 1, (
            "escalation event must fire exactly once, not repeat "
            "on every subsequent backoff-loop wedge"
        )
        timeouts = [
            e for e in events if e["type"] == "ws_connect_timeout"
        ]
        assert len(timeouts) >= 2

        await mux.close()


@pytest.mark.asyncio
async def test_successful_reconnect_clears_escalation():
    """A prior escalation must clear on the next successful
    on_connect — recovery is always possible, not permanent."""
    async with patch_multiplexer_ticker():
        mux = _make_mux()
        mux._wedge_escalated = True
        mux._rebuild_attempts = 5

        await mux.start()

        assert mux.connected is True
        assert mux._wedge_escalated is False
        assert mux._rebuild_attempts == 0
