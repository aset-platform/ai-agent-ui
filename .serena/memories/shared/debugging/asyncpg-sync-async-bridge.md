# asyncpg Sync-to-Async Bridge Patterns

## Problem
SQLAlchemy async (asyncpg) requires `await`. But scheduler threads,
background jobs, and cache warmup run in sync context. Calling
`asyncio.run()` directly conflicts with uvicorn's running event loop.

## Solution: `_run_pg()` helper

Located in `stocks/repository.py` on `StockRepository`:

```python
def _run_pg(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # In async context — use thread to avoid nested loop
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)
```

## When to use
- `StockRepository` sync methods that delegate to `backend/db/pg_stocks.py`
- `cache_warmup.py` thread calls wrapped with `asyncio.run()`
- `backend/tools/_ticker_linker.py` — fire-and-forget from thread

## When NOT to use
- FastAPI async endpoints — use `await` directly
- `UserRepository` facade — already async with `_session_scope()`

## Common errors
- `cannot perform operation: another operation is in progress` →
  add `pool_pre_ping=True` to engine
- `This event loop is already running` → use `_run_pg()` not
  bare `asyncio.run()`
- `NaTType does not support astimezone` → sanitize with `pd.isna()`
  before PG insert
