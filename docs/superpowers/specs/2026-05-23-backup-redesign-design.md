# Backup Redesign — Manifest-Driven Daily Snapshot

**Date:** 2026-05-23
**Author:** Abhay (with Claude pair-programming)
**Status:** Spec — pending implementation plan
**Supersedes:** ASETPLTFRM-418 per-table backup behaviour

## Goal

Replace today's per-pipeline, per-table backup loop with **one
warehouse snapshot per day**, surfaced as a single manifest the
Admin UI reads for size, health, and Browse drill-down. Reduce
CPU spent on backups by ~95% and reclaim ~5–6 GB of disk while
preserving the fail-closed "always back up before destructive
maintenance" safety rail. Land a manifest format that maps 1:1
to the upcoming S3 / cloud warehouse so the cut-over is a
storage-layer swap, not a redesign.

## Background — what's broken today

The warehouse currently has two backup paths:

- `run_backup()` — full warehouse rsync to
  `backup-YYYY-MM-DD/warehouse/`. Rotates via
  `_rotate_backups()` (keep 2).
- `backup_table(table_id)` — per-table rsync to
  `backup-YYYY-MM-DD-<ns>-<name>/<name>/`. **Does not call
  rotation.**

ASETPLTFRM-418 ("scoped iceberg maintenance") had every
pipeline switch from `run_backup()` to a per-table loop. Five
pipelines (India Daily, USA Daily, Intraday Bars Daily, Regime
India Daily, algo_events_retention, intraday_bars_retention)
each call `backup_table()` for their scoped tables every day.

Consequences:

1. **~30 per-table directories per day** instead of 1. Each
   carries its own copy of the full `catalog.db` (~25×
   duplication of catalog metadata).
2. **No rotation**. `backup-2026-05-19-*` directories survive
   alongside today's because rotation only triggers on the
   legacy unscoped `run_backup()` path, which no pipeline
   takes anymore.
3. **Disk: 8.3 GB across 91 directories** vs the warehouse
   itself at 2.3 GB — 3.6× duplication.
4. **Admin Backup Health card shows `SIZE 0 MB`**. The query
   reads `latest["size_mb"]` — whichever single per-table dir
   sorts first by date (today's `universe_snapshot` is 0 MB).
   It was designed for the one-row-per-day full-warehouse
   layout and was never updated for the per-table format.
5. **Backup Browse 404s on per-table rows**. The
   `/admin/backups/{date}/contents` endpoint walks
   `warehouse/stocks/` inside the backup directory — that
   path only exists in full-warehouse backups.
6. **CPU**: every pipeline pays the rsync scan cost (~10–30 s
   per table even when no files changed). India Daily +
   Intraday Bars Daily + Regime India alone burn ~5 min of
   redundant rsync time daily — for the same warehouse trees
   the previous pipeline already copied.

## Out of scope

- **Iceberg-snapshot-as-backup** (the considered Option B).
  Powerful but a much larger rework, and SQLite catalog
  corruption would still need a file-level backup. Revisit if
  the cloud move surfaces specific need.
- **S3 object versioning** (the considered Option C). The
  destination when the warehouse lives in S3, but only
  applicable post-migration. The manifest format chosen here
  is the contract that S3 will adopt.
- **Restore UX changes**. The CLI `restore_backup(date)` API
  stays as-is. Only the snapshot-creation path is
  redesigned.
- **Backup encryption / off-site replication**. Future work
  on top of the cloud move.

## Architecture

```
                ┌─────────────────────────────┐
   00:30 IST → │   backups_daily scheduler   │
                │   one rsync of warehouse    │
                └──────────────┬──────────────┘
                               ▼
            backup-YYYY-MM-DD/
            ├── warehouse/   (rsync of warehouse root)
            ├── catalog.db   (SQLite catalog snapshot)
            └── manifest.json   ← single source of truth

                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
   admin Health card     admin Browse drill     pipeline step-0
   reads manifest →      reads manifest →       reads manifest →
   cumulative size,      table list with        skip if fresh,
   age, status           per-table size         else fall back
                                                to scoped
                                                backup_table()
```

### One job, one snapshot, one manifest

A new scheduler job `backups_daily` runs once at 00:30 IST.
It:

1. Calls existing `run_backup()` — full warehouse rsync to
   `backup-YYYY-MM-DD/warehouse/`, copies `catalog.db`,
   rotates to keep `MAX_BACKUPS` (2).
2. Walks the freshly-written snapshot and writes
   `manifest.json` at the snapshot root summarising every
   table.
3. Logs `backup-2026-05-23: 2.3 GB across 27 tables, took
   4 min 12 s`.

The job is idempotent — re-running on the same date is a no-op
rsync (already up-to-date) and a manifest rewrite.

### Pipeline step-0 becomes "verify snapshot"

Pipelines no longer take their own backups in the common case.
The shared step-0 helper `verify_or_backup(tables)`:

1. Reads today's `manifest.json` (path
   `BACKUP_ROOT/backup-<today>/manifest.json`).
