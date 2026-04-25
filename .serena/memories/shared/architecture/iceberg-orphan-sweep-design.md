---
name: iceberg-orphan-sweep-design
description: Design + live-prod learnings for ASETPLTFRM-338 — safe orphan-parquet sweep. Updated 2026-04-25 with the snapshot.manifest_list bug.
type: architecture
---

# Iceberg orphan-parquet sweep — safe design

Companion to ASETPLTFRM-338 (shipped 2026-04-25). Captures the
algorithm, the live-prod failure that exposed a missing reference,
and the recovery pattern.

## Why current `cleanup_orphans()` is a no-op

Two design decisions in the **legacy**
`backend/maintenance/iceberg_maintenance.py`:

1. `cleanup_orphans()` only removes empty directories — explicit
   comment "does NOT delete any parquet files." Orphans live alongside
   live files; the function does nothing meaningful.
2. `expire_snapshots()` was documented as a no-op based on the
   assumption that "PyIceberg doesn't expose a safe snapshot expiry
   API." **That assumption is outdated for PyIceberg 0.11.1.**

Both are kept as backwards-compat fallbacks. The real work happens
in **`cleanup_orphans_v2()`** (added 2026-04-25).

## PyIceberg 0.11.1 API surface (verified live)

```python
tbl = catalog.load_table(("stocks", "ohlcv"))

# REAL expire_snapshots — was supposedly missing
es = tbl.maintenance.expire_snapshots()
es.by_id(snap_id).commit()
es.by_ids([id1, id2, id3]).commit()
es.older_than(timestamp_ms).commit()

# Authoritative referenced-set — DATA files
all_files_arrow = tbl.inspect.all_files()  # PyArrow Table
# Columns: content, file_path, file_format, spec_id, partition,
#          record_count, file_size_in_bytes, ...

# Authoritative referenced-set — DATA manifests ({uuid}-m0.avro)
all_manifests_arrow = tbl.inspect.all_manifests()
# Each row has 'path' to an .avro manifest file

# CRITICAL — per-snapshot manifest LIST files (snap-*.avro).
# These are NOT in all_manifests(). You MUST read them off
# each retained snapshot directly. See "live failure" below.
for snap in tbl.metadata.snapshots:
    manifest_list_uri = snap.manifest_list
    # → file:////abs/path/.../metadata/snap-<id>-<seq>-<uuid>.avro
```

## Live failure 2026-04-25 — `snapshot.manifest_list`

First sweep pass on `stocks.analysis_summary` deleted 7944 files
and reclaimed 964 MB — but `verified: False`. `tbl.scan()` raised
`FileNotFoundError` on a `snap-{snapshot_id}-*.avro` file.

**Root cause:** `inspect.all_manifests()` returns the **data
manifests** (`{uuid}-m0.avro`) but NOT the per-snapshot **manifest
LIST files** (`snap-{snapshot_id}-{seq}-{uuid}.avro`). The latter is
referenced by `snapshot.manifest_list` and is the FIRST file
`tbl.scan()` opens for the current snapshot.

**The fix** (already in `cleanup_orphans_v2`):

```python
# Step 2b — manifest-list files for every retained snapshot
for snap in tbl.metadata.snapshots:
    ml = getattr(snap, "manifest_list", None)
    if ml:
        referenced.add(_normalize_uri(ml))
```

**Recovery pattern** (~30 s):

```bash
rsync -a --delete \
  /Users/.../backup-2026-04-25/warehouse/stocks/analysis_summary/ \
  /Users/.../warehouse/stocks/analysis_summary/
docker compose exec -T backend python3 -c \
  "from backend.db.duckdb_engine import invalidate_metadata;
   invalidate_metadata('stocks.analysis_summary')"
```

Locked by regression test
`tests/backend/test_iceberg_orphan_sweep.py::test_snapshot_manifest_list_files_kept_in_referenced`.

## The past failure mode (CLAUDE.md Rule 20)

SQLite catalog stores ONE pointer per table, an absolute file path:

```sql
SELECT metadata_location FROM iceberg_tables
 WHERE table_namespace='stocks' AND table_name='ohlcv';
-- → file:////Users/abhay/.ai-agent-ui/data/iceberg/warehouse/stocks/
--    ohlcv/metadata/03128-ff1d343c-1b7d-470a-9cde-431a747f4005.metadata.json
```

