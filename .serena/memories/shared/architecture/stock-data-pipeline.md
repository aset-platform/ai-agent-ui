# Stock Data Pipeline Architecture (Sprint 5)

## Overview
Bulk-ingest and maintain stock data for Nifty 500 (499 stocks) from dual sources.
Module: `backend/pipeline/` (17 files). CLI: 12 commands via `python -m backend.pipeline.runner`.

## Data Model (4 new PG tables)
- `stock_master` — canonical stock identity (symbol, yf_ticker, nse_symbol, ISIN, exchange, sector)
- `stock_tags` — temporal many-to-many (nifty50, nifty100, nifty500, largecap, midcap)
- `ingestion_cursor` — keyset pagination with crash-safe per-ticker advance
- `ingestion_skipped` — failed ticker log with 5-category error classification

## Source Strategy
- **bulk/daily**: YfinanceSource (fast, batch-capable)
- **retry/correct**: NseSource via jugaad-data (NSE authoritative)
- **chat**: RacingSource (NSE vs yfinance, fastest wins)
- **fundamentals**: YfinanceSource only (.info, .dividends)

## Ticker Format
ALL Indian stocks use `.NS` suffix everywhere (Iceberg, PG registry, frontend, scheduler).
`stock_master.symbol` is canonical (no suffix) — identity table only.
`stock_master.yf_ticker` has `.NS` — used for all data operations.

## Market Detection
`backend/market_utils.py` — `detect_market(ticker, registry_market)`.
Checks suffix first (.NS/.BO), then registry market field (NSE/BSE/INDIA).

## CLI Commands
download, seed, bulk, bulk-download, fundamentals, daily, fill-gaps, status, skipped, retry, correct, reset

## Gotchas
- cache_warmup.py poisoned Redis with bare registry. Fixed: disabled registry warmup.
- jugaad-data hangs without timeout. Added 60s asyncio.wait_for in NseSource.
- Iceberg SQLite can't handle concurrent writes. Fundamentals: Semaphore(1).
- _update_registry must preserve existing market field.
- Scheduler executor needs yf_map to append .NS for canonical tickers.
- get_forecasts_summary internal call must pass ticker=None explicitly.