2. **If** the manifest exists AND `created_at` is within the
   last 24 hours AND every table in `tables` is listed in
   `manifest.tables[].id` → return `{"mode": "verified",
   "snapshot": "<path>"}`. No I/O beyond reading the manifest.
3. **Else** (snapshot missing, stale, or doesn't cover the
   scoped tables) → fall back to per-table
   `backup_table(t)` for each `t in tables` (the existing
   safety rail). Return `{"mode": "fallback_per_table",
   "paths": [...]}`.

The fallback path preserves the CLAUDE.md hard rule "always
backup before destructive maintenance" — if the daily snapshot
job failed or hasn't run yet, the pipeline pays the per-table
rsync cost rather than risk a no-backup compaction.

### Manifest format

`backup-YYYY-MM-DD/manifest.json`:

```json
{
  "schema_version": 1,
  "snapshot_id": "2026-05-23",
  "created_at": "2026-05-23T00:30:14Z",
  "created_by": "backups_daily",
  "warehouse_root": "/Users/.../warehouse",
  "warehouse_size_mb": 2347.6,
  "rsync_duration_s": 252,
  "catalog_present": true,
  "catalog_size_mb": 0.4,
  "tables": [
    {
      "id": "stocks.ohlcv",
      "namespace": "stocks",
      "name": "ohlcv",
      "size_mb": 98.4,
      "partition_count": 16,
      "file_count": 18,
      "last_modified_ns": 1747958412000000000
    },
    ...
  ]
}
```

`schema_version` lets us evolve the format. Bumping it forces
the Admin UI to fall back to filesystem scan for backups
written by older runners.

The `tables` array is sorted by `id` ascending. Sizes are
reported with one decimal place. `file_count` is the count of
`data/**/*.parquet` files; `partition_count` is the count of
top-level partition directories under `data/`. These are the
two metrics the existing Browse endpoint exposes — preserved
for parity.

### Admin endpoints

**`GET /admin/backups/health`** (modified):

Reads the latest manifest. Returns:

```json
{
  "status": "healthy",        // healthy < 24h, stale < 72h
  "latest_date": "2026-05-23",
  "completed_at": "2026-05-23T00:30:14Z",
  "age_hours": 5.2,
  "backup_count": 2,           // distinct dates only
  "table_count": 27,
  "warehouse_size_mb": 2347.6,
  "has_catalog": true
}
```

Behaviour when no manifest exists (legacy backup or
migration in flight): fall back to today's filesystem-walk
size — same `du -sk` path used today — so the UI doesn't go
blank during the cutover.

**`GET /admin/backups`** (modified):

Returns one row per `backup-YYYY-MM-DD` directory (full
snapshots only — per-table directories are filtered out by
name pattern). Per row: `date`, `path`, `size_mb` (from
manifest if present, else `du`), `completed_at`,
`age_hours`, `has_catalog`, `has_manifest`, `table_count`.

**`GET /admin/backups/{date}/contents`** (modified):

Reads `backup-<date>/manifest.json` if present and returns
`{date, tables: manifest.tables[], catalog_present}`. Else
falls back to the current filesystem walk for legacy backups.

### Migration / cleanup

A one-shot script `scripts/cleanup_per_table_backups.py`:

1. Lists every directory matching
   `^backup-\d{4}-\d{2}-\d{2}-(stocks|algo)-.+$`.
2. Skips today's per-table dirs (preserve until tomorrow's
   `backups_daily` run produces the full snapshot — this is
   the only safety copy if something blows up tonight).
