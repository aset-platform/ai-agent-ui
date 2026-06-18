# Iceberg write pattern: COW overwrite vs delete+append

## The problem with delete()+append()

The two-step pattern used in many write paths:

```python
tbl.delete(And(In("ticker", tickers), In("bar_date", bar_dates)))
tbl.append(arrow_tbl)
```

creates **two Iceberg commits per flush**:
1. A DELETE commit (adds a delete manifest)
2. An APPEND commit (adds a data manifest + new parquet files)

Over time this produces:
- 2× the expected parquet file count per partition
- 2× the manifest chain depth per flush
- Exponential metadata bloat with no compaction possible (too deep)

### Incident (2026-06-18)

`stocks.intraday_features` accumulated 64,733 parquet files (10 batches/day
× 499 tickers × months of history). Each batch wrote via delete+append.
Compaction tried `tbl.scan().to_arrow()` on 64k files → uvicorn froze for
10+ minutes.

## The fix: atomic COW overwrite

```python
tbl.overwrite(
    arrow_tbl,
    overwrite_filter=And(
        In("ticker", tickers),
        In("bar_date", bar_dates),
        In("interval_sec", interval_secs),
    ),
)
```

PyIceberg's `overwrite()` does a copy-on-write in **one snapshot**:
- Replaces all rows matching the filter with the new data
- Produces exactly **1 parquet file per affected partition**
- Creates **1 commit** (not 2)

Result: future writes stay at avg ~1 file/partition → compaction stays safe.

## When to use which

| Pattern | Use when |
|---|---|
| `tbl.overwrite(arrow, filter)` | Replacing rows for known keys (ticker+date upsert) |
| `tbl.append(arrow)` | Pure-append tables (audit logs, event streams) |
| `tbl.delete(filter) + tbl.append(arrow)` | **Never** — always replace with overwrite() |

## Tables fixed (PR #263, 2026-06-18)

- `stocks.intraday_features` — `backend/algo/jobs/intraday_features_daily_compute.py`
  - Old: `delete(And(...)) + append(arrow)`
  - New: `overwrite(arrow, overwrite_filter=And(...))`

## Tables still using delete+append (TODO)

`algo.events` write paths in:
- `backend/algo/broker/ws_multiplexer.py` — flushes every 50 WS events
- `backend/algo/live/runtime.py` — flushes on every `signal_generated`

These need the Tier 1-3 redesign (Redis live-WS + 30s batched flushes + COW).

## Related memories

- `shared/debugging/iceberg-deep-manifest-compaction-freeze` — why deep manifests freeze
- `shared/conventions/iceberg-maintenance-enrollment` — enrollment rules
