# Stocks — Hybrid Storage Layer (Iceberg + PostgreSQL)

The `stocks/` package provides an Apache Iceberg-backed persistence layer for stock analytics data. Row-level OLTP tables (`stocks.registry`, `stocks.scheduled_jobs`) have been migrated to PostgreSQL; the remaining 14 append-only tables stay on Iceberg.

## Architecture

```
stocks/
├── __init__.py        — package docstring + public API note
├── create_tables.py   — idempotent Iceberg table init (14 tables)
├── repository.py      — StockRepository: all reads + writes
└── backfill.py        — one-time migration of existing flat files

backend/db/
├── engine.py          — async SQLAlchemy session_factory
├── models.py          — ORM models (users, tickers, payments,
│                        registry, scheduled_jobs)
├── user_repository.py — UserRepository facade (OLTP auth)
├── pg_stocks.py       — registry + scheduler PG functions
├── duckdb_engine.py   — DuckDB query layer (reads Iceberg parquet)
└── migrations/        — Alembic async migrations
```

Iceberg is the **single source of truth** for all stock analytics
data (OHLCV, forecasts, indicators, etc.). PostgreSQL owns row-level
CRUD for registry and scheduler. All backend tool functions write to
Iceberg for analytics; flat files in `data/raw/` and `data/forecasts/`
are local backup only.

---

## Tables

### Iceberg tables (14 — append-only / scoped-delete)

| Table | Namespace | Write strategy | Primary key |
|-------|-----------|----------------|-------------|
| `stocks.company_info` | stocks | Append-only snapshots | (ticker, fetched_at) |
| `stocks.ohlcv` | stocks | Append + deduplicate | (ticker, date) |
| `stocks.dividends` | stocks | Append + deduplicate | (ticker, ex_date) |
| `stocks.technical_indicators` | stocks | Upsert partition | (ticker, date) |
| `stocks.analysis_summary` | stocks | Append-only snapshots | (ticker, analysis_date) |
| `stocks.forecast_runs` | stocks | Append-only | (ticker, horizon_months, run_date) |
| `stocks.forecasts` | stocks | Replace per run | (ticker, horizon_months, run_date) |
| `stocks.quarterly_results` | stocks | Append + deduplicate | (ticker, period_end) |
| `stocks.sentiment_scores` | stocks | Append (1 row/ticker/day) | (ticker, score_date) |
| `stocks.llm_pricing` | stocks | Append-only | (provider, model, effective_from) |
| `stocks.llm_usage` | stocks | Append-only | (request_id) |
| `stocks.scheduler_runs` | stocks | Append-only | run_id |
| `stocks.portfolio_transactions` | stocks | Append-only | (transaction_id) |
| `auth.audit_log` | auth | Append-only | event_id |
| `auth.usage_history` | auth | Append-only | (user_id, month) |

### PostgreSQL tables (5 — migrated from Iceberg, Sprint 4)

| Table | ORM model | Pattern |
|-------|-----------|---------|
| `stocks.registry` | `StockRegistry` | Upsert via `pg_stocks.py` |
| `stocks.scheduled_jobs` | `ScheduledJob` | Upsert via `pg_stocks.py` |
| `auth.users` | `User` | CRUD via `UserRepository` |
| `auth.user_tickers` | `UserTicker` | Upsert + delete |
| `auth.payment_transactions` | `PaymentTransaction` | Insert + read |
| `public.user_memories` | `UserMemory` | pgvector semantic memory (768-dim embeddings) |

> **pgvector:** `user_memories` uses the `vector` extension
> (`pgvector/pgvector:pg16` Docker image). Stores per-user
> session summaries, structured facts (JSONB), and preferences
> with cosine-similarity retrieval via IVFFlat index.

---

## Initialisation

Tables are created automatically by `run.sh start` via `_init_stocks()`:

```bash
./run.sh start   # calls stocks/create_tables.py on every start (idempotent)
```

To create tables manually:

```bash
cd ai-agent-ui && source ~/.ai-agent-ui/venv/bin/activate
python stocks/create_tables.py
```

---

## Backfill

After the tables are created, run the backfill script once to seed all historical flat-file data:

```bash
cd ai-agent-ui && source ~/.ai-agent-ui/venv/bin/activate
python stocks/backfill.py
```

The backfill is **idempotent** — re-running it will not create duplicate rows. It processes tickers in this order:

1. `stocks.registry` — from `data/metadata/stock_registry.json`
2. `stocks.company_info` — from `data/metadata/{TICKER}_info.json`
3. `stocks.ohlcv` — from `data/raw/{TICKER}_raw.parquet`
4. `stocks.dividends` — from `data/processed/{TICKER}_dividends.parquet`
5. `stocks.technical_indicators` — computed from OHLCV via `_calculate_technical_indicators()`
6. `stocks.analysis_summary` — computed from OHLCV (skips if today's row exists)
7. `stocks.forecasts` — from `data/forecasts/{TICKER}_{N}m_forecast.parquet`
8. `stocks.forecast_runs` — minimal metadata row per forecast file

---

## Dual-write in backend tools

Every backend tool that writes flat files also writes to Iceberg. All Iceberg writes are wrapped in `try/except` — a failure never breaks the tool's normal behaviour.

| Tool function | Iceberg write |
|---------------|---------------|
| `fetch_stock_data` | `repo.insert_ohlcv()` + `repo.upsert_registry()` |
| `get_stock_info` | `repo.insert_company_info()` |
| `get_dividend_history` | `repo.insert_dividends()` |
| `analyse_stock_price` | `repo.upsert_technical_indicators()` + `repo.insert_analysis_summary()` |
| `forecast_stock` | `repo.insert_forecast_run()` + `repo.insert_forecast_series()` |

---

## StockRepository API

```python
from stocks.repository import StockRepository

repo = StockRepository()

# Registry
repo.upsert_registry(ticker, last_fetch_date, total_rows, start, end, market)
repo.get_registry(ticker=None)  # None → all rows

# Company info
repo.insert_company_info(ticker, info_dict)
repo.get_latest_company_info(ticker)
repo.get_all_latest_company_info()

# OHLCV
repo.insert_ohlcv(ticker, df)        # returns rows inserted
repo.get_ohlcv(ticker, start, end)
repo.get_latest_ohlcv_date(ticker)

# Dividends
repo.insert_dividends(ticker, df, currency="USD")
repo.get_dividends(ticker)

# Technical indicators
repo.upsert_technical_indicators(ticker, df)
repo.get_technical_indicators(ticker, start, end)

# Analysis summary
repo.insert_analysis_summary(ticker, summary_dict)
repo.get_latest_analysis_summary(ticker)
repo.get_all_latest_analysis_summary()
repo.get_analysis_history(ticker)

# Forecast runs
repo.insert_forecast_run(ticker, horizon_months, run_dict)
repo.get_latest_forecast_run(ticker, horizon_months)
repo.get_all_latest_forecast_runs(horizon_months)  # batch: 1 row per ticker

# Forecast series
repo.insert_forecast_series(ticker, horizon_months, run_date, forecast_df)
repo.get_latest_forecast_series(ticker, horizon_months)
```

---

## Dashboard TTL-cached helpers

`dashboard/callbacks/iceberg.py` wraps every `StockRepository` read in a 5-minute TTL cache. This eliminates redundant Iceberg scans when multiple callbacks or page loads share the same data within a refresh cycle.

| Helper | Iceberg source | TTL |
|--------|---------------|-----|
| `_get_registry_cached(repo)` | `stocks.registry` | 5 min |
| `_get_company_info_cached(repo)` | `stocks.company_info` | 5 min |
| `_get_forecast_runs_cached(repo, horizon)` | `stocks.forecast_runs` | 5 min |
| `_get_ohlcv_cached(repo, ticker)` | `stocks.ohlcv` | 5 min |
| `_get_forecast_cached(repo, ticker, horizon)` | `stocks.forecasts` | 5 min |
| `_get_dividends_cached(repo, ticker)` | `stocks.dividends` | 5 min |
| `_get_analysis_summary_cached(repo)` | `stocks.analysis_summary` | 5 min |

`clear_caches(ticker=None)` invalidates all caches (or per-ticker entries for OHLCV, forecasts, and dividends) so that manual card refreshes see fresh data immediately.

---

## Dashboard Insights pages

The 6 new Insights pages in the dashboard read exclusively from Iceberg:

| Page | Route | Iceberg source |
|------|-------|----------------|
| Screener | `/screener` | `stocks.analysis_summary` (fallback: flat parquet) |
| Price Targets | `/targets` | `stocks.forecast_runs` |
| Dividends | `/dividends` | `stocks.dividends` |
| Risk Metrics | `/risk` | `stocks.analysis_summary` |
| Sectors | `/sectors` | `stocks.company_info` + `stocks.analysis_summary` |
| Correlation | `/correlation` | `stocks.ohlcv` (fallback: flat parquet) |

---

## Known data quirks

### `adj_close` is all NaN in `stocks.ohlcv`

yfinance >= 1.2 dropped the `Adj Close` column from `yf.download()`. When `insert_ohlcv()` writes to Iceberg, the schema includes `adj_close` but the values are `None` (NaN) for all rows. All consumers (`_get_ohlcv_cached`, `_prepare_data_for_prophet`, `forecast_cbs.py`) check `notna().any()` before using `Adj Close` and fall back to `Close` when the column is empty.

---

## PyIceberg quirks

- `table.append()` requires a `pa.Table` — not a `RecordBatch`
- `TimestampType` maps to `pa.timestamp("us")` — pass **naive** UTC datetimes (no `tzinfo`)
- There is no native `UPDATE` — upserts use the copy-on-write pattern: read full table → mutate pandas DataFrame → `table.overwrite()`
- Catalog config is in `.pyiceberg.yaml` at the project root (gitignored; see `.pyiceberg.yaml.example`)
