# DuckDB Read Migration (Sprint 6)

## What changed
All Iceberg read paths in `insights_routes.py` and `dashboard_routes.py` migrated from PyIceberg (`_scan_tickers`, `_table_to_df`, `get_*_batch`) to DuckDB (`query_iceberg_df`).

## Metadata cache (`backend/db/duckdb_engine.py`)
- `_meta_cache: dict[str, str]` — in-memory cache of table_name → metadata.json path
- Avoids filesystem glob (~30ms) on every query
- `invalidate_metadata(table_name)` called automatically after every Iceberg write via `_retry_commit()` in repository.py
- Thread-safe with `_meta_lock`

## Key optimization patterns
1. **Column projection**: `SELECT col1, col2` instead of `SELECT *`
2. **Date filters pushed to SQL**: OHLCV limited to last 45 days for sparklines, 10 days for portfolio
3. **Window functions**: `ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC)` for latest close per ticker — avoids scanning 1.4M rows
4. **Statement type filter in SQL**: quarterly endpoint pushes filter to DuckDB instead of Python

## Benchmark results (cold, superuser with 748 tickers)
| Endpoint | Before | After |
|---|---|---|
| Screener | 3.2s | 0.11s |
| Targets | 3.4s | 0.03s |
| Dividends | 3.8s | 0.04s |
| Risk | 1.9s | 0.05s |
| Quarterly | 1.7s | 0.02s |
| Registry | 5.5s | 0.17s |
| Home | 3.6s | 0.05s |
| Recommendations | 3.0s | 0.03s |

## What still uses PyIceberg (intentionally)
- `get_portfolio_holdings` / `get_portfolio_transactions` — small tables with user_id predicate pushdown
- `get_ohlcv(single_ticker)` — chart endpoint, single ticker
- `get_latest_forecast_series` — single ticker forecast
- `update_scheduler_run` — needs full table load-modify-overwrite pattern
- All Iceberg **writes** — PyIceberg is the only write path

## Gotcha
- DuckDB `invalidate_metadata()` MUST be called after writes, otherwise reads see stale snapshots
- Already wired into `_retry_commit()` — no manual calls needed
