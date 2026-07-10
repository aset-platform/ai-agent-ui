# Kite WS Wedge Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect a silently-wedged Kite WebSocket connection (one where
`connect()` was called but no `on_connect`/`on_close`/`on_error`
callback ever fires) via timers instead of callbacks, auto-rebuild it
in-process, and escalate to a loud, persistent signal — both backend
event and a frontend banner — if repeated rebuild attempts still
don't recover, so live trading never again runs blind for hours
without anyone noticing (ASETPLTFRM-470).

**Architecture:** A connect-timeout watchdog (asyncio timer, not a
Kite callback) arms after every `connect()` call inside
`KiteWsMultiplexer`; if it fires before `_connected` flips `True`,
a shared `_handle_wedge()` helper attempts a fast in-process rebuild
for the first 2 attempts, then falls back to the existing
exponential-backoff reconnect loop while setting a persistent
`wedge_escalated` flag. A second, market-hours-gated periodic task
catches the "connected forever but ticks silently stopped" variant
via the same `_handle_wedge()` path. `wedge_escalated` threads through
`health_snapshot()` → the existing `GET /algo/live/ws-health` route →
a new frontend banner shown whenever a user's live strategy is armed.

**Tech Stack:** Python 3.12 asyncio (backend), Next.js 16 / React 19
(frontend), pytest-asyncio, vitest + testing-library.

## Global Constraints

- Line length 79 chars (black/isort/flake8).
- `X | None`, not `Optional[X]`.
- No bare `except:` — `except Exception` or specific.
- Caught exceptions in long-running jobs MUST log with `exc_info=True`.
- No module-level mutable globals (exception: `_logger`).
- `_CONNECT_TIMEOUT_S = 15.0`, `_MAX_FAST_REBUILD_ATTEMPTS = 2`,
  `_STALENESS_CHECK_INTERVAL_S = 60.0`, `_STALE_TICK_THRESHOLD_S = 90.0`
  — exact values from the approved design spec
  (`docs/superpowers/specs/2026-07-10-kite-ws-wedge-watchdog-design.md`),
  do not substitute different numbers.
- Escalation (`wedge_escalated=True`) is an ALERTING signal, not a
  give-up signal — retries MUST continue via the existing backoff
  loop after escalating, never stop entirely.
- Follow this repo's "patch at SOURCE module, not importer" convention
  for all new mocks (`backend.algo.broker.ws_event_store.record_ws_event`,
  `backend.algo.live.reconciliation.is_market_open_ist`).
- Do NOT restart the backend without asking the user first — it kills
  the live Kite WS session.

---

## Task 1: Connect-timeout watchdog + fast-rebuild + escalation

**Files:**
- Modify: `backend/algo/broker/ws_multiplexer.py` (constants near
  top, `__init__`, `_connect`, `_build_ticker`'s `on_connect`
  closure, `_schedule_reconnect`, `close`; add `_watch_connect_timeout`
  and `_handle_wedge` methods)
- Modify: `backend/algo/tests/fixtures/mock_kite_ws_server.py`
  (`KiteTickerShim` gains a `wedge_mode` flag driven by a new
  module-level `set_wedge_mode()` test helper)
- Test: `backend/algo/tests/test_ws_wedge_watchdog.py` (create)

**Interfaces:**
- Produces: `KiteWsMultiplexer._wedge_escalated: bool` (instance
  attribute, starts `False`) — Task 3 reads this via
  `health_snapshot()`. `KiteWsMultiplexer._rebuild_attempts: int`,
  `KiteWsMultiplexer._handle_wedge() -> None` (async method) — Task 2
  calls this same method for the staleness-detection path.
- Consumes: nothing from other tasks (this is the foundational task).

- [ ] **Step 1: Add wedge-mode support to the test shim**

Read `backend/algo/tests/fixtures/mock_kite_ws_server.py` in full
first to confirm the current content matches what's shown below
(this file was last touched before this session — verify line
numbers before editing).

