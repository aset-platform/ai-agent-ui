# Sync→async bridge inside the paper runtime

## Symptom

```
RuntimeError: got Future <Future pending …> attached to a
different loop
asyncpg.exceptions._base.InterfaceError: cannot perform
operation: another operation is in progress
```

Cascade of follow-on `InterfaceError: another operation in
progress` errors on subsequent PG calls (connection state
gets corrupted, takes minutes to recover).

## Trigger

Sync code path that's nested INSIDE a running `async def`
function tries to invoke an async helper via:

```python
import threading, asyncio

threading.Thread(
    target=lambda: asyncio.run(_coro()),
    daemon=True,
).start()
```

Where `_coro` touches PG via the cached
`get_session_factory()` (or anything that resolves through
it — `reserve()`, `transition()`, most repo helpers).

Concrete example: `_emit_paper_budget_lifecycle` in
`backend/algo/paper/runtime.py` initially used this pattern
because the per-bar fill paths (lines around the
`_positions.apply_fill(...)` calls) are sync code, and the
budget helpers are async. The fill path runs inside paper
runtime's `async def run()`, so the daemon-thread bridge
felt safe — it isn't.

## Why

The cached SQLAlchemy session factory is bound to the
FIRST event loop that instantiated it — usually uvicorn's
main loop at FastAPI startup. asyncpg connections live
inside that loop. Spawning a fresh `asyncio.run()` in a
daemon thread creates a new loop that doesn't share the
connection pool. Futures created by asyncpg's protocol
state machine reference the original loop; await-ing them
on the new loop trips the runtime check.

## Fix

The sync code is on the call stack of an already-running
coroutine, so the running loop is reachable:

```python
import asyncio

loop = asyncio.get_running_loop()
task = loop.create_task(_coro())

def _log_done(t: asyncio.Task) -> None:
    try:
        exc = t.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _logger.warning("…", exc_info=exc)

task.add_done_callback(_log_done)
```

`create_task` schedules `_coro` on the SAME loop the
caller is running on. PG futures stay attached. The fill
path returns immediately and the budget writes happen
concurrently. Add a done-callback to log exceptions —
`create_task` otherwise swallows them silently.

## When `threading.Thread + asyncio.run` IS OK

Use it ONLY when the caller is genuinely sync (no
enclosing loop) AND the coroutine uses
`disposable_pg_session()` (NullPool, per-call factory, no
cached state). Existing reference: `_mark_recs_acted_on`
in `auth/endpoints/ticker_routes.py` — it's called from
sync threading background, opens a NullPool engine,
disposes after one use.

## Verification

Look for the symptom messages in `docker compose logs
backend`. If you see "Future attached to a different
loop" AFTER making sync→async bridge changes, this is
almost certainly the cause. Confirmed 2026-05-26 during
the paper-mode coverage end-to-end test (see
`mem:paper-mode-budget-tab-coverage`).

## Related

- `mem:pg-nullpool-sync-async-bridge`
- `mem:contextvar-run-in-executor`
- `mem:disposable-pg-session-asyncio-loop-bug`
- `mem:asyncpg-sync-async-bridge`
