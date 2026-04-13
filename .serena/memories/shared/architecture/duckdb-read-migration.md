# DuckDB Read Migration — Complete (Apr 13, 2026)

## Status
ALL Iceberg reads in `stocks/repository.py` now use DuckDB-first with PyIceberg fallback. Previously only 3 methods were migrated; now all 16+ are done.

## Pattern (established in `_scan_ticker`)
```python
# DuckDB fast path
try:
    from backend.db.duckdb_engine import query_iceberg_df
    view = identifier.split(".")[-1]
    df = query_iceberg_df(identifier, f"SELECT * FROM {view} WHERE col = ?", [val])
    return df
except Exception:
    pass  # fall through
# PyIceberg fallback (unchanged)
```

## Methods Migrated (Apr 13)

### Internal helpers (highest leverage — all callers benefit)
- `_scan_two_filters()` — `WHERE col1 = ? AND col2 = ?`
- `_load_table_and_scan()` — delegates DF to `_table_to_df()` (already DuckDB)
- `_scan_ticker_date_range()` — `WHERE ticker = ? AND date >= ? AND date <= ?`
- `_scan_date_range()` — `WHERE date >= ? AND date <= ?`

### Public methods
- `get_stocks_by_sector()` — `WHERE sector = ?`
- `get_portfolio_holdings()` — `WHERE user_id = ? AND side = ?`
- `get_portfolio_transactions()` — `WHERE user_id = ?`
- `list_chat_sessions()` — `WHERE user_id = ?`
- `get_chat_session_detail()` — `WHERE user_id = ? AND session_id = ?`
- `insert_ohlcv()` read portion — `SELECT date FROM ohlcv WHERE ticker = ?`
- `insert_dividends()` read portion — `SELECT ex_date FROM dividends WHERE ticker = ?`
- `get_dashboard_llm_usage()` — `WHERE request_date >= ?` + optional user_id

### Data gap methods
- `insert_data_gap()`, `increment_gap_count()`, `get_unfilled_data_gaps()`, `resolve_data_gap()` — delegate to `_table_to_df()`

## Rules
- DuckDB primary, PyIceberg fallback — never remove PyIceberg
- Parameterize values via `?` — column names interpolated (internal constants only)
- Lazy imports inside try block
- `selected_fields` → project columns in SQL, not post-filter
