# Iceberg orphan-parquet sweep

> Sanctioned safe path for reclaiming on-disk space from compacted Iceberg
> tables. Replaces the no-op `cleanup_orphans()` legacy fallback. Ships
> as ASETPLTFRM-338 (2026-04-25).

## Why we need it

Daily compaction (`compact_table`) writes one parquet per partition via
`tbl.overwrite()`. The new compacted snapshot is fast to read, but the
prior snapshot still references the OLD parquets — they remain on disk
as "old but still referenced" until their owning snapshot is expired.

Without expiry + physical sweep, file count and disk usage grow without
bound. The 2026-04-25 baseline measured:

| Table | Live files | On-disk | Orphan % |
|---|---:|---:|---:|
| `stocks.ohlcv` | 817 | 22 722 | 96 % |
| `stocks.sentiment_scores` | 809 | 22 241 | 96 % |
| `stocks.company_info` | 1 | 4 214 | 100 % |
| `stocks.analysis_summary` | 1 | 1 588 | 100 % |

Daily compaction was also slowing: India scope went 254 s → 572 s in 24
hours because DuckDB had to walk past more orphans + snapshot metadata
each run.

## Why this was avoided historically

CLAUDE.md hard rule 20 — *"NEVER delete Iceberg metadata/parquet files
directly — direct deletion breaks the SQLite catalog"* — was written
after a manual sweep deleted metadata files including the one the
catalog pointer (`iceberg_tables.metadata_location` in
`~/.ai-agent-ui/data/iceberg/catalog.db`) referenced. The table
vanished from PyIceberg's perspective even though parquets were intact.
Recovery required surgical SQL on `catalog.db`.

The legacy `cleanup_orphans()` over-corrected: it does nothing useful
(only removes empty directories) rather than risk that failure mode.

A correctly-designed sweep can avoid the past failure mode entirely
by using PyIceberg's authoritative `inspect.all_files()` /
`inspect.all_manifests()` for the referenced set + a hard-coded catalog
pointer exclusion + a paranoid pre-unlink assertion.

## API

`backend.maintenance.iceberg_maintenance.cleanup_orphans_v2()`:

```python
def cleanup_orphans_v2(
    table_name: str,
    *,
    retain_snapshots: int = SNAPSHOT_KEEP,        # default 5
    mtime_grace_minutes: int = 30,
    dry_run: bool = False,
    skip_backup: bool = False,                    # tests / batch only
    catalog_db: Path | None = None,               # tests
    warehouse_dir: Path | None = None,            # tests
) -> dict
```

Returns:

```python
{
    "table": "stocks.ohlcv",
    "backup": "/Users/.../backup-2026-04-25",
    "expired_snapshots": 3137,
    "referenced_count": 1661,
    "on_disk_count": 34116,
    "candidate_count": 32455,
    "grace_skipped": 0,
    "deleted_files": 32455,
    "deleted_bytes": 4_113_603_082,
    "verified": True,
    "dry_run": False,
}
```

## Algorithm

0. **Mandatory backup** — fail-closed via `run_backup()` (matches the
   pattern from ASETPLTFRM-328's hardened `drop_dead_tables`). If
   backup fails, abort with `result["error"]` set; nothing is touched.
1. **Expire old snapshots** — `tbl.maintenance.expire_snapshots()
   .by_ids(...).commit()` keeping the latest `retain_snapshots` by
   `timestamp_ms`.
2. **Build referenced set** = union of:
   - `tbl.inspect.all_files()` paths (data parquets)
   - `tbl.inspect.all_manifests()` paths (data manifests, `*-m0.avro`)
   - **`snap.manifest_list` for every retained snapshot** — the
     per-snapshot `snap-{snapshot_id}-{seq}-{uuid}.avro` files. THIS
     IS LOAD-BEARING (see "Live failure 2026-04-25" below).
   - The catalog's current `metadata_location` for this table (read
     from `catalog.db`).
   - The last `retain_snapshots + 5` `*.metadata.json` files in chain
     order — gives `git revert`-ability for recent compaction mistakes.
3. **Walk on-disk** = parquet + avro + `*.metadata.json` under
   `WAREHOUSE_DIR / table.replace('.','/')`.
4. **Apply mtime grace** — exclude files newer than 30 min
   (concurrent-write race protection; covers a sentiment / forecast
   batch).
5. **Paranoid catalog-pointer assertion** — refuse to unlink anything
   whose normalised path equals the catalog pointer. Raises
   `AssertionError` if violated (defense-in-depth).
6. **Delete** with plain `os.unlink()` (or skip on `dry_run`).
7. **Read-verify** — `catalog.load_table()` + `tbl.scan(limit=1)` to
   confirm the table still loads. Records `verified: False` if it
   raises (operator decides — restore from backup or investigate).
8. **Invalidate DuckDB metadata cache** — best-effort, so subsequent
   reads see the post-sweep file set.

## Live failure 2026-04-25 — `snapshot.manifest_list` bug

First sweep pass on `stocks.analysis_summary` deleted 7944 files and
reclaimed 964 MB — but `verified: False`. `tbl.scan()` raised
`FileNotFoundError` on `snap-4599013458522489988-0-...avro`.

