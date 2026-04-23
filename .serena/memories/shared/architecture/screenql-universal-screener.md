# ScreenQL — Universal Stock Screener

## Overview
Text-based query language on the Insights page ScreenQL tab.
Users write conditions like `pe_ratio < 15 AND piotroski_score >= 8`.
Backend parses into parameterized DuckDB SQL across Iceberg tables.

## Architecture
- Parser: `backend/insights/screen_parser.py` (tokenizer + recursive descent + SQL generator)
- Field catalog: 36 fields across 7 categories mapped to 6 Iceberg tables
- SQL: CTE-based with ROW_NUMBER dedup, dynamic JOINs (only referenced tables)
- Multi-table: `query_iceberg_multi()` in duckdb_engine.py creates views for all tables
- Endpoints: POST /v1/insights/screen, GET /v1/insights/screen/fields

## Query Syntax
- Operators: >, <, >=, <=, =, !=, CONTAINS (for tags)
- Connectors: AND, OR, parentheses
- Multi-line: implicit AND between lines
- String values: double-quoted ("Technology")

## RSI field
rsi_14 is extracted via `TRY_CAST(regexp_extract(rsi_signal, 'RSI:\\s*([\\d.]+)', 1) AS DOUBLE)`
in the analysis_summary CTE — NOT a standalone column.

## Dynamic columns
Base 5 always shown (ticker, company, sector, mcap, price + currency).
Query-referenced fields auto-added. Action column with analysis link.

## Known limitations (v1)
- tags/ticker_type fields need PG subquery (not in DuckDB)
- ^-prefixed index tickers not queryable
- No saved screens (URL encoding for bookmarks)
