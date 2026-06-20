# Pipelines Cleanup — Design

**Date:** 2026-06-20
**Branch:** `feature/pipelines-cleanup` (worktree)
**Status:** design → (pending) plan

## Goal

Four durable code fixes that stop Iceberg maintenance/backup bloat from recurring and cut admin-UI latency. The one-time data reclaims (backup folder −43.4 GB; `algo.events` metadata chain 105→10 metadata.json / 70 MB) are already done operationally; this spec covers the **code** that prevents recurrence and finishes the `algo.events` data-file compaction through the proper path.

## Global constraints

- Line length 79 (black/isort/flake8). `X | None` not `Optional`. No bare `except`. `_logger`, no `print`. (CLAUDE.md §4.2)
- Never `rm` Iceberg files; use `cleanup_orphans_v2` / overwrite APIs. (§4.3 #20)
- Backup is fail-closed step 0 before destructive maintenance (§6.4) — preserve this invariant; we only *dedupe* backups, never remove the precondition.
- Redis cache: `TTL_STABLE=300`, key schema `cache:<area>:<endpoint>:<scope>`, `ttl=` kwarg. (§5.13)
- Tests: happy + 1 error path min; in-container `docker compose exec -T backend python -m pytest …`. (§4.4)
- Co-Authored-By: `Abhay Kumar Singh <asequitytrading@gmail.com>`. Branch off `dev`; squash-only PR.

---

## Thread A — `algo.events` self-heal

**Root causes (verified):**
1. `expire_snapshots()` in `backend/maintenance/iceberg_maintenance.py` is a **legacy no-op** — it logs "legacy no-op; use cleanup_orphans_v2" and returns. The weekly Long-Tail maintenance chain is retention → expire_snapshots(**no-op**) → compact_table(**skipped**), so the snapshot/manifest chain never self-heals.
2. The compaction guard `_MAX_AVG_FILES_PER_PARTITION = 50` (`iceberg_maintenance.py:792`) is **size-blind**: it skips `compact_table` whenever avg files/partition > 50 regardless of total bytes — so a 70 MB / 3,269-file table is refused the same as a 50 GB one.

**Fixes:**
- **A1 — wire real expiry into maintenance.** In the maintenance flow (`run_maintenance()` and/or the scoped path in `executor.py::execute_iceberg_maintenance`), replace the legacy no-op `expire_snapshots()` step with a call to the real `cleanup_orphans_v2(table, skip_backup=True)` (step-0 backup already taken upstream). Verify the exact call site during planning — the SCOPED maintenance path already runs a sweep per table (observed: "…sweep: deleted N files"); the gap is the `run_maintenance()` / weekly Long-Tail `expire_snapshots` no-op. Goal: every maintenance path that compacts also performs real snapshot expiry so the chain can't deepen unbounded.
- **A2 — size-aware compaction guard.** Add constant `_SMALL_TABLE_COMPACT_BYTES = 512 * 1024 * 1024`. In `compact_table` (`iceberg_maintenance.py:351`), before the `avg > _MAX_AVG_FILES_PER_PARTITION` skip (line ~442): compute the table's total on-disk data bytes (sum of referenced parquet sizes, or `du` of the data dir). If `total_bytes <= _SMALL_TABLE_COMPACT_BYTES`, **proceed with compaction** regardless of the avg-files-per-partition guard. The absolute `_MAX_SAFE_COMPACT_FILES = 40_000` ceiling still applies (hard OOM backstop). Net: skip only when `avg > 50 AND total_bytes > 512 MB`.
- **A3 — operational follow-on (in plan, not code):** after A2 ships + backend restart, run `compact_table('algo.events')` through the normal backed-up path → 3,269 → ~7–14 files. Same one-time run for `stocks.nse_delivery` (Thread D dependency).

**Tests:** unit-test the size-aware branch — a table with avg>50 but total_bytes ≤ 512 MB returns "compacted" not "skipped_deep_manifest"; a table with avg>50 and total_bytes > 512 MB still skips. Mock `_avg_files_per_partition` + the byte-size helper.

**Files:** `backend/maintenance/iceberg_maintenance.py` (guard + expiry wiring), `backend/jobs/executor.py` (maintenance step if the no-op is invoked there), tests in `tests/backend/`.

---

## Thread B — Backup dedup + folder auto-cleanup

**Root causes (verified):** the two retention jobs call `backup_table()` **directly**, bypassing the `verify_or_backup()` dedup; and the 463-dir per-table backup bloat re-accumulates because `cleanup_per_table_backups.py` is **never called automatically**.

**Fixes:**
- **B1 — route retention through dedup.** `backend/algo/jobs/intraday_bars_retention.py:~191` and `backend/algo/jobs/algo_events_retention.py:~181`: replace `backup_table(X)` with `verify_or_backup([X])`. Fallback path unchanged (verify_or_backup calls backup_table internally when no fresh manifest). Preserves the fail-closed invariant.
- **B2 — auto-cleanup in daily backup.** In `execute_backups_daily()` (`executor.py:2686`), after `write_manifest()` succeeds, call `cleanup_per_table_backups.main(dry_run=False)`. Wrap in try/except (log `exc_info=True`, non-fatal — backup success must not depend on cleanup).
- **B3 — same-day dedup guard.** In `backup_table()` (`backend/maintenance/backup.py:79`): if the destination dir already exists and is non-empty, log and return its path without re-rsyncing (prevents multiple same-day per-table copies, esp. from multiple retention runs).

**Decision:** keep `MAX_BACKUPS = 2` (full snapshots) — not raising it (out of scope; current 48 h window is adequate and storage already reclaimed).

**Tests:** retention job calls `verify_or_backup` (mock, assert not `backup_table` directly when manifest fresh); `backup_table` returns early on existing non-empty same-day dir; `execute_backups_daily` invokes cleanup after manifest.

**Files:** `intraday_bars_retention.py`, `algo_events_retention.py`, `backup.py`, `executor.py`; tests.

---

## Thread C — Backups UI scan

**Root cause (verified):** `list_backups()` (backing `GET /admin/backups`) calls `_dir_size_mb()` (which spawns `du -sk`) on **all 467 dirs before** filtering to the 2 full snapshots — 465 wasted subprocess spawns per cold request.

**Fix:** apply the `_FULL_SNAPSHOT_NAME_RE` filter **before** the `_dir_size_mb()` loop, so only full-snapshot dirs are sized. (After Thread B, per-table dirs stay ≤ ~24, but this makes the endpoint O(full-snapshots) regardless.)

**Tests:** `list_backups()` calls the size helper only for full-snapshot dirs (mock `_dir_size_mb`, assert call count == number of full snapshots, not total dirs).

**Files:** the backups admin route module (locate during planning — `_admin_backups_list_impl` / `list_backups`); tests.

---

## Thread D — Data-health UI latency

**Root causes (verified, ranked):**
1. `_admin_data_health()` calls no-arg `invalidate_metadata()` at the top of **every** request (`routes.py:~2418`), nuking the whole DuckDB metadata cache → re-globs all tables' metadata each call.
2. `_ohlcv_health()` (`routes.py:~2499-2562`) runs **3** full-table scans of `stocks.ohlcv` where 1 `GROUP BY ticker` would return count + null-list.
3. Frontend SWR `dedupingInterval: 5_000` (`frontend/hooks/useAdminData.ts:~690`) << Redis TTL 60 s; and no protection when Redis absent (`_NoOpCache`).
4. `PipelineAssertionsCard` polls `algo.events` every 60 s (`usePipelineAssertions.ts:~57`) with no cache.

**Fixes:**
- **D1** — delete the no-arg `invalidate_metadata()` in `_admin_data_health()` (targeted per-table invalidations from write paths already exist; the read path must not nuke the cache).
- **D2** — collapse `_ohlcv_health()` to a single `GROUP BY ticker` query returning both the NULL-close count and the offending-ticker list.
- **D3** — `useAdminData.ts`: `dedupingInterval` 5_000 → 60_000. Add an in-process time-bucketed fallback cache in the data-health endpoint for when `REDIS_URL` is empty (so the no-Redis path isn't unprotected).
- **D4** — add Redis caching (`TTL_STABLE`) to the pipeline-assertions endpoint's `algo.events` query (write-through invalidation on algo.events writes per §5.13). Benefits further from the now-compacted algo.events (Thread A3).
- **nse_delivery** — no separate code; Thread A's size-aware guard + the A3 operational compaction run reduce its 2,642 files, cutting its scan cost in data-health.

**Tests:** data-health handler does NOT call `invalidate_metadata()` (assert not called); `_ohlcv_health` issues a single iceberg query (mock `query_iceberg_df`, assert call count); pipeline-assertions endpoint returns cached result on 2nd call (Redis mock). Frontend: vitest on the SWR hook config value.

**Files:** `backend/.../routes.py` (data-health + ohlcv health + pipeline-assertions route), `frontend/hooks/useAdminData.ts`, `frontend/hooks/usePipelineAssertions.ts`; tests.

---

## Out of scope

- Raising `MAX_BACKUPS`. Repartitioning `algo.events` (spec is already correct: `mode + month`). Repartitioning `nse_delivery` (compaction suffices). Any change to the live write path (already fixed by PRs #264/#265).

## Rollout / ordering

1. Land Thread A + B + C + D code (one plan, task-by-task, subagent-driven).
2. Backend restart (maintenance/guard code change) + `redis-cli FLUSHALL` (§6.2).
3. Operational A3: run `compact_table('algo.events')` and `compact_table('stocks.nse_delivery')` through the now-size-aware, backed-up path; verify file collapse.
4. Verify data-health + backups endpoints latency dropped (time the cold-cache requests before/after).

## Verification

- `algo.events`: post-A3 ≤ ~14 data files; weekly maintenance now self-expires (no-op replaced).
- backups dir stays ≤ ~30 dirs across days (B2 auto-cleanup); retention runs don't add per-table dirs when daily snapshot fresh (B1).
- `/admin/backups` cold-cache request: 465→2 `du` spawns.
- `/admin/data-health` cold-cache latency materially reduced (no full metadata re-glob; ohlcv 3→1 scan).
