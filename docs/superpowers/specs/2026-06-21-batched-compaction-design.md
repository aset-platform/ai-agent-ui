# Batched per-month Iceberg compaction — Design (ASETPLTFRM-442)

**Date:** 2026-06-21
**Branch:** `feature/batched-compaction` (worktree)
**Status:** design → (pending) plan

## Goal

Let byte-heavy intraday tables compact **incrementally** instead of being skipped. PR #270 added `_MAX_SAFE_COMPACT_BYTES = 1 GB`: `compact_table` SKIPS tables above it (e.g. `stocks.intraday_features`, 1.2 GB / 70M rows) because it reads the whole table into Arrow (`scan().to_arrow()`) → OOM. Skipping means those tables never fold their daily-append fragmentation. This adds a **per-month batched** path that compacts one month at a time (bounded memory), so byte-heavy tables self-compact without OOM.

PyIceberg 0.11.1 has **no** native `rewrite_data_files`/`optimize`, so this is a manual batched rewrite. `inspect.partitions()` IS available and exposes per-partition `file_count` + `total_data_file_size_in_bytes` from metadata (no data scan).

## Global constraints

- Line ≤79 (black/isort/flake8). `X | None` not `Optional`. No bare `except`. `_logger`, no `print`. (§4.2)
- Never `rm` Iceberg files; the existing orphan sweep reclaims superseded files. (§4.3 #20)
- Concurrent-writer safety: wrap writes in `retry_iceberg_op(...)`; `invalidate_metadata` after writes (§5.1, §6.4).
- Tests run via the one-off worktree container (`HOME=/root`, `PYTHONPATH=/app:/app/backend`, image `ai-agent-ui-backend:latest`); catalog-fixture tests need the compose env. (→ memory `worktree-docker-testing`)
- Branch off `dev`; squash-only PR; `Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>`.

## Architecture

### New helper — `_compact_table_by_month(table_name, repo) -> dict`
In `backend/maintenance/iceberg_maintenance.py`.

1. **Enumerate non-optimal months from metadata.** `tbl.inspect.partitions()` → for each `(ticker_bucket, bar_month)` partition, read `file_count`. Group by `bar_month`. A month is **non-optimal** when its total `file_count` exceeds its partition count (i.e. some bucket has >1 file). Months already at 1-file/partition are **skipped** (no read, no commit).
2. **Resolve `bar_month` → `year_month` string** (the filter key). `bar_month` is the `MonthTransform` value (months since 1970-01). Convert: `year = 1970 + m // 12`, `month = m % 12 + 1` → `f"{year}-{month:02d}"`. **Verify this against live `inspect.partitions()` output during implementation** (PyIceberg may already render the partition value as a readable `YYYY-MM`; if so, use it directly and drop the arithmetic).
3. **Rewrite each non-optimal month**, ascending:
   ```
   ym_filter = EqualTo("year_month", ym)
   arrow = tbl.scan(row_filter=ym_filter).to_arrow().cast(tbl.schema().as_arrow())
   retry_iceberg_op(table, lambda: tbl.overwrite(arrow, overwrite_filter=ym_filter))
   invalidate_metadata(table)
   ```
   This folds the month to 1 file per bucket (≤16 files). Memory bounded to one month (~12 MB for `intraday_features`).
4. **Per-month error isolation:** wrap each month in try/except; on failure log `exc_info=True`, append to `errors`, continue. One bad month never aborts the rest.
5. **Return** `{"table", "before", "after", "months_rewritten": int, "months_skipped": int, "errors": list, "batched": True}` (before/after = `_count_parquet_files`).

### Routing in `compact_table`
At the existing byte-ceiling check: when `total_bytes > _MAX_SAFE_COMPACT_BYTES`:
- if the table's schema has a `year_month` column → `return _compact_table_by_month(table_name, repo)`;
- else → keep today's skip-with-warning (`skipped_too_large_bytes`) — no generic batching (no such table exists).

`_require_repo`/`repo` is loaded as in the current read path. The whole-table fast path (small/normal tables, incl. `intraday_bars` ~510 MB) is unchanged.

## Data flow / correctness

- Scoping by the `year_month` **data column** (not the hidden `bucket`/`month` transforms) is the proven migration pattern. `overwrite(overwrite_filter=EqualTo("year_month", ym))` deletes exactly that month's rows and writes the merged month back → no cross-month data loss/duplication.
- Read→write within one month is idempotent (same rows in/out); row count per month preserved. A parity assert per month (`arrow.num_rows` vs pre-count) is optional — the overwrite_filter guarantees scoping; skip unless cheap.
- The maintenance loop already runs `cleanup_orphans_v2` after `compact_table`, which reclaims the superseded month files. No change there.

## Commit count

= number of non-optimal months. First run after a long skip-gap: up to ~102 (one commit/month). Steady state: ~1–2 (only the current + maybe previous month are non-optimal; older months are 1-file and skipped). Acceptable — far below the per-partition (1,632) alternative.

## Error handling

- `inspect.partitions()` failure → return `{"error": "inspect failed"}` (don't fall through to a full scan).
- Per-month overwrite failure → collected in `errors`, continue.
- Routed-but-no-`year_month` huge table → skip-with-warning (unchanged).

## Testing

Unit tests (mock `inspect.partitions()` + a fake table/repo; one-off container):
1. Non-optimal month → exactly one scoped `overwrite(overwrite_filter=EqualTo("year_month", ym))`; optimal months → no overwrite.
2. Mixed: 1 non-optimal + N optimal → `months_rewritten == 1`, `months_skipped == N`.
3. A month overwrite raising → captured in `errors`, other months still processed (no abort).
4. Routing: `compact_table` with bytes > 1 GB and a `year_month` column → delegates to the batched path; bytes > 1 GB without `year_month` → `skipped_too_large_bytes`.
5. `bar_month → year_month` conversion unit test (the formula / live-render handling).

Integration (compose env, optional manual): run on the real `stocks.intraday_features` and confirm it folds the current month, no OOM, row count unchanged.

## Out of scope

- Generic byte-budgeted batching for arbitrary partition specs (approach B) — YAGNI; `intraday_features` is the only >1 GB table and all byte-heavy tables here carry `year_month`.
- Removing/raising `_MAX_SAFE_COMPACT_BYTES` — keep it as the routing trigger.
- Changing the orphan-sweep or the maintenance pipeline wiring.

## Rollout

Land code (TDD) → PR → squash-merge → backend restart → next scheduled maintenance compacts `intraday_features` month-by-month (or run `compact_table('stocks.intraday_features')` once manually to verify the fold + no OOM).