Root cause: `inspect.all_manifests()` returns data manifests
(`{uuid}-m0.avro`) but NOT the per-snapshot manifest LIST files
(`snap-{snapshot_id}-{seq}-{uuid}.avro`). The latter is referenced
by `snapshot.manifest_list` and is the FIRST file `tbl.scan()` opens.

The fix added an explicit loop:

```python
for snap in tbl.metadata.snapshots:
    ml = getattr(snap, "manifest_list", None)
    if ml:
        referenced.add(_normalize_uri(ml))
```

Recovery: `rsync -a --delete <backup>/warehouse/stocks/analysis_summary/
<live>/warehouse/stocks/analysis_summary/` then
`invalidate_metadata('stocks.analysis_summary')`. ~30 seconds.

The regression test
`tests/backend/test_iceberg_orphan_sweep.py::test_snapshot_manifest_list_files_kept_in_referenced`
locks the behaviour.

## Phase 4 results (2026-04-25)

Sequential live sweep on the 4 hot tables:

| Table | Before | After | Reclaim | Snaps expired | Sweep time |
|---|---:|---:|---:|---:|---:|
| `analysis_summary` | 938 MB / 7964 files | 3.5 MB / 25 | −99.6 % | 1626 | 24.8 s |
| `company_info` | 5.6 GB / 18 832 | 8.2 MB / 25 | −99.9 % | 4134 | 412.0 s |
| `sentiment_scores` | 2.0 GB / 30 944 | 12 MB / 1650 | −99.4 % | 2402 | 154.7 s |
| `ohlcv` | 4.0 GB / 34 116 | 97 MB / 1661 | −97.6 % | 3137 | 241.1 s |
| **Total** | **12.5 GB / 91 856** | **120 MB / 3361** | **−12.4 GB** | **11 299** | **~14 min** |

Total warehouse: 16 GB → 3.6 GB (−78 %). Endpoint p95 sub-5 ms after
each sweep.

## Scheduled execution

Registered as a weekly job in `public.scheduled_jobs`:

```
job_type:    iceberg_orphan_sweep
name:        Iceberg Orphan Sweep - Weekly
cron_days:   sun
cron_time:   03:00       (Asia/Kolkata)
scope:       all
enabled:     true
force:       false
```

Executor `execute_iceberg_orphan_sweep` in `backend/jobs/executor.py`:
takes ONE backup at the start (fail-closed), then calls
`cleanup_orphans_v2(tbl, skip_backup=True)` for each table in
ascending size order.

`verified: False` on any per-table sweep is recorded as a non-fatal
error — the run continues so other tables still get cleaned, and the
operator sees the warning on the scheduler dashboard.

## Manual invocation

```bash
# Dry-run on a single table (mutates snapshot metadata via expire,
# does NOT unlink files):
docker compose exec -T backend python3 -c "
from backend.maintenance.iceberg_maintenance import cleanup_orphans_v2
import json
print(json.dumps(cleanup_orphans_v2('stocks.ohlcv', dry_run=True),
                 indent=2, default=str))
"

# Live sweep on a single table:
docker compose exec -T backend python3 -c "
from backend.maintenance.iceberg_maintenance import cleanup_orphans_v2
import json
print(json.dumps(cleanup_orphans_v2('stocks.ohlcv'),
                 indent=2, default=str))
"
```

## Recovery from a broken sweep

If `verified: False` after a sweep:

```bash
# 1. Identify the latest backup (auto-rotated, MAX_BACKUPS=2)
ls /Users/abhay/Documents/projects/ai-agent-ui-backups/

# 2. Rsync the table dir back
rsync -a --delete \
  /Users/.../backup-2026-04-25/warehouse/stocks/<table>/ \
  /Users/.../warehouse/stocks/<table>/

# 3. Invalidate DuckDB cache
docker compose exec -T backend python3 -c "
from backend.db.duckdb_engine import invalidate_metadata
invalidate_metadata('stocks.<table>')
"

# 4. Verify
docker compose exec -T backend python3 -c "
from backend.maintenance.iceberg_maintenance import _get_catalog
tbl = _get_catalog().load_table('stocks.<table>')
list(tbl.scan(limit=1).to_arrow().to_pylist())
print('OK')
"
```

If the catalog pointer itself was wiped (impossible with the v2
algorithm; only happens with manual `rm`), recover via direct sqlite:

```sql
sqlite3 ~/.ai-agent-ui/data/iceberg/catalog.db
UPDATE iceberg_tables
   SET metadata_location='file:///path/to/some-existing.metadata.json'
 WHERE table_namespace='stocks' AND table_name='<table>';
```

## Tests

`tests/backend/test_iceberg_orphan_sweep.py` — 17 cases covering all 5
load-bearing properties (backup-fail-closed, referenced-files-survive,
catalog-pointer-protected, mtime-grace, expire-with-correct-ids) plus
the `snapshot.manifest_list` regression and helper unit tests for
`_normalize_uri` + `_read_catalog_metadata_location`.

## Related

- `shared/architecture/iceberg-orphan-sweep-design` — original design
  notes (now updated with the live-prod failure)
- `shared/architecture/iceberg-maintenance` — sister doc covering
  `compact_table()` / `expire_snapshots()` legacy no-op
- ASETPLTFRM-315 — original compaction work
- ASETPLTFRM-328 — sister hardening (drop_dead_tables backup guard)