**Important — why a per-instance flag doesn't work here:** every
`KiteWsMultiplexer._build_ticker()` call (including each rebuild
attempt) constructs a BRAND NEW `KiteTickerShim` via `_ShimFactory`
(see `_ShimFactory.__call__`, ~line 153) — it is never the same
object twice. The `shim` object yielded by
`async with patch_multiplexer_ticker() as shim:` is a `_ShimProxy`
(~line 192) whose `__getattr__` dynamically resolves to whichever
shim was constructed MOST RECENTLY — but `_ShimProxy` has no custom
`__setattr__`, so `shim.wedge_mode = False` from a test would set an
attribute on the PROXY object itself and silently never reach any
real shim. A per-instance flag also can't express "stay wedged
across every rebuild attempt", since each freshly-built shim would
default back to unwedged. Use a MODULE-LEVEL flag instead, read at
construction time by the factory — this is what lets a test control
every subsequent shim a rebuild creates, not just the first one.

In `KiteTickerShim.__init__` (currently ends with
`self.on_error = None`), add:

```python
        self.on_error = None

        # Test-only: mirrors the module-level _force_wedge_mode flag
        # at construction time — see set_wedge_mode() below for why
        # this can't be a per-instance flag a test sets directly.
        self.wedge_mode: bool = _force_wedge_mode
```

Replace the `connect` method body:

```python
    def connect(self, threaded: bool = True) -> None:
        """Simulate an immediate successful connect.

        Calls ``on_connect`` synchronously so tests can assert
        ``mux.connected`` immediately after ``await mux.start()``.

        When ``wedge_mode`` is True, simulates a silently-wedged
        Kite WS registration instead — marks ``_running`` but never
        calls any callback (ASETPLTFRM-470 regression coverage).
        """
        self._running = True
        if self.wedge_mode:
            return
        if self.on_connect:
            self.on_connect(self, None)
```

Add a module-level flag and setter function right before the
`_current_shim: KiteTickerShim | None = None` line:

```python
# Test-only: read by _ShimFactory at construction time so a test can
# control whether the NEXT shim built (including ones built by an
# in-process rebuild, e.g. KiteWsMultiplexer._handle_wedge) starts
# wedged. See KiteTickerShim.connect()'s wedge_mode branch.
_force_wedge_mode: bool = False


def set_wedge_mode(value: bool) -> None:
    """Test helper — controls whether the NEXT KiteTickerShim built
    by _ShimFactory starts in wedge_mode. Call this BEFORE
    mux.start() (or between rebuild attempts, from inside a test) to
    simulate a wedged Kite WS connection (ASETPLTFRM-470)."""
    global _force_wedge_mode
    _force_wedge_mode = value


_current_shim: KiteTickerShim | None = None
```

Update `_ShimFactory.__call__` to read the flag at construction time
(it already constructs a fresh `KiteTickerShim()` per call — no
other change needed there since `KiteTickerShim.__init__`'s new
`self.wedge_mode: bool = _force_wedge_mode` line already reads the
current module-level value).

Reset the flag alongside `_current_shim` in `patch_multiplexer_ticker`
(both the entry and exit resets, so one test's wedge setting can
never leak into the next test):

```python
    global _current_shim
    _current_shim = None
    set_wedge_mode(False)
    factory = _ShimFactory()
    with patch(
        "kiteconnect.KiteTicker",
        factory,
    ):
        yield _ShimProxy()
    _current_shim = None
    set_wedge_mode(False)
```

- [ ] **Step 2: Write the failing tests**

Create `backend/algo/tests/test_ws_wedge_watchdog.py`:

```python
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
        # Let the wedged first attempt time out and trigger the
        # fast-rebuild's NEW _build_ticker() call, THEN un-wedge —
        # the rebuild's freshly-constructed shim reads the flag at
        # construction time, so it connects normally.
        await asyncio.sleep(0.2)
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
```

Before running, confirm the real `_capture_ws_events`-style call
signature for `record_ws_event` matches
`backend/algo/broker/ws_event_store.py`'s actual function signature
(the snippet above mirrors `test_ws_backpressure.py`'s existing
helper verbatim — read that file's `_capture_ws_events` once to
confirm nothing has changed since).

- [ ] **Step 3: Run tests to verify they fail**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ws_wedge_watchdog.py -v`
Expected: FAIL — `AttributeError: 'KiteWsMultiplexer' object has no
attribute '_wedge_escalated'` (or similar — the watchdog doesn't
exist yet).

- [ ] **Step 4: Implement the watchdog**

In `backend/algo/broker/ws_multiplexer.py`, add two new module
constants after the existing `_GAP_TOO_LARGE_S = 3_600` line:

```python
_GAP_TOO_LARGE_S = 3_600  # 1 hour: abandon gap-fill
_CONNECT_TIMEOUT_S = 15.0
_MAX_FAST_REBUILD_ATTEMPTS = 2
```

In `__init__`, after the existing `self._connect_ts: float = 0.0`
block (before the "Backpressure-drop aggregation" comment), add:

```python
        # ASETPLTFRM-470 — connect-timeout watchdog. Every existing
        # reconnect mechanism below is triggered by a Kite callback
        # (on_close/on_error) — in the documented wedge incident, NO
        # callback ever fired for 16 hours. This watchdog is timer-
        # based instead, so it doesn't depend on a callback that may
        # never come.
        self._connect_timeout_task: asyncio.Task | None = None
        self._rebuild_attempts: int = 0
        self._wedge_escalated: bool = False
