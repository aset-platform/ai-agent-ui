# Sync blocking I/O in async FastAPI routes

## Pattern that freezes uvicorn

Calling synchronous blocking functions directly inside `async def` route handlers
blocks the asyncio event loop. ALL other HTTP requests (including health checks)
time out until the blocking call completes.

**Incident 2026-06-18**: `_admin_backups_health` and `_admin_backups_list` both
called `list_backups()` synchronously inside `async def` functions. `list_backups()`
calls `_dir_size_mb()` which runs `subprocess.run("du -sk <backup_dir>")`.

On a backup containing 64k+ parquet files, `du` traversal took 10+ seconds. When
`du` timed out (timeout=10), the fallback `path.rglob("*")` Python walk ran —
potentially minutes. The admin page polls `/admin/backups/health` every 30 seconds
(TTL_ADMIN), so the freeze recurred on every cache expiry.

Health check symptom: "Health check exceeded timeout (5s)" — the server port is open
but every request times out. Not "connection refused".

## The fix: asyncio.to_thread()

```python
# BAD — blocks event loop
backups = [b for b in list_backups(backup_root) if ...]

# GOOD — runs in thread pool, event loop stays free
backups = await asyncio.to_thread(
    lambda: [b for b in list_backups(backup_root) if ...]
)
```

Applied in `backend/routes.py` for both `_admin_backups_list_impl` and
`_admin_backups_health_impl` (PR #263, 2026-06-18).

## Finding blocked event loops

```bash
# 1. Check if the port responds but requests time out (vs "connection refused")
curl -m 2 http://localhost:8181/v1/health  # timeout = event loop blocked

# 2. Install py-spy in container
docker compose exec backend pip install py-spy -q

# 3. Find the heavy worker process (largest RSS)
docker compose exec backend sh -c '
ls /proc/[0-9]*/comm | while read f; do
  pid=$(echo $f | cut -d/ -f3)
  rss=$(cat /proc/$pid/status 2>/dev/null | grep VmRSS | awk "{print \$2}")
  comm=$(cat $f 2>/dev/null)
  echo "PID $pid ($comm): ${rss} kB"
done | sort -t: -k2 -rn | head -5
'

# 4. Dump all thread stacks — MainThread shows the culprit
docker compose exec backend sh -c 'py-spy dump --pid <heavy_pid>'
```

The MainThread stack will show exactly what's blocking. Common culprits:
- `subprocess.communicate` → blocking shell command in async handler
- `path.rglob` / `os.walk` → filesystem scan in async handler
- `tbl.scan().to_arrow()` → Iceberg scan in async handler (should be in to_thread)
- `requests.get` → sync HTTP call (use httpx async instead)

## Incident 2026-06-18 (second): ws_multiplexer backpressure flush

`ws_multiplexer.py::_flush_events()` called `flush_events()` (synchronous Iceberg
`_retry_commit`) directly on the asyncio event loop thread.

**Call chain**: Kite WS thread → `call_soon_threadsafe(_enqueue_tick)` → runs on
event loop → `_record_backpressure_event` → `_emit_ws_event` → `_flush_events` →
`flush_events` (sync Iceberg write, 1-2 s per commit).

**Trigger**: Heavy tick load caused hundreds of queue-full backpressure drops/second.
Every 50 drops accumulated, `_flush_events` wrote synchronously to Iceberg on the
main event loop thread — freezing health probes for the full duration.

**Symptom**: Dozens of stacked `curl -sf http://localhost:8181/v1/health` processes
piling up in the container (visible via `ls /proc/*/cmdline`). py-spy MainThread showed
`flush_events → _flush_events → _record_backpressure_event → _enqueue_tick`.

**Fix** (`backend/algo/broker/ws_multiplexer.py`, 2026-06-18):

```python
# BAD — blocks event loop inline
flush_events(self._ws_events)

# GOOD — offload to thread pool; event loop stays free
loop = asyncio.get_running_loop()
loop.run_in_executor(None, flush_events, rows)
# Falls back to sync if no running loop (tests/backtest context)
```

Add `flush_events` to the "MUST use thread pool" list below.

## Rule for all admin/maintenance endpoints

Any endpoint that touches the filesystem, runs subprocesses, or reads large
Iceberg tables MUST wrap those calls in `asyncio.to_thread()`.

Functions that are always safe to call directly in async handlers:
- Redis reads/writes (async client)
- PG reads via asyncpg (async)
- Cache hits (in-memory dict lookup)

Functions that MUST use `asyncio.to_thread()`:
- `list_backups()` / `_dir_size_mb()` (subprocess + filesystem)
- `compact_table()` (Iceberg scan + overwrite)
- `cleanup_orphans_v2()` (filesystem walk)
- Any `subprocess.run()` / `subprocess.Popen()`
- Any `path.rglob()` on large directories
- `flush_events()` / `_flush_events()` in ws_multiplexer (Iceberg write)

## Related memories

- `shared/debugging/iceberg-deep-manifest-compaction-freeze`
- `shared/debugging/sync-async-migration-patterns`
- `shared/conventions/api-versioning`