3. `rm -rf` everything else.
4. Logs the reclaimed disk space.

Run once after the new `backups_daily` job has produced its
first successful snapshot.

### Cloud-readiness contract

The manifest IS the contract for the S3 cut-over:

- `backup-YYYY-MM-DD/` → S3 prefix
  `s3://ai-agent-ui-backups/YYYY-MM-DD/`.
- `manifest.json` at the prefix root.
- `warehouse/` and `catalog.db` as objects under the prefix.
- Rotation → S3 lifecycle policy (expire prefixes older than
  N days). The `backups_daily` job no longer rotates; it just
  writes.
- Health card / Browse endpoints don't change — they read
  the manifest, agnostic to filesystem vs S3.

The runner abstraction is left as a "swap the rsync call for
an S3 sync call" change post-cloud-cut. Not implemented in
this spec.

## Components

### Component 1 — Manifest writer (pure function)

**File:** `backend/maintenance/backup_manifest.py` (new)

**Interface:**

```python
def build_manifest(
    snapshot_root: Path,
    *,
    created_by: str,
    rsync_duration_s: int,
) -> dict:
    """Walk snapshot_root, return manifest dict.

    snapshot_root expected layout:
        <root>/warehouse/<ns>/<table>/...
        <root>/catalog.db (optional)
    """

def write_manifest(
    snapshot_root: Path,
    manifest: dict,
) -> Path:
    """Write manifest.json atomically (tmp + rename)."""

def read_manifest(
    snapshot_root: Path,
) -> dict | None:
    """Read manifest.json. Returns None if absent or invalid."""
```

Pure functions over the filesystem. No globals, no I/O beyond
what's declared. Unit-testable with tmpdir fixtures.

### Component 2 — `backups_daily` scheduler job

**File:** `backend/jobs/executor.py` (modify — add new
`@register_job("backups_daily")` near the existing
`iceberg_maintenance` registration)

**Schedule:** Cron `30 0 * * *` (00:30 IST, before any data
pipeline). Seeded via existing scheduler infrastructure.

**Behaviour:**

1. Call `run_backup()` (existing) — returns path
   `backup-YYYY-MM-DD`.
2. Time the rsync.
3. Call `build_manifest(path, created_by="backups_daily",
   rsync_duration_s=elapsed)`.
4. Call `write_manifest(path, manifest)`.
5. Log size + table count + duration. Update
   `scheduler_runs` row with `tickers_done=27` (table count)
   for UI parity.
6. Fail-closed: any exception during rsync OR manifest write
   marks the run failed and surfaces in admin.

### Component 3 — `verify_or_backup()` helper

**File:** `backend/maintenance/backup.py` (modify — add new
function alongside existing `backup_table` and `run_backup`)

**Interface:**

```python
def verify_or_backup(
    tables: list[str],
    *,
    max_age_h: float = 24.0,
    backup_root: str | None = None,
) -> dict:
    """Return {'mode': 'verified' | 'fallback_per_table',
              'snapshot': '<path>' | None,
              'paths': [<per_table_paths>]}
    """
```

**Logic:**

1. Resolve `backup-<today>/manifest.json` path.
2. Try `read_manifest(path)`. If None or invalid → fallback.
3. If `created_at` age > `max_age_h` → fallback.
4. If any table in `tables` is missing from
   `manifest.tables[].id` → fallback.
5. Else return `{"mode": "verified", "snapshot": path}`.

Fallback path: iterate `backup_table(t)` for each `t`,
swallow `FileNotFoundError` (table never written — current
behaviour), aggregate paths, return.

### Component 4 — Pipeline step-0 refactor

**File:** `backend/jobs/executor.py` (modify — the existing
`@register_job("iceberg_maintenance")` body around line 2805)

The current code:

```python
if scoped:
    paths: list[str] = []
    for t in tables:
        try:
            paths.append(backup_table(t))
        except FileNotFoundError:
            ...
    backup_path = "; ".join(paths) if paths else "<none>"
else:
    backup_path = run_backup()
```

Becomes:

```python
if scoped:
    result = verify_or_backup(tables)
    if result["mode"] == "verified":
        backup_path = result["snapshot"]
        _logger.info(
            "[maint] Verified today's snapshot covers "
            "%d scoped table(s) — skipping per-table backup",
            len(tables),
        )
    else:
        backup_path = "; ".join(result["paths"]) or "<none>"
        _logger.info(
            "[maint] Snapshot stale/missing — per-table "
            "backup of %d table(s)", len(tables),
        )
else:
    backup_path = run_backup()
```

The unscoped branch is untouched — admin "Run maintenance"
button + ad-hoc CLI calls continue to take a fresh full
backup as before.

### Component 5 — Admin endpoints

**File:** `backend/routes.py` (modify — the three
`_admin_backups_*` handlers around line 3720)

Changes:

- `_admin_backups_health` reads
  `BACKUP_ROOT/backup-<latest>/manifest.json`. Returns
  `warehouse_size_mb`, `table_count`, plus the existing
  fields. Falls back to `du -sk` for legacy backups.
- `_admin_backups_list` filters out per-table directories by
  name regex (`backup-\d{4}-\d{2}-\d{2}$`). Per-table dirs
  are an internal fallback, not user-facing.
- `_admin_backup_contents` returns
  `manifest.tables` when present. Falls back to the existing
  filesystem walk for legacy / per-table format.

Cache keys (`cache:admin:backups-list`,
`cache:admin:backups-health`) and 120s TTLs unchanged.

### Component 6 — Frontend Backup Health card

**File:** `frontend/components/admin/BackupHealthPanel.tsx`
(mounted from `frontend/app/(authenticated)/admin/page.tsx`
line 1404)

Changes:

- "SIZE" tile reads `warehouse_size_mb` (the new manifest
  aggregate field) instead of `latest.size_mb`.
- "BACKUPS STORED" tile reads `backup_count` (now a count of
  distinct dates, not per-table dirs).
- Add small "TABLES" tile (count from `table_count`).
- Browse drill-down renders the table list returned by the
  modified `/contents` endpoint. Same row schema as today
  (table_name / partitions / files / size_mb).

### Component 7 — Cleanup migration script

**File:** `scripts/cleanup_per_table_backups.py` (new)

Idempotent. Run once after the new job has produced its
first successful snapshot.

Logic:

```python
PER_TABLE_PATTERN = re.compile(
    r"^backup-\d{4}-\d{2}-\d{2}-(stocks|algo)-.+$"
)
today = date.today().isoformat()

for d in Path(BACKUP_ROOT).iterdir():
    if not d.is_dir():
        continue
    m = PER_TABLE_PATTERN.match(d.name)
    if not m:
        continue
    if d.name.startswith(f"backup-{today}-"):
        # Preserve today's per-table dirs until the new
        # daily snapshot job has produced a full snapshot.
        continue
    print(f"removing {d}")
    shutil.rmtree(d)
```

Run instruction: `python scripts/cleanup_per_table_backups.py`.

## Data flow

### Happy path (daily)

1. **00:30 IST** — `backups_daily` runs. Full rsync (~4 min,
   2.3 GB). Manifest written. Rotation drops the
   3-day-old snapshot.
2. **01:00–05:00 IST** — data pipelines fire. Each pipeline's
   step 0 calls `verify_or_backup(scoped_tables)`. Sees
   manifest age <1 h, table coverage 100%, returns
   `{"mode": "verified"}`. Maintenance proceeds.
3. **Admin UI** — Health card shows status="healthy",
   age_hours≈8, warehouse_size_mb=2347.6, table_count=27.
   Browse on `2026-05-23` returns 27 rows from manifest.

### Recovery path (daily snapshot fails)

1. 00:30 IST job fails (disk full, rsync timeout, …).
2. Pipeline step 0 reads manifest — either missing or
   yesterday's (>24h old) or doesn't cover all scoped tables
   (e.g. new table since snapshot). Falls back to per-table
   `backup_table()` loop. Same safety as today.
3. Admin Health card shows status="stale" or "critical".
4. Operator: re-trigger `backups_daily` from admin Run
   Maintenance button OR let pipelines continue accumulating
   per-table fallback backups until the next 00:30 run.

### Restore (unchanged)

`restore_backup("2026-05-22")` works against the
full-warehouse layout as it does today. Per-table fallback
backups remain restorable via the existing legacy path
inside `restore_backup` (the function already handles
`backup_root / backup-{date}` direct layout).