```

Replace `_connect()` (the whole method):

```python
    async def _connect(self) -> None:
        """Build and connect a KiteTicker instance (non-blocking)."""
        if self._closed:
            return
        try:
            self._kt = self._build_ticker()
            self._kt.connect(threaded=True)
            # _connected flag is set inside on_connect callback.
            self._arm_connect_timeout()
        except Exception:
            _logger.exception(
                "KiteWsMultiplexer: connect() raised for user=%s",
                self._user_id,
            )
            await self._schedule_reconnect()

    def _arm_connect_timeout(self) -> None:
        """Start (or restart) the connect-timeout watchdog task."""
        if self._connect_timeout_task is not None:
            self._connect_timeout_task.cancel()
        self._connect_timeout_task = asyncio.ensure_future(
            self._watch_connect_timeout(),
        )

    async def _watch_connect_timeout(self) -> None:
        """Timer-based wedge detection — does NOT depend on any
        Kite callback firing. See ASETPLTFRM-470."""
        await asyncio.sleep(_CONNECT_TIMEOUT_S)
        if self._connected or self._closed or self._auth_failed:
            return
        _logger.error(
            "KiteWsMultiplexer: connect() wedged (no callback "
            "within %.0fs) user=%s attempt=%d",
            _CONNECT_TIMEOUT_S, self._user_id,
            self._rebuild_attempts + 1,
        )
        self._emit_ws_event("ws_connect_timeout", {
            "timeout_s": _CONNECT_TIMEOUT_S,
            "attempt": self._rebuild_attempts + 1,
        })
        await self._handle_wedge()

    async def _handle_wedge(self) -> None:
        """Shared rebuild/escalate logic for BOTH the connect-timeout
        watchdog and the staleness watchdog (Task 2). Fast in-process
        rebuild for the first _MAX_FAST_REBUILD_ATTEMPTS attempts,
        then escalate (alert, once) and fall back to the existing
        exponential-backoff reconnect loop — escalation is an
        ALERTING signal, not a give-up signal; retries continue."""
        if self._closed or self._auth_failed:
            return
        self._rebuild_attempts += 1
        if self._rebuild_attempts <= _MAX_FAST_REBUILD_ATTEMPTS:
            _logger.warning(
                "KiteWsMultiplexer: fast-rebuild attempt %d/%d "
                "user=%s",
                self._rebuild_attempts, _MAX_FAST_REBUILD_ATTEMPTS,
                self._user_id,
            )
            self._disconnect_kt()
            try:
                self._kt = self._build_ticker()
                self._kt.connect(threaded=True)
                self._arm_connect_timeout()
            except Exception:
                _logger.exception(
                    "KiteWsMultiplexer: fast-rebuild connect() "
                    "raised user=%s", self._user_id,
                )
                await self._handle_wedge()
            return
        if not self._wedge_escalated:
            self._wedge_escalated = True
            _logger.error(
                "KiteWsMultiplexer: wedge ESCALATED after %d fast "
                "rebuild attempts user=%s — falling back to "
                "backoff reconnect loop, manual restart may be "
                "required",
                self._rebuild_attempts, self._user_id,
            )
            self._emit_ws_event("ws_wedge_escalated", {
                "attempts": self._rebuild_attempts,
            })
        self._disconnect_kt()
        self._trigger_reconnect()
```

In `_build_ticker`'s `on_connect` closure, add the reset logic and
watchdog cancellation as the FIRST two lines of the function (before
the existing `self._connect_ts = time.monotonic()` line):

```python
        def on_connect(ws, _resp):
            self._connected = True
            self._rebuild_attempts = 0
            self._wedge_escalated = False
            if self._connect_timeout_task is not None:
                self._connect_timeout_task.cancel()
                self._connect_timeout_task = None
            self._connect_ts = time.monotonic()
            # Reset backoff only if the PREVIOUS connection was stable
