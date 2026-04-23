# Iceberg Maintenance System

## Module
`backend/maintenance/` — backup.py + iceberg_maintenance.py

## Backup
- Location: `/Users/abhay/Documents/projects/ai-agent-ui-backups/`
- Format: `backup-YYYY-MM-DD/warehouse/` + `catalog.db`
- Rotation: keep 2 latest, delete older
- Always backup BEFORE compaction/retention
- Docker mount: added in docker-compose.override.yml

## Compaction
- `compact_table(table_name)`: reads via DuckDB, overwrites via PyIceberg
- Produces 1 file per partition instead of many small files
- OHLCV: 8670 → 817 files, reads 9s → 0.24s
- company_info: 4055 files (830 rows!) → 1 file

## CRITICAL RULES
1. NEVER delete .metadata.json files — SQLite catalog references them
2. NEVER delete .parquet files directly — use Iceberg overwrite() API
3. cleanup_orphans only removes empty directories, not files
4. If catalog breaks: `sqlite3 catalog.db "UPDATE iceberg_tables SET metadata_location='file:///path' WHERE table_name='xxx'"`

## Dead tables dropped
- stocks.scheduler_runs (25 GB) — migrated to PG
- stocks.scheduled_jobs (824 KB) — migrated to PG
- stocks.technical_indicators (2.3 GB) — unused, indicators computed on-the-fly

## Post-pipeline hook
pipeline_executor.py calls expire_snapshots() on 4 key tables after successful runs.

## Freshness gate fix
batch_refresh.py line 429: `latest >= today` (not yesterday).
OHLCV upsert: scoped delete + re-append for today's rows.
