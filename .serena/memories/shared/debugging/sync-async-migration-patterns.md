# Sync-to-Async Migration Patterns

## Problem
When converting sync endpoint functions to async (asyncpg), several patterns
break silently. The coroutine object is returned instead of the actual value,
with no compile-time error.

## Pattern 1: Missing `await` on repo methods
After making repo methods async, ALL callers must be found via
`grep -rn "repo\."` across auth/, backend/, stocks/. Missing `await`
returns a coroutine object that silently passes truthiness checks.

## Pattern 2: Test mocks after async conversion
Replace `MagicMock` with `AsyncMock` for any mocked repo method.
Awaiting a plain MagicMock returns a coroutine, not the mock value.

## Pattern 3: Sync callers of async PG code
For scheduler threads and background jobs running in sync context:
- Pass a *callable* not a coroutine: `_run_pg(_call)` not `_run_pg(_call())`
- Use `_pg_session()` (fresh engine) not `get_session_factory()` (cached, binds to uvicorn loop)
- Both helpers live in `stocks/repository.py`

## Pattern 4: Thread-local state across executor boundaries
`threading.local()` values set on the async event loop thread are invisible
to `ThreadPoolExecutor` worker threads (via `run_in_executor`).
Fix: set thread-local inside the worker closure, not outside it.
Example: `set_current_user()` in `routes.py` must be inside the
executor function, not before `run_in_executor()`.

## Pattern 5: asyncpg pool_pre_ping
Always set `pool_pre_ping=True` in `create_async_engine()`. Without it,
uvicorn hot-reload leaves stale connections from the old process, causing
"SSL connection has been closed unexpectedly" errors.

## Files
- `backend/db/engine.py` — async engine config
- `stocks/repository.py` — `_run_pg()` + `_pg_session()` bridges
- `backend/routes.py` — thread-local in executor closures
- `backend/tools/_ticker_linker.py` — `set_current_user` / `get_current_user`