```

(keep every line after `self._connect_ts = time.monotonic()`
unchanged — this only adds 4 new lines before the existing comment
block).

In `_schedule_reconnect()`, add `self._arm_connect_timeout()`
immediately after the existing `self._kt.connect(threaded=True)`
line:

```python
            self._disconnect_kt()
            try:
                self._kt = self._build_ticker()
                self._kt.connect(threaded=True)
                self._arm_connect_timeout()
                return  # on_connect callback will set connected flag
            except Exception:
```

In `close()`, add cancellation of `_connect_timeout_task` alongside
the existing `_reconnect_task`/`_gap_fill_task` cancellation blocks.
Insert this new block immediately after the existing
`_reconnect_task` cancellation block (before the "Cancel any
in-flight gap-fill" comment):

```python
            self._reconnect_task = None

        if self._connect_timeout_task is not None:
            self._connect_timeout_task.cancel()
            try:
                await self._connect_timeout_task
            except asyncio.CancelledError:
                pass
            self._connect_timeout_task = None

        # Cancel any in-flight gap-fill (5.2).
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ws_wedge_watchdog.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full multiplexer test suite to check for regressions**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ws_multiplexer.py backend/algo/tests/test_ws_backpressure.py backend/algo/tests/test_multiplexer_health_snapshot.py backend/algo/tests/test_ws_health_endpoint.py -v`
Expected: PASS, no new failures (the existing "immediate successful
connect" shim path is unchanged — `wedge_mode` defaults to `False`).

- [ ] **Step 7: Commit**

```bash
git add backend/algo/broker/ws_multiplexer.py backend/algo/tests/fixtures/mock_kite_ws_server.py backend/algo/tests/test_ws_wedge_watchdog.py
git commit -m "feat(algo): connect-timeout watchdog + auto-rebuild + wedge escalation (ASETPLTFRM-470)"
```

---

## Task 2: Ongoing staleness watchdog (market-hours gated)

**Files:**
- Modify: `backend/algo/broker/ws_multiplexer.py` (constants,
  `start()`, `close()`; add `_watch_staleness_loop`)
- Test: `backend/algo/tests/test_ws_wedge_watchdog.py` (extend)

**Interfaces:**
- Consumes: `KiteWsMultiplexer._handle_wedge()` from Task 1 (exact
  same method — no new rebuild logic, just a different trigger).
  `is_market_open_ist() -> bool` from
  `backend.algo.live.reconciliation` (existing helper, do not
  reimplement).
- Produces: `KiteWsMultiplexer._staleness_task: asyncio.Task | None`
  — no other task depends on this directly.

- [ ] **Step 1: Write the failing tests**

Append to `backend/algo/tests/test_ws_wedge_watchdog.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ws_wedge_watchdog.py -v -k staleness`
Expected: FAIL — `AttributeError: 'KiteWsMultiplexer' object has no
attribute '_watch_staleness_loop_once'`.

- [ ] **Step 3: Implement the staleness watchdog**

In `backend/algo/broker/ws_multiplexer.py`, add two more constants
next to `_MAX_FAST_REBUILD_ATTEMPTS`:

```python
_CONNECT_TIMEOUT_S = 15.0
_MAX_FAST_REBUILD_ATTEMPTS = 2
_STALENESS_CHECK_INTERVAL_S = 60.0
_STALE_TICK_THRESHOLD_S = 90.0
```

In `__init__`, immediately after the `_connect_timeout_task` /
`_rebuild_attempts` / `_wedge_escalated` block added in Task 1, add:

```python
        self._staleness_task: asyncio.Task | None = None
