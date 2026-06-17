# Live Runtime: Iceberg flush_events must run in asyncio.to_thread()

## Bug (fixed 2026-06-17, PR #262 commit `0053875`)

`_flush_events_now()` in `backend/algo/live/runtime.py` called `flush_events()`
synchronously from an async function. Each Iceberg commit takes ~1.7 s of blocking
I/O. With 712 tickers all closing bars simultaneously at market close (15:30 IST),
the event loop was blocked continuously → FastAPI health probes timed out (5 s) →
Docker marked the container **unhealthy** (40 consecutive failures, ~6 min downtime).

## Full write path

```
flush_events(rows)                                       # event_writer.py
  → StockRepository()._retry_commit("algo.events", "append", arrow_table)
    → with _commit_lock  (threading.Lock, class-level — serialises concurrent callers)
        tbl.append(arrow_table)      # PyIceberg → Parquet file on local disk
        → invalidate_metadata()      # flushes DuckDB in-process metadata cache
        → _invalidate_cache()        # busts Redis read key
```

**Storage: local filesystem Iceberg** (Parquet + SQLite catalog at
`~/.ai-agent-ui/iceberg/`). Not PG. Not Redis.

`asyncio.to_thread()` works because all work releases the GIL:
- Parquet write → PyArrow C extension
- SQLite catalog update → C extension
- Redis invalidation → network I/O

`_commit_lock` is class-level: two concurrent `asyncio.to_thread(flush_events)`
calls block the *thread*, not the event loop — correct behaviour.

## Fix pattern

```python
async def _flush_events_now(self) -> None:
    if not self._events:
        return
    rows = self._events[:]   # snapshot before await
    self._events = []
    try:
        await asyncio.to_thread(flush_events, rows)
    except Exception:
        _logger.warning("in-session flush failed", exc_info=True)
        self._events = rows + self._events   # re-buffer on failure
```

All 4 in-session call sites use `await self._flush_events_now()`.
Direct `flush_events(self._events)` in the drain finally-block and
`cancel_in_flight_orders` also wrapped in `asyncio.to_thread`.

## Rule

**Any synchronous Iceberg / StockRepository write inside an async function
MUST use `asyncio.to_thread()`.** See `mem:pg-nullpool-sync-async-bridge` for
the equivalent PG pattern.
