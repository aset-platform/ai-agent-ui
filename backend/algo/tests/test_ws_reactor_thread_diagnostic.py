"""Tests: reactor-thread liveness diagnostic (ASETPLTFRM-470).

Twisted's reactor is a per-PROCESS singleton; kiteconnect only
spawns its background thread on the FIRST ``KiteTicker.connect
(threaded=True)`` call in the process, so ``_capture_reactor_thread``
/ ``_reactor_thread_alive`` are module-level, not per-instance. These
tests exercise that module state directly — diagnostic only, no
control-flow / auto-restart behavior to assert.
"""
from __future__ import annotations

import threading
from uuid import uuid4

import pytest

import backend.algo.broker.ws_multiplexer as _mux_mod
from backend.algo.broker.ws_multiplexer import (
    KiteWsMultiplexer,
    _capture_reactor_thread,
    _reactor_thread_alive,
)


@pytest.fixture(autouse=True)
def _reset_reactor_thread_state(monkeypatch):
    """Module-level ``_reactor_thread`` must not leak between tests
    (or between this file and any other test importing the same
    module in the same pytest process)."""
    monkeypatch.setattr(_mux_mod, "_reactor_thread", None)


def test_alive_returns_none_before_any_capture():
    """Never having seen a connect() spawn the thread is honestly
    reported as unknown, not a false True/False."""
    assert _reactor_thread_alive() is None


def test_capture_records_thread_from_kt_attribute():
    """Mirrors kiteconnect's real shape: KiteTicker.connect() sets
    ``self.websocket_thread`` on the FIRST connect in the process."""

    class _FakeKt:
        websocket_thread = threading.Thread(target=lambda: None)

    _capture_reactor_thread(_FakeKt())
    assert _reactor_thread_alive() is False  # never started -> not alive


def test_capture_reflects_live_thread():
    ev = threading.Event()

    def _spin():
        ev.wait(timeout=2)

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    try:

        class _FakeKt:
            websocket_thread = t

        _capture_reactor_thread(_FakeKt())
        assert _reactor_thread_alive() is True
    finally:
        ev.set()
        t.join(timeout=2)


def test_capture_is_idempotent_across_rebuilds():
    """A rebuilt KiteTicker never gets its own thread (kiteconnect
    guards creation with ``if not reactor.running``) — capture must
    NOT overwrite the originally-captured thread with whatever a
    later kt object happens to carry (or lack)."""
    first = threading.Thread(target=lambda: None)

    class _First:
        websocket_thread = first

    class _Rebuilt:
        """No websocket_thread attribute at all — the real shape of
        every KiteTicker built after the first in a process."""

    _capture_reactor_thread(_First())
    _capture_reactor_thread(_Rebuilt())

    assert _mux_mod._reactor_thread is first


def test_capture_ignores_missing_attribute():
    """A kt object with no websocket_thread (rebuilt-ticker shape, or
    the test shim, which doesn't model this attribute at all) must
    not raise and must leave state as 'unknown'."""

    class _NoThreadAttr:
        pass

    _capture_reactor_thread(_NoThreadAttr())
    assert _reactor_thread_alive() is None


def test_health_snapshot_includes_diagnostic_key():
    """health_snapshot() always carries the key (None when unknown)
    so the /ws-health endpoint never has to special-case it."""
    mux = KiteWsMultiplexer(
        user_id=uuid4(), api_key="k", access_token="t",
    )
    snap = mux.health_snapshot()
    assert "reactor_thread_alive" in snap
    assert snap["reactor_thread_alive"] is None