```

Split the staleness check out as a single-iteration method (testable
in isolation, per the tests above) plus a thin looping wrapper.
Add both new methods right after `_watch_connect_timeout`:

```python
    async def _watch_staleness_loop_once(self) -> None:
        """One staleness check — split out from the sleep loop so
        tests can invoke it directly without waiting real wall-clock
        time. See ASETPLTFRM-470."""
        from backend.algo.live.reconciliation import (
            is_market_open_ist,
        )

        if not is_market_open_ist():
            return
        if not self._connected:
            return  # the connect-timeout watchdog owns this case
        has_tokens = bool(self._token_subs or self._universe_tokens)
        if not has_tokens:
            return
        if self.last_tick_at is None:
            return  # freshly connected, no tick expected yet
        age_s = (
            datetime.now(UTC).replace(tzinfo=None)
            - self.last_tick_at
        ).total_seconds()
        if age_s < _STALE_TICK_THRESHOLD_S:
            return
        _logger.error(
            "KiteWsMultiplexer: tick stream stale (%.0fs, "
            "threshold %.0fs) despite connected=True user=%s — "
            "treating as silent stall",
            age_s, _STALE_TICK_THRESHOLD_S, self._user_id,
        )
        self._emit_ws_event("ws_stale_stream_detected", {
            "age_s": int(age_s),
        })
        await self._handle_wedge()

    async def _watch_staleness_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(_STALENESS_CHECK_INTERVAL_S)
            if self._closed:
                return
            await self._watch_staleness_loop_once()
```

In `start()`, add the staleness task after the existing
`await self._connect()` call:

```python
    async def start(self) -> None:
        """Start the WS connection loop."""
        if self._closed:
            raise RuntimeError("Multiplexer already closed")
        self._loop = asyncio.get_running_loop()
        await self._connect()
        self._staleness_task = asyncio.ensure_future(
            self._watch_staleness_loop(),
        )
```

In `close()`, add cancellation of `_staleness_task` right after the
`_connect_timeout_task` cancellation block added in Task 1 (before
the "Cancel any in-flight gap-fill" comment):

```python
            self._connect_timeout_task = None

        if self._staleness_task is not None:
            self._staleness_task.cancel()
            try:
                await self._staleness_task
            except asyncio.CancelledError:
                pass
            self._staleness_task = None

        # Cancel any in-flight gap-fill (5.2).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ws_wedge_watchdog.py -v`
Expected: PASS (5 tests total — the 3 from Task 1 plus these 2)

- [ ] **Step 5: Run the full multiplexer test suite for regressions**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ws_multiplexer.py backend/algo/tests/test_ws_backpressure.py backend/algo/tests/test_multiplexer_health_snapshot.py backend/algo/tests/test_ws_health_endpoint.py -v`
Expected: PASS, no new failures.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/broker/ws_multiplexer.py backend/algo/tests/test_ws_wedge_watchdog.py
git commit -m "feat(algo): market-hours-gated staleness watchdog (ASETPLTFRM-470)"
```

---

## Task 3: Surface `wedge_escalated` on the WS-health route

**Files:**
- Modify: `backend/algo/broker/ws_multiplexer.py:345-357`
  (`health_snapshot`)
- Modify: `backend/algo/routes/live.py:173-187, 1701-1708`
  (`WsHealth`, `get_ws_health`)
- Modify: `backend/algo/tests/test_ws_health_endpoint.py` (update the
  exact-equality assertion + add a new test)

**Interfaces:**
- Consumes: `KiteWsMultiplexer._wedge_escalated: bool` from Task 1.
- Produces: `WsHealth.wedge_escalated: bool` — Task 4's frontend type
  mirrors this exact field name.

- [ ] **Step 1: Write the failing tests**

