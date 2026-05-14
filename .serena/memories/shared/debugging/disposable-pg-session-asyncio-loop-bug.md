# Pipeline scheduler-job asyncpg cross-loop crash

## Symptom

Scheduled job fails with:

```
Task <Task pending name='Task-3719' coro=<run_intraday_bars_daily_ingest_job()
running at /app/backend/algo/jobs/intraday_bars_daily_ingest.py:229>
cb=[_run_until_complete_cb() at /usr/local/lib/python3.12/asyncio/base_events.py:181]>
got Future <Future pending cb=[BaseProtocol._on_waiter_completed()]>
attached to a different loop
```

Hit on 2026-05-14 during `intraday_bars_daily_ingest` step of the
`Intraday Bars Daily Pipeline`.

## Root cause

`backend/db/engine.py::get_engine()` is decorated `@lru_cache` so the
async engine + its asyncpg pool are constructed ONCE — typically by
the first uvicorn request handler. The pool's connections are
implicitly bound to the event loop that created them.

Scheduler jobs in `backend/jobs/executor.py` wrap their async
implementations with `asyncio.run(async_impl(payload))`, which
creates a FRESH event loop every fire. When the async impl then
calls `get_session_factory()` it gets back the cached factory →
which holds the asyncpg pool from the ORIGINAL loop. Issuing any
query on that session creates a Future bound to the original loop;
awaiting it from the fresh loop raises the cross-loop error.

The pre-2026-05-14 hot tables that masked the bug:
- `recommendations` / `recommendation_outcomes` / `recommendation_cleanup`
  — used `asyncio.run` but their async paths happened to construct
  their own engines locally
- `intraday_bars_daily_ingest`, `algo_reconciliation`,
  `risk_state_reset`, `reauth_notify` — all called
  `get_session_factory()` directly. Worked the FIRST time after a
  uvicorn restart (loop A = uvicorn loop, loop A = job loop by
  coincidence), then crashed on the second scheduled fire.

## Fix

Added `backend/db/engine.py::disposable_pg_session()` — async
context manager that creates a per-call `NullPool` `create_async_engine`,
yields a session, and disposes on exit. All four scheduler-driven
async PG callers migrated:

- `backend/algo/jobs/intraday_bars_daily_ingest.py`
- `backend/algo/jobs/algo_reconciliation.py`
- `backend/algo/jobs/risk_state_reset.py`
- `backend/algo/jobs/reauth_notify.py`

Pattern:

```python
from backend.db.engine import disposable_pg_session

async def run_my_scheduler_job(payload):
    async with disposable_pg_session() as session:
        await session.execute(text("..."))
        await session.commit()
```

`get_session_factory()` stays for FastAPI request handlers and
uvicorn-loop background tasks — its native loop, faster on reuse.
Both `get_engine` and `get_session_factory` docstrings updated to
spell out the loop-binding rule + when to use which.

## How to spot future regressions

Any of these is a signal:
- New `@register_job(...)` whose async impl calls
  `get_session_factory()` directly — should use
  `disposable_pg_session()` instead.
- Manual `asyncio.run(...)` of an async helper that touches the
  cached engine.
- Test mocks that monkey-patch `get_session_factory` in scheduler-
  job tests — they should patch `disposable_pg_session` instead
  (see `backend/algo/jobs/tests/test_intraday_bars_daily_ingest.py`
  fixture for the correct shape: async context manager directly,
  no factory layer).

## Verification

Two consecutive `asyncio.run(run_intraday_bars_daily_ingest_job(...))`
from a fresh REPL now both succeed; previously the second crashed
on the cross-loop Future. 12/12 keeper unit tests pass with the new
fixture shape.

## Files

- `backend/db/engine.py` — added `disposable_pg_session`
- `backend/algo/jobs/intraday_bars_daily_ingest.py`
- `backend/algo/jobs/algo_reconciliation.py`
- `backend/algo/jobs/risk_state_reset.py`
- `backend/algo/jobs/reauth_notify.py`

## Cross-refs

- CLAUDE.md §5.1 "Scheduler-job PG access"
- CLAUDE.md §6.7 "Sync→async migration"
- `pg-nullpool-sync-async-bridge` memory
- `asyncpg-sync-async-bridge` memory
- Shipped via PR #221 (squash f140fd6)