If you `rm` THAT exact file, PyIceberg can't load the table even though
every parquet under `data/` is intact. Recovery requires:

```sql
UPDATE iceberg_tables
   SET metadata_location='file:///path/to/some-other-existing.metadata.json'
 WHERE table_name='ohlcv';
```

— which works only if you have another existing metadata file in the
chain. `cleanup_orphans_v2` hard-excludes the catalog pointer + adds
a paranoid pre-unlink assertion to make this failure impossible.

## The safe algorithm (full)

Implemented in `backend/maintenance/iceberg_maintenance.py::cleanup_orphans_v2`:

1. **Backup** — fail-closed via `run_backup()` (matches
   ASETPLTFRM-328's hardened `drop_dead_tables`).
2. **Expire old snapshots** via
   `tbl.maintenance.expire_snapshots().by_ids(...).commit()`.
   Keep last N snapshots (default N=5) — configurable retention
   window.
3. **Compute referenced set** = union of:
   - `tbl.inspect.all_files()` paths (data parquets)
   - `tbl.inspect.all_manifests()` paths (data manifests)
   - **`snap.manifest_list` for every retained snapshot** ← THIS
   - the catalog's current `metadata_location` for this table
   - the last K=N+5 metadata.json files in chain order
4. **Walk on-disk** = parquet/avro/metadata.json under
   `WAREHOUSE_DIR / table.replace('.','/')`.
5. **Apply mtime grace** — exclude files newer than 30 minutes
   (concurrent-write race protection).
6. **Paranoid assertion** — refuse to unlink anything whose
   normalised path equals the catalog pointer.
7. **Delete** with plain `os.unlink()`.
8. **Read-verify** — `catalog.load_table()` + `tbl.scan(limit=1)` to
   confirm the table still loads.

## Critical safety filters

- `_normalize_uri()` helper — catalog stores `file:////absolute/path`,
  filesystem walk yields `/absolute/path`. Must compare normalised
  forms or the assertion misses.
- mtime grace covers a single sentiment/forecast batch (longest is
  sentiment at ~30 min). 30 min is conservative.
- Retain ≥ 5 snapshots — gives `git revert`-ability for recent
  compaction mistakes.

## Phase 4 results — full rollout (2026-04-25)

| Table | Before | After | Reclaim |
|-------|---:|---:|---:|
| analysis_summary | 938 MB / 7964 files / 1631 snaps | 3.5 MB / 25 / 5 | −99.6 % |
| company_info | 5.6 GB / 18 832 / 4139 snaps | 8.2 MB / 25 / 5 | −99.9 % |
| sentiment_scores | 2.0 GB / 30 944 / 2407 snaps | 12 MB / 1650 / 5 | −99.4 % |
| ohlcv | 4.0 GB / 34 116 / 3142 snaps | 97 MB / 1661 / 5 | −97.6 % |
| **Total** | **12.5 GB / 91 856 files** | **120 MB / 3361** | **−12.4 GB** |

Warehouse total: 16 GB → 3.6 GB (−78 %). Endpoint p95 sub-5 ms after
each sweep.

## Scheduled execution

Weekly job in `public.scheduled_jobs` (Sun 03:00 IST, scope=all).
Executor `execute_iceberg_orphan_sweep` in
`backend/jobs/executor.py` takes ONE backup at the start (fail-closed)
then calls `cleanup_orphans_v2(tbl, skip_backup=True)` for each
hot table in ascending size order.

`verified: False` on any per-table sweep is recorded as non-fatal —
the run continues and the operator sees the warning on the scheduler
dashboard. CLAUDE.md Rule 20 amended to reflect that the sanctioned
path is `cleanup_orphans_v2()`, not direct `rm`.

## Things to verify before re-running on a fresh table

- `docker compose exec backend python3 -c "import pyiceberg; print(pyiceberg.__version__)"` → confirm 0.11.1 or newer
- That `tbl.maintenance.expire_snapshots()` still works on a small
  table (run on a temp table first, NOT prod)
- Backup directory has free space — backups are 16 GB each, two
  retained. After this sprint's reclaim, only ~4 GB each.

## See also

- `docs/backend/iceberg-orphan-sweep.md` — full prose guide with
  recovery procedure
- `tests/backend/test_iceberg_orphan_sweep.py` — 17 unit tests
- `shared/architecture/iceberg-maintenance` — sister doc on
  `compact_table()` + legacy no-ops