## Error handling

- Manifest write failure → job marked failed, rsync stays on
  disk (partial state is OK — manifest is reconstructable).
- Manifest read failure (corrupt JSON) → admin endpoints
  fall back to filesystem walk; pipeline step-0 falls back
  to per-table backup.
- Rsync timeout → `run_backup()` already raises
  `RuntimeError`; job fails closed.
- Disk full → rsync fails; manifest never written; job
  failed; pipelines fall back to per-table (which may also
  fail and surface in admin).

## Telemetry

The `backups_daily` job logs at INFO:

```
[backups_daily] starting full snapshot
[backups_daily] rsync complete: 2347.6 MB in 252 s
[backups_daily] manifest written: 27 tables
[backups_daily] rotated 1 old backup
[backups_daily] done in 256 s
```

Pipeline step-0 logs at INFO whichever mode it took:

```
[maint] Verified today's snapshot covers 14 scoped table(s)
        — skipping per-table backup
```

or

```
[maint] Snapshot stale/missing — per-table backup of 14 table(s)
```

The verified-vs-fallback ratio is a soft health signal — if
fallback rate is >10% over a week, the 00:30 job is unstable.

## Testing

### Unit tests

- `test_backup_manifest_build_walks_warehouse`: fixture
  warehouse with 2 namespaces × 3 tables × N parquets;
  assert manifest contains all tables, sizes, partition
  counts.
- `test_backup_manifest_write_atomic`: write fails midway
  (mock open) → no manifest.json left behind.
- `test_backup_manifest_read_invalid_json`: returns None,
  doesn't raise.
- `test_verify_or_backup_uses_fresh_manifest`: manifest <1h
  old, covers all tables → mode="verified",
  backup_table not called.
- `test_verify_or_backup_falls_back_on_stale`: manifest 30h
  old → fallback path, backup_table called N times.
- `test_verify_or_backup_falls_back_on_missing_table`:
  manifest doesn't list a scoped table → fallback.

### Integration tests

- `test_backups_daily_job_writes_manifest`: invoke the job
  in-process with a tmp warehouse, assert
  manifest.json exists at the new snapshot root, contains
  expected tables.
- `test_iceberg_maintenance_skips_backup_when_fresh`:
  invoke scoped maintenance with a fresh manifest fixture,
  assert `backup_table` is not called.
- `test_iceberg_maintenance_falls_back_when_stale`: same as
  above but stale manifest, assert per-table backup happens.

### Admin endpoint tests

- `test_admin_backups_health_reads_manifest`: tmp backup dir
  with manifest, GET returns warehouse_size_mb +
  table_count.
- `test_admin_backups_list_filters_per_table_dirs`: tmp dir
  with mixed full + per-table backups, GET only returns
  full backups.
- `test_admin_backup_contents_reads_manifest_when_present`:
  manifest-backed response.
- `test_admin_backup_contents_falls_back_for_legacy`: no
  manifest → filesystem walk.

### Frontend tests

- Vitest snapshot of Backup Health card with mocked manifest
  payload — assert TABLES tile renders and SIZE shows
  aggregate.

## Process / git

- One PR per Component 1–6. Component 7 (cleanup script)
  ships in the same PR as Component 5 (admin endpoints) so
  the disk reclaim and the UI fix land together.
- Branch off `dev`; squash merge.
- Co-Authored-By Abhay per CLAUDE.md.
- Update `PROGRESS.md` with the redesign summary.
- Memory: `feedback_backup_manifest_design` (new) — captures
  the per-table-vs-snapshot trade-off for future agents.

## Open questions answered (defaults applied)

- **00:30 IST timing**: chosen because the first scheduled
  data pipeline (India Daily Pipeline) fires at 03:00 IST
  earliest. Leaves 2.5h slack for the rsync.
- **Retention**: `MAX_BACKUPS=2` unchanged. Two days is
  enough for "oh shit, restore" recovery; the cloud move
  brings longer retention via lifecycle policy.
- **Manifest schema_version**: starts at 1.
- **No CLI command added**: pipelines + admin Run
  Maintenance + scheduler cover all callers.
- **Restore path unchanged**: the existing
  `restore_backup(date)` works against the full-warehouse
  layout that the new job produces.
