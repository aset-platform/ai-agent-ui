# Iceberg maintenance enrollment — new tables MUST be in BOTH lists

When adding a write-heavy Iceberg table (any table the runtime writes to more than once per day), enroll it in **both** maintenance registries:

1. `backend/jobs/executor.py` → `_HOT_ICEBERG_TABLES` — daily compaction loop (`iceberg_maintenance` job).
2. `backend/maintenance/iceberg_maintenance.py` → `ALL_TABLES` — used by retention + orphan-sweep tooling. Also wire the date column into `DATE_COLUMNS` if retention applies.

## Why

Every Iceberg commit writes a fresh `metadata.json` carrying the FULL snapshot history embedded. Without periodic snapshot expiry, the history grows unbounded and each new metadata.json gets bigger. After thousands of commits the metadata can dwarf the actual data parquet by 100×+.

## How to apply

Whenever you create a new Iceberg table in the `algo` or `stocks` namespace AND a runtime path appends rows to it (LiveRuntime emissions, scheduler step writes, etc.), do the registry update in the **same PR** as the schema creation. Skipping this is invisible — there's no error, the table just silently bloats.

## Concrete reference incident — 2026-05-12

`algo.events` was missing from both lists since the algo-trading epic started shipping. Result:

- 11 GB warehouse footprint
- Only 59 MB was actual data parquet
- **5,901 metadata.json files** × ~2 MB each = the rest of the 11 GB
- Every `signal_generated` / `order_submitted_live` / `kite_postback_received` / fill emission compounded the bloat (10+/min during a live session)

Fixed via PR #216 (enrolment in both lists) + `cleanup_orphans_v2` one-shot reclaim that freed 10.68 GB (verified read-back OK). Permanent prevention is the registry update; the cleanup is now nightly via the standard maintenance job.

## Verification recipe when adding a new table

```bash
grep -nE "your_new_table|stocks\\.|algo\\." \
  backend/jobs/executor.py:_HOT_ICEBERG_TABLES \
  backend/maintenance/iceberg_maintenance.py:ALL_TABLES
```

If your table doesn't appear in both greps, it's not enrolled. Fix before merging.

## Related

- `shared/architecture/iceberg-maintenance` — overall maintenance design.
- `shared/architecture/iceberg-orphan-sweep-design` — `cleanup_orphans_v2` algorithm.
- `shared/conventions/iceberg-freshness-checks` — read-side companion.
