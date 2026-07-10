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


@pytest.mark.asyncio
async def test_wedge_recovery_does_not_cause_reconnect_storm(
    monkeypatch,
):
    """Review finding (Critical, ASETPLTFRM-470): a successful
    in-process fast-rebuild must NOT leave a spurious
    _trigger_reconnect queued against the freshly-healthy
    connection.

    Root cause: a wedge means the OLD ticker's on_close has NEVER
    fired before, so _handle_wedge()'s own _disconnect_kt() call is
    the FIRST time .close() ever runs on it — and in this test
    shim (mirroring how many WS libraries behave for an
    already-pending/never-upgraded socket) that fires on_close
    SYNCHRONOUSLY, before self._kt has been reassigned to the new
    ticker. So the on_close identity guard alone does not catch it
    (``ws is self._kt`` still holds at that instant) — but its
    ``call_soon_threadsafe(self._trigger_reconnect)`` side effect
    is deferred to the next loop tick, by which point the fast
    rebuild has already connected successfully. Without a
    self._connected guard inside _trigger_reconnect() itself, this
    silently tears the healthy connection back down and repeats
    forever — invisible to rebuild_attempts/wedge_escalated, both
    already reset by the fast on_connect."""
    import backend.algo.broker.ws_multiplexer as _mux_mod

    monkeypatch.setattr(_mux_mod, "_CONNECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(_mux_mod, "_MIN_BACKOFF_S", 0.05)
    _capture_ws_events(monkeypatch)

    async with patch_multiplexer_ticker():
        set_wedge_mode(True)
        mux = _make_mux()

        start_task = asyncio.ensure_future(mux.start())
        await asyncio.sleep(0.02)
        set_wedge_mode(False)
        await asyncio.sleep(0.2)
        await start_task

        assert mux.connected is True

        # Give any spuriously-queued reconnect loop a full window to
        # wake up (backoff patched to 0.05s) and tear the healthy
        # connection back down, if the storm bug were still present.
        await asyncio.sleep(0.3)

        assert mux.connected is True
        assert (
            mux._reconnect_task is None
            or mux._reconnect_task.done()
        ), "a spurious reconnect must never be scheduled while connected"

        await mux.close()


@pytest.mark.asyncio
async def test_stale_close_from_replaced_ticker_is_ignored(
    monkeypatch,
):
    """Review finding (Critical, ASETPLTFRM-470), identity-guard
    half: a late on_close callback that fires for a ticker
    generation already replaced by a rebuild must be a no-op.

    Simulates the real-world background-thread race directly — the
    SDK's close signal reaching the wedged ticker's own thread well
    AFTER a faster in-process rebuild has already moved self._kt on
    to a new, healthy ticker — by holding a reference to the first
    (wedged) shim and manually re-invoking its bound on_close well
    after the rebuild has succeeded."""
    import backend.algo.broker.ws_multiplexer as _mux_mod
    import backend.algo.tests.fixtures.mock_kite_ws_server as _shim_mod

    monkeypatch.setattr(_mux_mod, "_CONNECT_TIMEOUT_S", 0.05)
    _capture_ws_events(monkeypatch)

    async with patch_multiplexer_ticker():
        set_wedge_mode(True)
        mux = _make_mux()

        start_task = asyncio.ensure_future(mux.start())
        await asyncio.sleep(0.02)
        old_shim = _shim_mod._current_shim  # still-wedged 1st shim
        set_wedge_mode(False)
        await asyncio.sleep(0.2)
        await start_task

        assert mux.connected is True
        assert old_shim is not mux._kt  # rebuild replaced it

        # Simulate the deferred real-world callback: the OLD shim's
        # own on_close fires late, well after the rebuild succeeded.
        old_shim.on_close(old_shim, 0, "late close from replaced kt")

        assert mux.connected is True
        assert (
            mux._reconnect_task is None
            or mux._reconnect_task.done()
        )

        await mux.close()


@pytest.mark.asyncio
async def test_schedule_reconnect_skips_when_already_connected(
    monkeypatch,
):
    """Review finding round 2 (Important, ASETPLTFRM-470): the real
    (threaded) Kite SDK's on_connect fires asynchronously — unlike
    this test shim's synchronous connect() — so in production there
    is a narrow window where a stale ``call_soon_threadsafe(
    self._trigger_reconnect)`` could still run while
    ``self._connected`` is momentarily False (the fast-rebuild's new
    ticker hasn't fired its real on_connect yet), letting one
    spurious ``_schedule_reconnect()`` task get scheduled despite
    the ``_trigger_reconnect()`` guard from round 1.

    Defense-in-depth: ``_schedule_reconnect()`` now re-checks
    ``self._connected`` right after its own backoff sleep — by which
    point (at minimum ``_MIN_BACKOFF_S`` real seconds later, far
    slower than any real Kite handshake) the connection is expected
    to already be genuinely healthy, so the loop can bail out before
    touching anything.

    This test deliberately BYPASSES ``_trigger_reconnect()``'s own
    guard — it schedules ``_schedule_reconnect()`` directly, as if a
    stale callback had already slipped past that layer — to isolate
    and prove THIS specific defense layer in isolation."""
    import backend.algo.broker.ws_multiplexer as _mux_mod

    monkeypatch.setattr(_mux_mod, "_MIN_BACKOFF_S", 0.05)
    build_calls: list[int] = []

    async with patch_multiplexer_ticker():
        mux = _make_mux()
        await mux.start()
        assert mux.connected is True

        orig_build = mux._build_ticker

        def _spy_build():
            build_calls.append(1)
            return orig_build()

        mux._build_ticker = _spy_build
        kt_before = mux._kt

        # Deliberately bypass _trigger_reconnect()'s own connected
        # guard (round 1's fix) by scheduling _schedule_reconnect()
        # directly — simulating a stale trigger that already slipped
        # past that layer in the real SDK's narrow timing window.
        mux._backoff_s = _mux_mod._MIN_BACKOFF_S
        mux._reconnect_task = asyncio.ensure_future(
            mux._schedule_reconnect(),
        )

        # Wait past the (patched-short) backoff window.
        await asyncio.sleep(0.2)

        assert mux.connected is True
        assert mux._kt is kt_before  # untouched — no rebuild ran
        assert build_calls == []  # _build_ticker never called

        await mux.close()


@pytest.mark.asyncio
async def test_staleness_watchdog_fires_when_ticks_stop(
    monkeypatch,
):
    """connected=True but last_tick_at is stale despite subscribed
    tokens must trigger the same _handle_wedge() rebuild path."""
    import backend.algo.broker.ws_multiplexer as _mux_mod
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(_mux_mod, "_STALE_TICK_THRESHOLD_S", 1.0)
    monkeypatch.setattr(
        "backend.algo.live.reconciliation.is_market_open_ist",
        lambda: True,
    )
    handled: list[bool] = []

    async with patch_multiplexer_ticker():
        mux = _make_mux()
        await mux.start()
        mux._token_subs[12345] = {uuid4()}
        mux.last_tick_at = datetime.now(timezone.utc).replace(
            tzinfo=None,
        ) - timedelta(seconds=5)

        async def _fake_handle_wedge():
            handled.append(True)

        monkeypatch.setattr(mux, "_handle_wedge", _fake_handle_wedge)

        await mux._watch_staleness_loop_once()

        assert handled == [True]
        await mux.close()


@pytest.mark.asyncio
async def test_staleness_watchdog_skips_outside_market_hours(
    monkeypatch,
):
    """Outside NSE session hours, no ticks are expected — the
    staleness check must NOT fire, even with a very old
    last_tick_at."""
    import backend.algo.broker.ws_multiplexer as _mux_mod
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(_mux_mod, "_STALE_TICK_THRESHOLD_S", 1.0)
    monkeypatch.setattr(
        "backend.algo.live.reconciliation.is_market_open_ist",
        lambda: False,
    )
    handled: list[bool] = []

    async with patch_multiplexer_ticker():
        mux = _make_mux()
        await mux.start()
        mux._token_subs[12345] = {uuid4()}
        mux.last_tick_at = datetime.now(timezone.utc).replace(
            tzinfo=None,
        ) - timedelta(seconds=999)

        async def _fake_handle_wedge():
            handled.append(True)

        monkeypatch.setattr(mux, "_handle_wedge", _fake_handle_wedge)

        await mux._watch_staleness_loop_once()

        assert handled == []
        await mux.close()
