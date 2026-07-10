# Kite WS Wedge Watchdog — Design Spec

**Jira:** ASETPLTFRM-470 (High priority)

## Problem

On 2026-07-05 15:40 IST, after a normal Zerodha re-login following an
earlier 403 auth-failure, `KiteWsMultiplexer.start()` →
`KiteTicker.connect(threaded=True)` never actually established a
WebSocket connection. No `on_connect`, `on_close`, or `on_error`
callback fired for 16 hours. `_connected`, `_closed`, and
`auth_failed` all stayed at their initial `False` values, so
`get_or_create_multiplexer()`'s reuse check (`if mux is not None and
not mux._closed and not mux.auth_failed: return mux`) kept handing
back the same dead-but-not-marked-dead instance on every subsequent
call. The Algo Trading UI showed "LIVE ARMED" the whole time; only
the WS health hover tooltip revealed `disconnected`. Real-money live
trading ran with zero market data for 16 hours, caught only because
the user happened to check the tooltip before market open.

Root cause is believed to be `kiteconnect`'s underlying Twisted
`reactor` (a process-wide singleton) reaching a state where new
`connectWS()` registrations are silently never serviced. The exact
Twisted-level trigger was never confirmed (no traceback surfaced —
likely swallowed inside the daemon reactor thread). The only recovery
observed was a full backend process restart.

**Every existing detection/recovery mechanism in
`backend/algo/broker/ws_multiplexer.py` is callback-triggered**
(`on_close` → `_trigger_reconnect` → `_schedule_reconnect`). In the
documented incident, no callback ever fired, so none of that
machinery could help. Any fix must be timer-based, not
callback-dependent.

## Known open risk (accepted, not solved here)

If the wedge really is at the process-wide Twisted reactor layer, a
fresh `KiteTicker` built in the *same process* might hit the same
wedged reactor and also silently fail to connect. This design cannot
fully rule that out without reproducing the wedge live. It is
mitigated, not eliminated, by:

- Auto-rebuild is attempted first (cheap, might just work if the
  wedge was scoped to that one WS registration rather than the whole
  reactor).
- If rebuild attempts keep failing, `wedge_escalated` fires a loud,
  persistent signal (backend event + frontend banner) rather than
  retrying forever in silence — guaranteeing visibility even in the
  worst case where only a manual restart truly recovers.
- Root-causing the exact Twisted-level failure mode is explicitly
  out of scope for this spec (the ticket calls it a "stretch" goal).

## Architecture

Four components, all inside `backend/algo/broker/ws_multiplexer.py`
except where noted.

### 1. Connect-timeout watchdog

New instance state (in `KiteWsMultiplexer.__init__`):

```python
self._connect_timeout_task: asyncio.Task | None = None
self._rebuild_attempts: int = 0
self._wedge_escalated: bool = False
```

New module constants:

```python
_CONNECT_TIMEOUT_S = 15.0
_MAX_FAST_REBUILD_ATTEMPTS = 2
```

Every place `self._kt.connect(threaded=True)` is called — currently
`_connect()` (line ~378) and `_schedule_reconnect()` (line ~641) —
must, immediately after the call, arm a watchdog task:

```python
self._connect_timeout_task = asyncio.ensure_future(
    self._watch_connect_timeout(),
)
```

`_watch_connect_timeout()`:

```python
async def _watch_connect_timeout(self) -> None:
    await asyncio.sleep(_CONNECT_TIMEOUT_S)
    if self._connected or self._closed or self._auth_failed:
        return  # connected normally, or torn down/auth-failed meanwhile
    _logger.error(
        "KiteWsMultiplexer: connect() wedged (no callback within "
        "%.0fs) user=%s attempt=%d",
        _CONNECT_TIMEOUT_S, self._user_id, self._rebuild_attempts + 1,
    )
    self._emit_ws_event("ws_connect_timeout", {
        "timeout_s": _CONNECT_TIMEOUT_S,
        "attempt": self._rebuild_attempts + 1,
    })
    await self._handle_wedge()
```

`on_connect` (the success path) must cancel any pending watchdog task
and reset the escalation state:

```python
def on_connect(ws, _resp):
    self._connected = True
    self._rebuild_attempts = 0
    self._wedge_escalated = False
    if self._connect_timeout_task is not None:
        self._connect_timeout_task.cancel()
        self._connect_timeout_task = None
    ...  # existing body unchanged
