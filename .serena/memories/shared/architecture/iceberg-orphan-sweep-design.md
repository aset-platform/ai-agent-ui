---
name: iceberg-orphan-sweep-design
description: Design notes for ASETPLTFRM-338 — safe orphan-parquet sweep using PyIceberg 0.11.1 native APIs (pickup next session)
type: architecture
---

# Iceberg orphan-parquet sweep — safe design

Companion to ASETPLTFRM-338. Captures the investigation from session 2026-04-25 so the implementer in the next session has full context without re-discovering it.

## Why current `cleanup_orphans()` is a no-op

Two design decisions in `backend/maintenance/iceberg_maintenance.py`:

1. `cleanup_orphans()` only removes empty directories — explicit comment "does NOT delete any parquet files." Orphans live alongside live files; this function does nothing meaningful.

2. `expire_snapshots()` is documented as a no-op based on the assumption that "PyIceberg doesn't expose a safe snapshot expiry API." **That assumption is outdated for PyIceberg 0.11.1.**

## PyIceberg 0.11.1 API surface (verified live)

```python
tbl = catalog.load_table(("stocks", "ohlcv"))

# REAL expire_snapshots — was supposedly missing
es = tbl.maintenance.expire_snapshots()
es.by_id(snap_id).commit()
es.by_ids([id1, id2, id3]).commit()
es.older_than(timestamp_ms).commit()

# Authoritative referenced-set
all_files_arrow = tbl.inspect.all_files()  # PyArrow Table
# Columns: content, file_path, file_format, spec_id, partition,
#          record_count, file_size_in_bytes, ...

all_manifests_arrow = tbl.inspect.all_manifests()
# Each row has 'path' to an .avro manifest file
```

## The past failure mode (CLAUDE.md Rule 20)

SQLite catalog stores ONE pointer per table, an absolute file path:

```sql
SELECT metadata_location FROM iceberg_tables
 WHERE table_namespace='stocks' AND table_name='ohlcv';
-- → file:////Users/abhay/.ai-agent-ui/data/iceberg/warehouse/stocks/
--    ohlcv/metadata/03128-ff1d343c-1b7d-470a-9cde-431a747f4005.metadata.json
```

If you `rm` THAT exact file, PyIceberg can't load the table even though every parquet under `data/` is intact. Recovery requires:

```sql
UPDATE iceberg_tables
   SET metadata_location='file:///path/to/some-other-existing.metadata.json'
 WHERE table_name='ohlcv';
```

— which works only if you have another existing metadata file in the chain. If you wipe ALL metadata files, you have to restore from backup.

## The safe algorithm

Captured in ticket ASETPLTFRM-338 description (full Python pseudocode there):

1. **Backup** — fail-closed via `run_backup()` at function entry (matches the pattern from ASETPLTFRM-328's hardened `drop_dead_tables`).
2. **Expire old snapshots** via `tbl.maintenance.expire_snapshots().by_ids(...).commit()`. Keep last N snapshots (default N=5) — that's the configurable retention window.
3. **Compute referenced set** = union of:
   - `tbl.inspect.all_files()` paths (after expiry)
   - `tbl.inspect.all_manifests()` paths (after expiry)
   - the catalog's current `metadata_location` for this table (read from catalog.db)
   - the last K=N+5 metadata.json files in chain order
4. **Walk on-disk** = all parquet/avro/metadata.json under `WAREHOUSE_DIR / table.replace('.','/')`.
5. **Apply mtime grace** — exclude files newer than 30 minutes (concurrent-write race protection).
6. **Paranoid assertion** — before unlinking, assert no orphan path equals the catalog pointer. Refuse to delete if it does.
7. **Delete** with plain `os.unlink()`.
8. **Read-verify** — `catalog.load_table()` + `tbl.scan(limit=1).to_arrow()` to confirm the table still loads.

## Critical safety filters

- `_normalize_uri()` helper required — catalog stores `file:////absolute/path`, filesystem walk yields `/absolute/path`. Must compare normalised forms or the assertion misses.
- mtime grace covers a single sentiment/forecast batch (longest is sentiment at ~30 min). 30 min is conservative.
- Retain ≥ 5 snapshots — gives `git revert`-ability for recent compaction mistakes.

## Phased rollout (do NOT skip)

1. **Synthetic table tests first** — `tests/backend/test_iceberg_orphan_sweep.py`. Build a temp Iceberg table inside pytest, write rows, compact, expire, sweep, verify reads. ~5 cases including "catalog pointer never deletable" assertion.
2. **`stocks.analysis_summary` dry-run** — smallest hot table, 1588 files, recoverable via `pipeline analytics --scope ...` in ~2 min if it dies. Do this BEFORE the big tables.
3. **Verify dashboard endpoints** — `/v1/dashboard/analysis/latest`, recommendation pages, anything that reads analysis_summary. Backend logs for errors.
4. **Roll out** in size order: company_info → sentiment_scores → ohlcv. Backup before each.
5. **Schedule** as a WEEKLY job (not daily), Sunday 03:00 IST. Daily is too aggressive — there's no need to reclaim disk every day.

## Disk reclaim baseline (2026-04-25 close-of-session)

| Table | Live files | On-disk | Orphan ratio |
|-------|---:|---:|---:|
| ohlcv | 817 | 22 722 | 96% |
| sentiment_scores | 809 | 22 241 | 96% |
| company_info | 1 | 4 214 | 100% |
| analysis_summary | 1 | 1 588 | 100% |

~50K orphan files. 16 GB warehouse total. Estimated reclaim: 10-12 GB.

## Things to verify in next session before starting

- `docker compose exec backend python3 -c "import pyiceberg; print(pyiceberg.__version__)"` → confirm still 0.11.1
- That `tbl.maintenance.expire_snapshots()` still works on a small table (run on a temp table first, NOT prod)
- That backup directory has free space — backups are 16 GB each, two retained
