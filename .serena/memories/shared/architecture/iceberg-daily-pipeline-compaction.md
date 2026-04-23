# Daily Iceberg Compaction in Pipeline (auto-backup-first)

Each daily India + USA pipeline now ends with a `iceberg_maintenance` step that backs up + compacts the hot Iceberg tables. Prevents the slow drift to thousands of small parquet files that made `Clean NaN Rows` take 5+ minutes on a 16K-file OHLCV table.

## Why

Per-ticker Iceberg writes (especially OHLCV daily refresh and sentiment per-ticker upserts) create one tiny parquet per `(ticker, write)`. Without compaction this balloons fast:

- Original ASETPLTFRM-315 compaction reduced OHLCV from 8,670 → 817 files (reads 9s → 0.24s)
- Without recurring compaction, OHLCV grew back to 16,156 files in 6 days → reads 5+ s, scoped deletes pathologically slow
- Same pattern hit `company_info` historically (4,055 files for 830 rows = 5.3 GB before compaction)

A daily auto-compaction keeps the file count bounded.

## Pipeline integration

```sql
-- pipeline_steps rows added 2026-04-23
INSERT INTO pipeline_steps (pipeline_id, step_order, job_type, job_name) VALUES
  ('<india-pipeline-id>', 6, 'iceberg_maintenance', 'Iceberg Maintenance - India'),
  ('<usa-pipeline-id>',   6, 'iceberg_maintenance', 'Iceberg Maintenance - USA');
```

Both daily pipelines now end with this step. Idempotent — running twice/day (once after India 08:00 IST, once after USA 08:15 IST) is fine. Second run is a near-no-op (just-compacted, very few new rows since first run).

## Executor (`backend/jobs/executor.py::execute_iceberg_maintenance`)

Order matters:

1. **Step 0 — backup** (`run_backup()`). Fail-closed: if backup fails, the maintenance step exits with `failed` status and **skips compaction**. Preserves the CLAUDE.md hard rule "always backup before maintenance" — no auto-compact without a fresh restore point.
2. **Step 1..N — compact each hot table**:
   - `stocks.ohlcv`
   - `stocks.sentiment_scores`
   - `stocks.company_info`
   - `stocks.analysis_summary`
3. After each table: best-effort `expire_snapshots()` + `cleanup_orphans()`. Failures here are logged but don't fail the run.

Each `compact_table()` call:
- Reads all rows via DuckDB
- `tbl.overwrite(arrow_table)` writes back as one file per partition
- Old parquets become orphans (Iceberg metadata only references new ones; reads stay fast)

## Container changes for backup

`run_backup()` shells to `rsync` (~14 GB warehouse) which wasn't in the runtime image. Added to `Dockerfile.backend`:

```dockerfile
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl rsync && \
    rm -rf /var/lib/apt/lists/*
```

The host backup directory is already mounted at the same path inside the container via `docker-compose.override.yml`:

```yaml
- /Users/abhay/Documents/projects/ai-agent-ui-backups:/Users/abhay/Documents/projects/ai-agent-ui-backups
```

so `run_backup()` writes to `BACKUP_ROOT = /Users/abhay/Documents/projects/ai-agent-ui-backups` and the file appears on host transparently. Rotation policy `MAX_BACKUPS = 2` (default in `backend/maintenance/backup.py`).

## Performance observed end-to-end

After a clean run:
- Backup: ~80 s (15 GB rsync, mostly already-incremental files)
- Compact OHLCV: ~5 s (1.5M rows → ~800 files)
- Compact sentiment_scores: ~3 s
- Compact company_info, analysis_summary: ~1 s each
- expire_snapshots + cleanup_orphans best-effort: ~few s each
- **Total: ~2 min** per pipeline

Reads post-compaction:
- OHLCV full count of 1.5M rows: **0.50 s** (vs 5+ s when fragmented)
- Per-ticker query (RELIANCE.NS, all dates): 0.13 s

## Related

- `shared/architecture/iceberg-maintenance` — the original compaction module + APIs
- ASETPLTFRM-315 — parent ticket (original compaction work, this extends with pipeline integration)
- CLAUDE.md "Backup before maintenance" hard rule — preserved by the fail-closed step 0