```

`close()` must also cancel `_connect_timeout_task`, mirroring the
existing `_reconnect_task`/`_gap_fill_task` cancellation pattern.

### 2. `_handle_wedge()` — shared rebuild/escalate logic

```python
async def _handle_wedge(self) -> None:
    if self._closed or self._auth_failed:
        return
    self._rebuild_attempts += 1
    if self._rebuild_attempts <= _MAX_FAST_REBUILD_ATTEMPTS:
        _logger.warning(
            "KiteWsMultiplexer: fast-rebuild attempt %d/%d user=%s",
            self._rebuild_attempts, _MAX_FAST_REBUILD_ATTEMPTS,
            self._user_id,
        )
        self._disconnect_kt()
        try:
            self._kt = self._build_ticker()
            self._kt.connect(threaded=True)
            self._connect_timeout_task = asyncio.ensure_future(
                self._watch_connect_timeout(),
            )
        except Exception:
            _logger.exception(
                "KiteWsMultiplexer: fast-rebuild connect() raised "
                "user=%s", self._user_id,
            )
            await self._handle_wedge()  # count this as another attempt
        return
    # Exhausted fast attempts — escalate (once) and fall back to the
    # existing exponential-backoff reconnect loop. Escalation is an
    # ALERTING signal, not a give-up signal: retries continue at the
    # existing backoff cadence (already caps at _MAX_BACKOFF_S=60s),
    # since a transient issue might still self-resolve and there is
    # no meaningful cost to continuing at that cadence.
    if not self._wedge_escalated:
        self._wedge_escalated = True
        _logger.error(
            "KiteWsMultiplexer: wedge ESCALATED after %d fast "
            "rebuild attempts user=%s — falling back to backoff "
            "reconnect loop, manual restart may be required",
            self._rebuild_attempts, self._user_id,
        )
        self._emit_ws_event("ws_wedge_escalated", {
            "attempts": self._rebuild_attempts,
        })
    self._disconnect_kt()
    self._trigger_reconnect()
```

`_schedule_reconnect()`'s existing loop body (after
`self._kt.connect(threaded=True)`) also needs the same
`_connect_timeout_task` arm — a wedge can recur on ANY reconnect
attempt, not just the first.

### 3. Ongoing staleness watchdog (market-hours gated)

New periodic task, started once from `start()` alongside the initial
`_connect()` call:

```python
self._staleness_task = asyncio.ensure_future(
    self._watch_staleness_loop(),
)
```

New constants:

```python
_STALENESS_CHECK_INTERVAL_S = 60.0
_STALE_TICK_THRESHOLD_S = 90.0
```

```python
async def _watch_staleness_loop(self) -> None:
    from backend.algo.live.reconciliation import is_market_open_ist

    while not self._closed:
        await asyncio.sleep(_STALENESS_CHECK_INTERVAL_S)
        if self._closed:
            return
        if not is_market_open_ist():
            continue
        if not self._connected:
            continue  # connect-timeout watchdog already owns this case
        has_tokens = bool(self._token_subs or self._universe_tokens)
        if not has_tokens:
            continue
        if self.last_tick_at is None:
            continue  # freshly connected, no tick expected yet
        age_s = (
            datetime.now(UTC).replace(tzinfo=None) - self.last_tick_at
        ).total_seconds()
        if age_s < _STALE_TICK_THRESHOLD_S:
            continue
        _logger.error(
            "KiteWsMultiplexer: tick stream stale (%.0fs, threshold "
            "%.0fs) despite connected=True user=%s — treating as "
            "silent stall",
            age_s, _STALE_TICK_THRESHOLD_S, self._user_id,
        )
        self._emit_ws_event("ws_stale_stream_detected", {
            "age_s": int(age_s),
        })
        await self._handle_wedge()
```

`close()` must also cancel `_staleness_task`.

### 4. Surfacing to the frontend

**Backend** — `health_snapshot()` gains one key:

```python
def health_snapshot(self) -> dict[str, Any]:
    return {
        "connected": self._connected,
        "subscriber_count": len(self._queues),
        "subscribed_tokens": len(self._token_subs),
        "last_tick_at": self.last_tick_at,
        "tick_count_today": self.tick_count_today,
        "wedge_escalated": self._wedge_escalated,
    }