`backend/algo/tests/test_ws_health_endpoint.py`'s
`test_no_mux_returns_disconnected` currently asserts full dict
equality (`assert body == {...}` with 6 keys, no `wedge_escalated`)
— this WILL break once the field is added with a default. Update it
first (TDD: this edit itself is the "write the failing test" step,
since it'll fail against today's code which doesn't include the key
at all... actually it currently passes; after your Step 2 backend
change it needs the new key present. Make this edit now so the test
accurately describes the target state, then Step 3 will show it
failing for the RIGHT reason — missing key — against today's code):

```python
def test_no_mux_returns_disconnected(app):
    """No multiplexer registered → all-zero disconnected snapshot."""
    client = TestClient(app)
    r = client.get("/v1/algo/live/ws-health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "connected": False,
        "subscriber_count": 0,
        "subscribed_tokens": 0,
        "last_tick_at": None,
        "tick_age_seconds": None,
        "tick_count_today": 0,
        "wedge_escalated": False,
    }
```

Then append a new test to the same file:

```python
def test_wedge_escalated_surfaces_from_mux(app):
    """A multiplexer mid-escalation must surface wedge_escalated=True
    (ASETPLTFRM-470) — this is what drives the frontend's persistent
    wedge banner."""
    uid = UUID(SUPERUSER_ID)
    _seed_mux(user_id=uid, connected=True)
    ws_registry._registry[uid].health_snapshot.return_value = {
        "connected": True,
        "subscriber_count": 2,
        "subscribed_tokens": 4,
        "last_tick_at": None,
        "tick_count_today": 17,
        "wedge_escalated": True,
    }
    client = TestClient(app)
    r = client.get("/v1/algo/live/ws-health")
    assert r.status_code == 200, r.text
    assert r.json()["wedge_escalated"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ws_health_endpoint.py -v`
Expected: FAIL — `test_no_mux_returns_disconnected` fails on the
dict-equality assertion (missing `wedge_escalated` key in the actual
response); `test_wedge_escalated_surfaces_from_mux` fails with a
`KeyError` or assertion mismatch (route doesn't read the new key
yet).

- [ ] **Step 3: Implement**

In `backend/algo/broker/ws_multiplexer.py`'s `health_snapshot`
method, add the new key:

```python
    def health_snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable health view (OBS-1).

        ``last_tick_at`` is left as a ``datetime | None`` — the
        endpoint serialiser converts to ISO 8601 UTC ``Z``.
        """
        return {
            "connected": self._connected,
            "subscriber_count": len(self._queues),
            "subscribed_tokens": len(self._token_subs),
            "last_tick_at": self.last_tick_at,
            "tick_count_today": self.tick_count_today,
            "wedge_escalated": self._wedge_escalated,
        }
```

In `backend/algo/routes/live.py`, add the field to `WsHealth`
(currently ending `tick_count_today: int = 0`):

```python
class WsHealth(BaseModel):
    """OBS-1 — KiteWsMultiplexer health view for the dashboard dot.

    Read-only snapshot served by GET /v1/algo/live/ws-health. All
    fields default to their disconnected values when no
    multiplexer is registered for the user.
    """

    connected: bool = False
    subscriber_count: int = 0
    subscribed_tokens: int = 0
    last_tick_at: str | None = None
    tick_age_seconds: int | None = None
    tick_count_today: int = 0
    wedge_escalated: bool = False
```

In `get_ws_health()`, add the field to the `WsHealth(...)`
construction (currently ending `tick_count_today=int(snap.get("tick_count_today", 0)),`):

```python
        return WsHealth(
            connected=bool(snap.get("connected")),
            subscriber_count=int(snap.get("subscriber_count", 0)),
            subscribed_tokens=int(snap.get("subscribed_tokens", 0)),
            last_tick_at=_iso_utc(last),
            tick_age_seconds=age,
            tick_count_today=int(snap.get("tick_count_today", 0)),
            wedge_escalated=bool(snap.get("wedge_escalated", False)),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ws_health_endpoint.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the broader route + multiplexer suites for regressions**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ws_multiplexer.py backend/algo/tests/test_multiplexer_health_snapshot.py backend/algo/tests/test_ws_wedge_watchdog.py -v`
Expected: PASS, no new failures.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/broker/ws_multiplexer.py backend/algo/routes/live.py backend/algo/tests/test_ws_health_endpoint.py
git commit -m "feat(algo): surface wedge_escalated on GET /algo/live/ws-health (ASETPLTFRM-470)"
```

---

## Task 4: Frontend — persistent wedge banner

**Files:**
- Modify: `frontend/hooks/useWsHealth.ts` (`WsHealth` interface)
- Create: `frontend/components/algo-trading/live/LiveWsWedgeBanner.tsx`
- Modify: `frontend/components/algo-trading/live/LiveHeaderStrip.tsx`
- Test: `frontend/components/algo-trading/live/__tests__/LiveWsWedgeBanner.test.tsx`
  (create — check `find frontend/components/algo-trading/live -type d`
  first; the `__tests__` subdirectory does not exist yet under `live/`
  and must be created)

**Interfaces:**
- Consumes: `WsHealth.wedge_escalated: boolean` from Task 3 (exact
  field name).
- Produces: `LiveWsWedgeBanner({ armed: boolean, wedgeEscalated:
  boolean })` — no later task depends on this.

- [ ] **Step 1: Add the field to the hook type**

In `frontend/hooks/useWsHealth.ts`, in the `WsHealth` interface
(currently ending `tick_count_today: number;`), add:

```typescript
export interface WsHealth {
  connected: boolean;
  subscriber_count: number;
  subscribed_tokens: number;
  last_tick_at: string | null;
  tick_age_seconds: number | null;
  tick_count_today: number;
  wedge_escalated: boolean;
}
```

- [ ] **Step 2: Write the failing test**

Create `frontend/components/algo-trading/live/__tests__/LiveWsWedgeBanner.test.tsx`:

```tsx
/**
 * LiveWsWedgeBanner — unit tests (ASETPLTFRM-470).
 *
 * Verifies:
 * 1. Banner renders when armed AND wedgeEscalated.
 * 2. Banner is absent when armed=false, even if wedgeEscalated=true.
 * 3. Banner is absent when wedgeEscalated=false, even if armed=true.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { LiveWsWedgeBanner } from "../LiveWsWedgeBanner";

describe("LiveWsWedgeBanner", () => {
  it("renders when armed and wedgeEscalated are both true", () => {
    render(
      <LiveWsWedgeBanner armed={true} wedgeEscalated={true} />,
    );
    const banner = screen.getByTestId("live-ws-wedge-banner");
    expect(banner).toBeDefined();
    expect(banner.textContent).toContain("WEDGED");
  });

  it("does not render when armed is false", () => {
    render(
      <LiveWsWedgeBanner armed={false} wedgeEscalated={true} />,
    );
    expect(
      screen.queryByTestId("live-ws-wedge-banner"),
    ).toBeNull();
  });

  it("does not render when wedgeEscalated is false", () => {
    render(
      <LiveWsWedgeBanner armed={true} wedgeEscalated={false} />,
    );
    expect(
      screen.queryByTestId("live-ws-wedge-banner"),
    ).toBeNull();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/algo-trading/live/__tests__/LiveWsWedgeBanner.test.tsx`
Expected: FAIL — cannot find module `../LiveWsWedgeBanner`.

- [ ] **Step 4: Implement the banner component**

Create `frontend/components/algo-trading/live/LiveWsWedgeBanner.tsx`:

```tsx
"use client";

/**
 * LiveWsWedgeBanner — persistent, high-visibility alert shown when
 * a user's live strategy is armed but the Kite WS connection has
 * wedged and repeated auto-rebuild attempts have failed
 * (ASETPLTFRM-470). Deliberately NOT a hover tooltip — the WS health
 * dot (LiveWsHealthDot) already covers the passive/subtle signal;
 * this is the loud one for when auto-recovery hasn't worked.
 */

interface Props {
  armed: boolean;
  wedgeEscalated: boolean;
}

export function LiveWsWedgeBanner({ armed, wedgeEscalated }: Props) {
  if (!armed || !wedgeEscalated) return null;

  return (
    <div
      className="flex items-start gap-2 rounded-md border
        border-rose-300 bg-rose-50 px-3 py-2
        dark:border-rose-700 dark:bg-rose-950/40"
      data-testid="live-ws-wedge-banner"
      role="alert"
    >
      <span
        className="mt-px text-base leading-none"
        aria-hidden="true"
      >
        🔴
      </span>
      <p className="text-xs text-rose-800 dark:text-rose-200">
        <span className="font-semibold">
          KITE WS CONNECTION WEDGED
        </span>
        {" — the market-data connection has failed to reconnect "}
        {"after repeated attempts. Live strategies are running "}
        {"BLIND (no signals will fire on missing data). A backend "}
        {"restart is likely required — contact the operator."}
      </p>
    </div>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/algo-trading/live/__tests__/LiveWsWedgeBanner.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 6: Wire the banner into LiveHeaderStrip**

Read `frontend/components/algo-trading/live/LiveHeaderStrip.tsx` in
full first to confirm current line numbers before editing (it may
have shifted since this plan was written).

Add the import (alongside the existing `LiveWsHealthDot`/
`LiveModeChip` imports at the top):

```typescript
import { useLiveDashboardSummary } from "@/hooks/useLiveDashboardSummary";
import { usePaperRuns } from "@/hooks/usePaperRuns";
import { useWsHealth } from "@/hooks/useWsHealth";

import { LiveWsHealthDot } from "../LiveWsHealthDot";
import { LiveModeChip } from "./LiveModeChip";
import { LiveWsWedgeBanner } from "./LiveWsWedgeBanner";
```

Inside `LiveHeaderStrip()`, add the hook call alongside the existing
`useLiveDashboardSummary()`/`usePaperRuns()` calls:

```typescript
export function LiveHeaderStrip() {
  const { summary } = useLiveDashboardSummary();
  const { runs } = usePaperRuns();
  const { health } = useWsHealth();
  const armed = runs.some(
    (r) => r.mode === "live" && !r.dry_run,
  );
```

Change the function's return statement from a single `<div>` to a
`<>` fragment wrapping the existing sticky strip PLUS the banner
rendered below it (the banner is a block-level alert, not another
inline KPI item, so it does not belong inside the `flex` row itself).
Replace the closing of the component (currently
`<LiveWsHealthDot />` / `</div>` / `</span>` / `</div>` / `);` /
`}`) with:

```typescript
      <div
        className="flex items-center gap-1 text-xs text-slate-500"
        data-testid="live-ws-age"
      >
        <span>WS</span>
        <LiveWsHealthDot />
      </div>
      </div>
      <LiveWsWedgeBanner
        armed={armed}
        wedgeEscalated={health?.wedge_escalated ?? false}
      />
    </>
  );
}
```

And change the opening of the return statement from
`return (\n    <div\n      className="sticky top-0 ...` to:

```typescript
  return (
    <>
      <div
        className="sticky top-0 z-10 flex flex-wrap items-center gap-3
          bg-white/95 dark:bg-slate-900/95 backdrop-blur border-b
          border-slate-200 dark:border-slate-700 px-4 py-3"
        data-testid="live-header-strip"
      >
```

(i.e. wrap the pre-existing sticky `<div>...</div>` in a `<>...</>`
fragment, and add `<LiveWsWedgeBanner ... />` as a sibling
immediately after that `<div>` closes, before the fragment closes.)

- [ ] **Step 7: Run the broader algo-trading component suite for regressions**

Run: `cd frontend && npx vitest run components/algo-trading/`
Expected: PASS, no new failures (pre-existing unrelated failures, if
any, must match the exact same count/names as before this change —
compare against a `git stash` baseline run if any fail).

- [ ] **Step 8: Commit**

```bash
git add frontend/hooks/useWsHealth.ts frontend/components/algo-trading/live/LiveWsWedgeBanner.tsx frontend/components/algo-trading/live/LiveHeaderStrip.tsx frontend/components/algo-trading/live/__tests__/LiveWsWedgeBanner.test.tsx
git commit -m "feat(algo): persistent WS-wedge banner in Live header strip (ASETPLTFRM-470)"
```

---

## Self-Review Notes

- **Spec coverage:** All 4 design components covered — Task 1
  (connect-timeout watchdog + fast-rebuild + escalation), Task 2
  (staleness watchdog), Task 3 (backend surfacing), Task 4 (frontend
  banner). The spec's "Non-goals" (Twisted root-cause, auth_failed
  path changes, cross-process health checks) are correctly NOT
  covered by any task.
- **Type/name consistency:** `_wedge_escalated` (Task 1) →
  `wedge_escalated` in `health_snapshot()` (Task 3) →
  `WsHealth.wedge_escalated` (Task 3) → `WsHealth` TS interface (Task
  4) → `LiveWsWedgeBanner`'s `wedgeEscalated` prop (Task 4, camelCase
  at the React boundary per this codebase's existing convention —
  compare `off_universe_tickers` → `offUniverseTickers` from a prior
  session's PR #309). Consistent throughout.
- **Regression risk called out explicitly:** Task 3 Step 1 flags that
  `test_no_mux_returns_disconnected`'s exact-dict-equality assertion
  WILL break once the field is added — the plan updates it as part of
  the same task rather than leaving it as a surprise CI failure.
- **Caught during self-review:** the first draft of Task 1's test
  fixture design set `wedge_mode` as a per-instance attribute a test
  would mutate directly on the yielded `shim` proxy
  (`shim.wedge_mode = False`). Traced through
  `patch_multiplexer_ticker()`'s actual mechanics and found this
  would silently no-op — `_ShimProxy` has no `__setattr__` override,
  so the write lands on the proxy object itself, never on the real
  shim; and since every rebuild constructs a BRAND NEW
  `KiteTickerShim`, a per-instance flag can't express "stay wedged
  across every rebuild attempt" even if writes did work. Fixed by
  making it a module-level flag (`_force_wedge_mode` +
  `set_wedge_mode()`) read fresh at construction time by
  `_ShimFactory` — this is what lets a test control every subsequent
  shim a rebuild creates, not just the first. Both affected tests
  (`test_connect_timeout_triggers_rebuild`,
  `test_wedge_escalates_after_max_fast_attempts`) were rewritten to
  use `set_wedge_mode()` instead of the broken pattern.
