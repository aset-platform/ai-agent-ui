# Iceberg deep-manifest compaction freeze

**Incidents 2026-06-18**: backend froze twice (00:08 IST, 00:26 IST) from
compaction trying to scan tables with very deep manifest chains.

## Root cause

PyIceberg `tbl.scan().to_arrow()` must traverse the *entire* manifest chain
of the current snapshot before reading any parquet data. Each Iceberg commit
(append/delete) adds one manifest `.avro` file (~100 KB). A table with 4,900
commits therefore requires reading ~500 MB of manifest files just to enumerate
data — before touching a single parquet row.

`algo.events` example (2026-06-18):

| Metric | Value |
|---|---|
| Parquet files | 4,833 |
| Partitions | 7 |
| Avg files/partition | 690 |
| Manifest `.avro` files | ~4,900 |
| Total manifest size | ~500 MB |
| Actual data | ~50 MB |

This is 10× the metadata overhead vs data. The in-process scan blocked
uvicorn's ThreadPoolExecutor slot for 8+ minutes, causing GIL saturation
that prevented the asyncio event loop from handling health checks.

## Guards added (PR #263, 2026-06-18)

Two guards in `compact_table()` in `backend/maintenance/iceberg_maintenance.py`:

```python
_MAX_SAFE_COMPACT_FILES = 40_000  # file-count guard (intraday_features)
_MAX_AVG_FILES_PER_PARTITION = 50  # manifest-depth guard (algo.events)
```

Tables over either threshold skip compaction with a WARNING log. The avg
guard covers all tables with high commit-to-partition ratios:

| Table | avg | Root cause |
|---|---|---|
| auth.audit_log | 844 | every audit event = 1 commit |
| algo.events | 690 | per-signal live flush + per-50-event WS flush |
| auth.users | 283 | every user mutation = 1 commit |
| stocks.query_log | 254 | every chat query logged = 1 commit |
| stocks.chat_audit_log | 118 | — |
| stocks.registry | 111 | every ticker registration = 1 commit |
| stocks.data_gaps | 55 | — |

## How to detect before it bites

```bash
# Inside container: scan all Iceberg tables for high avg
docker compose exec backend python3 -c "
from backend.maintenance.iceberg_maintenance import WAREHOUSE_DIR, _avg_files_per_partition
for schema_dir in WAREHOUSE_DIR.iterdir():
    for tbl_dir in schema_dir.iterdir():
        data_dir = tbl_dir / 'data'
        if not data_dir.exists(): continue
        files, parts, avg = _avg_files_per_partition(data_dir)
        if avg > 50:
            print(f'{schema_dir.name}.{tbl_dir.name}: {files} files / {parts} parts = avg {avg:.1f}')
"
```

## Fix for high-commit tables

Rewrite the write path to use `tbl.overwrite(arrow, overwrite_filter=...)` 
(copy-on-write, 1 commit + 1 parquet per operation) instead of
`tbl.delete(...) + tbl.append(...)` (2 commits + 2 parquets per operation).

See `shared/operations/nuke-rebuild-faster-than-fragmented-compaction` for
when to do a one-time nuke-rebuild to reset the manifest chain.

For `algo.events` specifically: the Tier 1-3 redesign (Redis sorted set for
live-WS events, 30s batched flushes, COW overwrite) is the permanent fix.
Until then, the avg guard prevents freezes but the table won't be compacted.

## Diagnostic: py-spy to find a blocked uvicorn

```bash
# Install in container (ephemeral — lost on restart)
docker compose exec backend pip install py-spy -q

# Get all thread stack traces of the worker process
docker compose exec backend sh -c 'py-spy dump --pid $(cat /proc/*/comm 2>/dev/null | grep -n python | head -1)'
# Or find the large-RAM process:
docker compose exec backend sh -c 'ls /proc/[0-9]*/comm | while read f; do echo "PID $(echo $f | cut -d/ -f3): $(cat $f)"; done | grep python'
# Then: py-spy dump --pid <heavy_pid>
```

The MainThread stack will show exactly which function is blocking (e.g.
`subprocess.communicate`, `tbl.scan`, `path.rglob`).

Also useful: check `/proc/<pid>/wchan` for kernel-level wait:
- `do_wait` = process waiting for child to exit (hot-reload limbo)
- `futex_wait_queue` = thread waiting for mutex (normal idle pool)
- `do_epoll_wait` = asyncio thread idle (normal)
- `pipe_read` = thread stuck reading from pipe (suspicious)

## Related memories

- `shared/operations/nuke-rebuild-faster-than-fragmented-compaction`
- `shared/debugging/iceberg-compact-duckdb-stale-read`
- `shared/conventions/iceberg-maintenance-enrollment`
