---
name: iceberg-maintenance-smart-skip-and-scoped
description: Two-layer maintenance perf fix shipped 2026-05-14/15 — smart-skip when partitions optimal (PR #223) + scoped maintenance via pipeline_steps.payload (PR #225). Prevents the 6h "overwrite failed" pattern on heavily-fragmented tables.
metadata:
  type: architecture
---

# Iceberg maintenance perf workstream

Two complementary fixes that together eliminate the failure mode where `iceberg_maintenance` ran 6+ hours on a heavily-fragmented table and hit PyIceberg's atomic-overwrite branch-ref conflict ("overwrite failed").

## Layer 1 — Smart-skip (PR #223, commit included in `a0bbcc5`)

`backend/maintenance/iceberg_maintenance.py::is_compaction_already_optimal(table_dir)`:

- Walks the table directory, counts parquet files per leaf partition dir
- Returns True iff `avg files per partition ≤ 1.5` (`_OPTIMAL_FILES_PER_PARTITION` constant)
- `compact_table()` checks this gate immediately after the file count. When optimal, returns `{skipped_optimal: True, partitions, avg_files_per_partition}` and exits BEFORE the read+overwrite cycle.

**Why 1.5**: leaves margin for one in-flight write per partition before triggering a rewrite. `avg=1.0` (perfect) all the way to `1.5` (slightly above ideal) → skip. Above 1.5 → compact.

**Composes with orphan-sweep** — the sweep runs as a separate caller path even when smart-skip fires; expires dead snapshots and reclaims orphaned files. The compact_table optimization is solely about avoiding the wasted overwrite.

## Layer 2 — Scoped maintenance (ASETPLTFRM-418, PR #225 `008665b`)

`iceberg_maintenance` accepts `payload.tables: list[str] | None`. When provided, scopes:

- **Backup phase**: per-table `backup_table()` calls instead of `run_backup()` (full warehouse rsync)
- **Compact phase**: iterates `tables` only, not all 12 `_HOT_ICEBERG_TABLES`
- **Orphan-sweep**: same scope

When `None`: legacy behavior preserved (preserves manual-trigger admin paths).

Plumbing:
1. Alembic migration `c9d0e1f2a3b4` adds `pipeline_steps.payload jsonb DEFAULT '{}'::jsonb`
2. `backend/jobs/pipeline_executor.py::_run_step` uses `inspect.signature(executor_fn).parameters` to opt-in payload kwarg ONLY for wrappers that declare it. 25+ legacy wrappers stay backward-compat untouched.
3. Per-pipeline payloads:
   - Intraday Bars Daily → 4 intraday tables
   - India Regime Daily → 4 regime/factor tables
   - India Daily → 14 OHLCV/sentiment/piotroski/fundamentals tables (applied via `scripts/migrate_pipeline_payloads.py` — no Python seed exists)
   - USA Daily → 10 US-scoped tables

## Live impact (verified 2026-05-15)

Intraday Bars Daily Pipeline ran with both layers active:

| Phase | Pre-fix | Post-fix |
|---|---|---|
| Backup | full warehouse rsync (~1-2 GB, ~16 min) | per-table 9.7 GB / 1m 37s for 4 intraday tables |
| Compact of `stocks.intraday_bars` (22k partitions, 26k files, avg 1.19) | 6h rewrite, hit "overwrite failed" | smart-skip in 0.0s |
| Compact of `stocks.intraday_features` (529 files, 497 partitions, avg 1.06) | n/a (new table) | smart-skip in 0.0s |
| Orphan-sweep on intraday_bars | n/a | deleted 3,917 orphans / 84.7 MB in 16s |

**Wall-clock savings**: ~25 min/day across pipelines. Concurrency-conflict risk eliminated on tables the pipeline didn't actually touch.

## Tests

- `backend/maintenance/tests/test_compaction_skip_optimal.py` — 13 tests covering avg calc, threshold edge, empty/missing dirs, full `compact_table` integration with mocked repo
- `backend/maintenance/tests/test_scoped_maintenance.py` — 10 tests covering scoped vs unscoped paths, payload.tables filter, smart-skip composition
- `backend/jobs/tests/test_pipeline_executor_payload.py` — 5 tests for inspect-based dispatch

## Anti-pattern (avoided)

Initial instinct was to compact heavily-fragmented `stocks.intraday_bars` (29,809 small parquets accumulated from PR #220's Nifty 500 backfill). Two 6h attempts both failed with "overwrite failed". The fix that actually worked: **drop + Kite re-backfill** in 30 min (see `nuke-rebuild-faster-than-fragmented-compaction`).

## Cross-refs

- `centralized-feature-engine` — the workstream that exposed these issues
- `nuke-rebuild-faster-than-fragmented-compaction` — companion lesson
- CLAUDE.md §6.4 (Iceberg / DuckDB pitfalls)
