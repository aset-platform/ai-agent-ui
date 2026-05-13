# Iceberg compact — DuckDB stale-read row loss

**Incident 2026-05-12**: `stocks.regime_history` lost the just-written 2026-05-12 row during compaction. Forensics + fix below.

## Snapshot timeline that exposed the bug

| IST | Op | Δ rows | Source |
|---|---|---|---|
| 21:45:23 | APPEND | +1 | Classifier wrote 2026-05-12 row → total 3050 |
| 21:46:49 | DELETE | -3050 | `compact_table` step 1 of `tbl.overwrite()` |
| 21:46:49 | APPEND | +3049 | `compact_table` step 2 — **ONE ROW SHORT** |
| 2026-05-13 09:35 | DELETE + APPEND | 3049 / 3049 | Next compaction confirms 3049 is the new ground truth |

## Root cause

The classifier wrote 1 new row via `tbl.append(...)` and called `invalidate_metadata(REGIME_HISTORY_TABLE)` at line 113 of `backend/algo/regime/repo.py`. That clears the in-process `_meta_cache` dict in `backend/db/duckdb_engine.py`.

86 seconds later, the maintenance compaction step ran:

```python
# OLD code in compact_table
df = query_iceberg_df(
    table_name,
    f"SELECT * FROM {view_name}",
)
# ... df → arrow → tbl.overwrite(arrow)
```

`query_iceberg_df` resolves the metadata file path via `_resolve_metadata(table_name)` which reads `_meta_cache`. The cache had been invalidated (cleared) but **a concurrent endpoint request between the classifier write and the compaction read had re-populated it** with the post-write metadata path. So far so good.

Then `cleanup_orphans_v2` expired old snapshots and unlinked orphan metadata files. The path the cache held pointed at a file the orphan sweep was about to delete. Reading the file at compaction time would have worked (file still present), but the snapshot view DuckDB built from it was off by one row — the file existed but the in-memory iceberg-scan reader served a snapshot that was missing the latest append. This is the bug that's hard to see without reading the snapshot timestamps side-by-side with the maintenance log timeline.

Result: 3049 rows read, 3050 deleted, 3049 written back. Yesterday's row gone.

## Fix (PR #219 commit b23bb1c)

`compact_table` now reads through PyIceberg directly — same `Table` object that performs the overwrite:

```python
tbl = repo.load_table(table_name)
tbl.refresh()                       # <-- forces metadata to match catalog
arrow = tbl.scan().to_arrow()       # <-- bypasses _meta_cache entirely
```

Reader and writer share the same snapshot, which is the only invariant compaction needs. Bonus: skips the pandas roundtrip.

## Why this hadn't surfaced before

The race needs three conditions to align:
1. A write happens (classifier append)
2. A foreground request hits `_resolve_metadata` between the write and the next compaction (cache repopulate)
3. The orphan sweep deletes the cached metadata file between steps 2 and the next compaction (cache stale)

Most tables aren't hot enough on the read side to hit (2). `regime_history` happens to be polled every 60 s by the frontend (`/v1/algo/regime/current` SWR refresh), which makes (2) reliable. Most other tables in `_HOT_ICEBERG_TABLES` aren't polled with the same cadence.

## How to detect future occurrences

Two signals:
1. **`compact_table` returns `rows != before_write + new_rows_appended`** — log + alert.
2. **Snapshot pattern**: `APPEND N` followed immediately by `DELETE M` + `APPEND M-1` is the signature. Add a maintenance assertion that compacts must produce `delete == append.added_records + 1` (the +1 absorbs the snapshot expiry write).

Both are candidates for the pipeline-quality-assertions framework (ASETPLTFRM-380).

## Recovery procedure if it recurs

```python
from datetime import date
from backend.algo.regime.classifier_job import run_classifier
run_classifier(as_of=date(YYYY, MM, DD))   # rewrites the lost row
```

The NaN-replaceable upsert is idempotent — re-running on an existing row pre-deletes then appends, so it's safe to run any time.

## Related memories

- `shared/architecture/iceberg-orphan-sweep-design` — the sweep that participates in the race
- `shared/conventions/iceberg-maintenance-enrollment` — the dual-list enrollment that determines which tables hit this code path

## Tests pinning the fix

- `backend/maintenance/tests/test_compact_table_no_row_loss.py::test_compact_preserves_every_row_from_scan` — pins the round-trip invariant
- `backend/maintenance/tests/test_compact_table_no_row_loss.py::test_compact_empty_table_short_circuits` — no-op short-circuit
