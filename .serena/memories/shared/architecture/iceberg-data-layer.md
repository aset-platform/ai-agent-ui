# Iceberg Data Layer

## Core Rules

- ALL stock data lives in Iceberg at
  `~/.ai-agent-ui/data/iceberg/`.
- `_require_repo()` raises `RuntimeError` if unavailable;
  `_get_repo()` returns `None`.
- **Copy-on-write upserts**: read full table -> mutate DataFrame ->
  `table.overwrite()` (no native UPDATE in PyIceberg 0.11).
- `_load_parquet()` reads from Iceberg, not flat files.
- Dashboard uses `_get_ohlcv_cached` / `_get_forecast_cached`.
- Single repo singleton via `_stock_shared.py`.
- Writes MUST NOT be silenced — failures propagate to tool
  exception handlers.

## Anti-Patterns

| Anti-Pattern | Correct Pattern |
|---|---|
| Flat file reads for stock data | Iceberg via `_load_parquet()` / `StockRepository` |
| Duplicate repo singletons | Import from `_stock_shared.py` |
| Silencing Iceberg write failures | Let errors propagate to tool handler |
| Direct `tbl.append()`/`tbl.overwrite()` | Use `_append_rows()` / `_overwrite_table()` helpers |

## Performance

- Copy-on-write is expensive: `table.overwrite()` reads + rewrites
  the full table. Batch updates; minimize calls.
- N+1 queries: Load all data in one call, not one query per
  iteration.
- Cache awareness: `~/.ai-agent-ui/data/cache/` provides same-day
  caching. Clear only on refresh (`_clear_tool_cache()`).
- `CachedRepository` (`stocks/cached_repository.py`) wraps
  `StockRepository` with TTL cache for read-heavy methods.

## Concurrency & Retry

- `_retry_commit(identifier, operation, *args, **kwargs)` retries
  Iceberg writes up to 3x with exponential backoff (0.5s, 1s, 2s)
  on `CommitFailedException`
- `_append_rows` and `_overwrite_table` both delegate to `_retry_commit`
- Table object is reloaded on each retry for fresh snapshot
- All write methods in `StockRepository` use these helpers — no
  direct `tbl.append()`/`tbl.overwrite()` calls remain
- Needed because dashboard card refresh runs 4 concurrent pipelines
  via `ThreadPoolExecutor(max_workers=4)`

## Scoped Deletes

5 methods use scoped delete+append instead of full-table overwrite
for better concurrency:
- `update_ohlcv_adj_close`
- `upsert_technical_indicators`
- `insert_forecast_series`
- `insert_quarterly_results`
- `delete_ticker_data`

Uses `_delete_rows()` helper with PyIceberg row-level deletes.
`_retry_commit()` extended with `**kwargs` for `delete_filter`.

## Freshness Gates

- OHLCV fetch skipped if latest data < 1 day old
- Prophet forecast skipped if forecast run < 7 days old
- Analysis skipped if done today (Iceberg check)
- All gates are non-blocking (skip silently, log reason)

## Inspection

```python
from stocks.repository import StockRepository
repo = StockRepository()
print(sorted(repo.get_all_registry().keys()))
for t in sorted(repo.get_all_registry().keys()):
    df = repo.get_ohlcv(t)
    adj = df["adj_close"].notna().mean() * 100
    print(f"{t}: {len(df)} rows, {adj:.1f}% adj_close")
```