```

`backend/algo/routes/live.py`'s `WsHealth` model gains
`wedge_escalated: bool = False`; `get_ws_health()` reads it from the
snapshot the same way it reads `connected`.

**Frontend** — `frontend/hooks/useWsHealth.ts`'s `WsHealth` interface
gains `wedge_escalated: boolean`.

New component `frontend/components/algo-trading/live/LiveWsWedgeBanner.tsx`
(colocated with `LiveHeaderStrip.tsx`, not the flat
`components/algo-trading/` dir, since it's Live-tab-specific):

```tsx
"use client";

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
      <span className="mt-px text-base leading-none" aria-hidden="true">
        🔴
      </span>
      <p className="text-xs text-rose-800 dark:text-rose-200">
        <span className="font-semibold">
          KITE WS CONNECTION WEDGED
        </span>
        {" — the market-data connection has failed to reconnect after "}
        {"repeated attempts. Live strategies are running BLIND (no "}
        {"signals will fire on missing data). A backend restart is "}
        {"likely required — contact the operator."}
      </p>
    </div>
  );
}
```

Wired into `LiveHeaderStrip.tsx`: reuse the existing `armed` boolean
already computed there, pass `health?.wedge_escalated ?? false`
(`useWsHealth()` is not currently called in this file — add it,
mirroring how `LiveWsHealthDot` already self-drives via the same
hook; both will independently call `useWsHealth()`, which is fine —
SWR dedupes concurrent identical-key requests).

## Testing

Extend `backend/algo/tests/fixtures/mock_kite_ws_server.py`'s
`KiteTickerShim` with an opt-in wedge mode:

```python
def __init__(self, api_key: str, access_token: str) -> None:
    ...
    self.wedge_mode: bool = False  # set by test before connect()

def connect(self, threaded: bool = True) -> None:
    self._running = True
    if self.wedge_mode:
        return  # simulate a connect() that never calls back
    if self.on_connect:
        self.on_connect(self, None)
```

New test file `backend/algo/tests/test_ws_wedge_watchdog.py`:

1. `test_connect_timeout_triggers_rebuild` — shim starts in
   `wedge_mode=True`; patch `_CONNECT_TIMEOUT_S` down to something
   tiny (e.g. `0.05`) via `monkeypatch` on the module constant so the
   test doesn't sleep 15 real seconds; after the timeout, flip
   `shim.wedge_mode = False` before the rebuild's `connect()` call
   fires (or use a counter on the shim to succeed on the 2nd call);
   assert `mux.connected` becomes `True` and `shim.connect` was
   called more than once.
2. `test_wedge_escalates_after_max_fast_attempts` — shim stays wedged
   permanently across all attempts; drive time forward (or patch the
   timeout constant down) through `_MAX_FAST_REBUILD_ATTEMPTS + 1`
   cycles; assert `mux._wedge_escalated is True` and an
   `ws_wedge_escalated` event was recorded (patch
   `_emit_ws_event`/`record_ws_event` to capture calls rather than
   hitting real Redis).
3. `test_staleness_watchdog_fires_when_ticks_stop` — connect
   normally (shim default), subscribe a token, manually set
   `mux.last_tick_at` to a stale timestamp, patch
   `is_market_open_ist` to return `True`, invoke the staleness check
   body directly (or drive one loop iteration with a patched short
   interval) and assert `_handle_wedge` was called (patch/spy it).
4. `test_staleness_watchdog_skips_outside_market_hours` — same setup
   but patch `is_market_open_ist` to return `False`; assert no
   rebuild attempted.
5. `test_successful_reconnect_clears_escalation` — force
   `mux._wedge_escalated = True` and `_rebuild_attempts = 2`
   directly, then simulate a real `on_connect` firing (shim's normal
   path); assert both reset to `False`/`0`.
6. `test_health_snapshot_includes_wedge_escalated` — trivial: set the
   flag, call `health_snapshot()`, assert the key is present and
   correct.
7. Backend route test (extend `backend/algo/tests/test_ws_health_endpoint.py`
   if that file exists and covers `get_ws_health`, else add there):
   `GET /algo/live/ws-health` surfaces `wedge_escalated` from the
   mocked multiplexer's `health_snapshot()`.
8. Frontend `frontend/components/algo-trading/live/__tests__/LiveWsWedgeBanner.test.tsx`:
   renders when `armed=true, wedgeEscalated=true`; renders nothing
   for `armed=false` (regardless of `wedgeEscalated`); renders
   nothing for `wedgeEscalated=false` (regardless of `armed`).

## Non-goals (explicitly out of scope)

- Root-causing the exact Twisted reactor failure mode (ticket's own
  "stretch" goal).
- Any change to the 403/`auth_failed` path — that mechanism already
  works correctly (confirmed by the ticket's own description) and is
  untouched by this design.
- Cross-process/Redis-backed health checks — the registry is
  explicitly process-local by design (single Uvicorn worker
  assumption, documented in `ws_registry.py`'s module docstring);
  out of scope to change that here.
