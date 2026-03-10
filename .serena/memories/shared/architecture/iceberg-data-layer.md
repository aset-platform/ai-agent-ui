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

## Performance

- Copy-on-write is expensive: `table.overwrite()` reads + rewrites
  the full table. Batch updates; minimize calls.
- N+1 queries: Load all data in one call, not one query per
  iteration.
- Cache awareness: `~/.ai-agent-ui/data/cache/` provides same-day
  caching. Clear only on refresh (`_clear_tool_cache()`).

## Concurrency & Retry (Mar 10, 2026)

- `_retry_commit(identifier, operation, *args)` retries Iceberg writes up to 3x with exponential backoff (0.5s, 1s, 2s) on `CommitFailedException`
- `_append_rows` and `_overwrite_table` both delegate to `_retry_commit`
- Table object is reloaded on each retry for fresh snapshot
- All 8 write methods in `StockRepository` use these helpers — no direct `tbl.append()`/`tbl.overwrite()` calls remain
- Needed because dashboard card refresh runs 4 concurrent pipelines via `ThreadPoolExecutor(max_workers=4)`

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
