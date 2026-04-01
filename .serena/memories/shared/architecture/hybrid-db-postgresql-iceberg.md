# Hybrid DB Architecture: PostgreSQL + Iceberg

## Split rationale
Tables with row-level CRUD (updates, deletes, upserts) → PostgreSQL.
Tables with append-only or scoped-delete-and-reappend → Iceberg.

## PostgreSQL (5 tables — OLTP)

| Table | PK | Pattern | ORM Model |
|-------|-----|---------|-----------|
| `users` | `user_id` (UUID) | CRUD | `backend/db/models/user.py` |
| `user_tickers` | `(user_id, ticker)` | Insert + delete | `backend/db/models/user_ticker.py` |
| `payment_transactions` | `transaction_id` | Insert + update | `backend/db/models/payment.py` |
| `stock_registry` | `ticker` | Upsert | `backend/db/models/registry.py` |
| `scheduled_jobs` | `job_id` | Upsert | `backend/db/models/scheduler.py` |

### Access patterns
- Auth repos: `auth/repo/user_reads.py`, `user_writes.py`, `oauth.py`,
  `ticker_repo.py`, `payment_repo.py` — all async SQLAlchemy
- `UserRepository` facade in `auth/repo/repository.py` — accepts
  `session_factory`, creates per-call sessions via `_session_scope()`
- Registry/scheduler: `backend/db/pg_stocks.py` — async functions,
  wrapped by sync methods in `StockRepository._run_pg()` for threads

### Engine config
- `backend/db/engine.py`: `pool_size=5`, `max_overflow=10`,
  `pool_pre_ping=True` (required for uvicorn reload)
- `DATABASE_URL`: `postgresql+asyncpg://app:${PG_PASSWORD}@postgres:5432/aiagent`
- Alembic migrations: `backend/db/migrations/`

## Iceberg (14 tables — OLAP)

### Append-only (9)
audit_log, usage_history, company_info, dividends, analysis_summary,
forecast_runs, llm_pricing, llm_usage, scheduler_runs

### Scoped delete-and-reappend (5)
ohlcv, technical_indicators, forecasts, quarterly_results,
portfolio_transactions

### Access
- `stocks/repository.py` — StockRepository reads/writes via PyIceberg
- `backend/db/duckdb_engine.py` — DuckDB foundation for future reads
- Catalog: `load_catalog("local")` → SQLite-backed `.pyiceberg.yaml`

## Data migration
One-time script: `scripts/migrate_iceberg_to_pg.py`
- Reads Iceberg → sanitizes NaT/NaN → bulk inserts to PG
- FK-ordered: users → user_tickers → payment_transactions → registry → jobs
- Idempotent (DELETE before INSERT)
