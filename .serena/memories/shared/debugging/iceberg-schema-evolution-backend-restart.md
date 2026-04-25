# Iceberg schema evolution + backend in-process cache gotcha

## Symptom

After `tbl.update_schema().add_column(...)` succeeds against the live Iceberg catalog, the new column is:
- ✓ present in the on-disk schema (verified via `PyIceberg _get_catalog().load_table(...).schema().fields`)
- ✓ queryable via a fresh DuckDB session
- ✗ **not visible** to the running backend process — its `/insights/screener` response still returns rows without the new field, even after `cache.FLUSHALL` + `invalidate_metadata()`

Hit this twice during ASETPLTFRM-332 (adding `peg_ratio_yf` to `company_info`).

## Root cause

The backend worker process holds a long-lived DuckDB connection (or imported metadata handle) that caches the table's schema at open time. `invalidate_metadata()` tells DuckDB to refetch metadata, but for the Python process's own DuckDB connection it's scoped — and the Iceberg table-reference cached in `query_iceberg_df`'s internals doesn't always pick up mid-process schema changes.

Redis cache is a secondary layer — flushing it clears serialised response payloads but doesn't help with the process-internal caches.

## Fix

`docker compose restart backend` after the schema evolution completes. Full process recycle forces a fresh DuckDB connection + fresh imports of whatever holds metadata handles.

Redis FLUSHALL is still needed separately to invalidate the 300s screener response cache.

## Full recovery sequence

```bash
# 1. Run the evolution
docker compose exec backend python3 -c \
  "from stocks.create_tables import evolve_company_info_peg_yf; \
   evolve_company_info_peg_yf()"

# 2. Flush Redis response cache
docker compose exec redis redis-cli FLUSHALL

# 3. Force process recycle — this is the critical step
docker compose restart backend

# 4. Wait for /v1/health to come up
for i in 1 2 3 4 5 6 7; do
  curl -s http://localhost:8181/v1/health >/dev/null && break
  sleep 3
done

# 5. Verify the new column is visible in the API response
```

## Why `invalidate_metadata()` alone isn't enough

CLAUDE.md has the `invalidate_metadata()` guidance for write-then-read ordering within a single connection. It handles DuckDB's catalog-refresh scenario for writes through `StockRepository`. But for **schema changes that happen outside the backend's own write path** (e.g., a separate `evolve_*` script called during deploy), the backend process never participates in the write and never knows to invalidate.

Short version: `invalidate_metadata()` = "I wrote data, refresh snapshot visibility." Backend restart = "The table shape itself changed, drop everything."

## Applies to

Any Iceberg schema change touching a table the backend reads:
- Adding columns via `update_schema().add_column()`
- Future: renaming columns, changing types (nullable → required)

Does NOT apply to:
- Data-only appends / overwrites (those work with `invalidate_metadata()`)
- New rows added to an existing schema

## Deployment implication

Schema-evolution tickets need two deploy steps per environment (dev/qa/release/main):
1. Run evolution against the env's Iceberg catalog
2. Recycle the backend container

PR descriptions should document both. `insert_company_info` in `stocks/repository.py` is defensive (drops unknown columns from the write payload) so step 1 failing doesn't crash writes during the rollout window.

## Related

- ASETPLTFRM-332 — first hit, documented in PR #123 body
- CLAUDE.md Iceberg Maintenance section — already notes "NEVER delete metadata/parquet files directly" + `invalidate_metadata()` pattern
