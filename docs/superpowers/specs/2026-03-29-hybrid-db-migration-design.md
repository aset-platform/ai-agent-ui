# Hybrid DB Migration Design Spec

**Epic:** ASETPLTFRM-225
**Date:** 2026-03-29
**Branch:** feature/sprint4
**Approach:** Big Bang (Approach A) — single cut-over, no feature flags

---

## 1. Goal

Move 5 true CRUD tables from Iceberg to PostgreSQL. Keep 14
append-only / scoped-delete tables on Iceberg. Add DuckDB as
in-process query engine for Iceberg reads.

## 2. Problem

Iceberg CRUD creates excessive snapshots and orphan files (disk
bloat). No row-level updates, no FK constraints, no unique
constraints. Copy-on-write updates require full-table rewrites.

## 3. Table Split

### PostgreSQL (5 tables — row-level CRUD)

| Table | Columns | Write pattern |
|-------|---------|---------------|
| `users` | 25 | Row-level updates (login, subscription, profile) |
| `user_tickers` | 4 | Link + unlink (insert + delete) |
| `payment_transactions` | 13 | Reconciliation, adjustments, status updates |
| `stock_registry` | 8 | Upserts per ticker (fetch metadata) |
| `scheduled_jobs` | 10 | Upsert job definitions |

### Iceberg (14 tables — append-only + scoped-delete)

**Append-only:**
`audit_log`, `usage_history`, `company_info`, `dividends`,
`analysis_summary`, `forecast_runs`, `llm_pricing`, `llm_usage`,
`scheduler_runs`

**Scoped delete-and-reappend:**
`ohlcv`, `technical_indicators`, `forecasts`, `quarterly_results`

## 4. PostgreSQL Foundation

### Package structure

```
backend/db/
├── __init__.py          # exports engine, async_session, Base
├── engine.py            # async engine + session factory
├── base.py              # declarative Base
├── models/
│   ├── __init__.py      # exports all models
│   ├── user.py          # users (25 cols)
│   ├── user_ticker.py   # user_tickers (4 cols)
│   ├── payment.py       # payment_transactions (13 cols)
│   ├── registry.py      # stock_registry (8 cols)
│   └── scheduler.py     # scheduled_jobs (10 cols)
└── migrations/
    ├── alembic.ini
    ├── env.py           # async migration env
    └── versions/
        └── 001_initial_schema.py
```

### Engine config

- `create_async_engine(DATABASE_URL)` from `config.py`
- `async_sessionmaker` with `expire_on_commit=False`
- Pool: `pool_size=5`, `max_overflow=10` (Docker `max_connections=20`)
- Single Alembic migration — all 5 tables created together

## 5. ORM Models

### `users`

- PK: `user_id` (UUID, server-default)
- Unique: `email`
- Index: `(oauth_provider, oauth_sub)` composite
- Index: `subscription_tier`
- Relationships: `tickers` (one-to-many), `payment_transactions`
  (one-to-many)
- `server_default=func.now()` for `created_at`, `updated_at`

### `user_tickers`

- PK: composite `(user_id, ticker)` — natural key, prevents dupes
- FK: `user_id -> users.user_id` (CASCADE delete)

### `payment_transactions`

- PK: `transaction_id` (UUID)
- FK: `user_id -> users.user_id`
- Index: `(gateway, gateway_event_id)` for webhook dedup
- `raw_payload` as `JSONB` — queryable for reconciliation

### `stock_registry`

- PK: `ticker` (VARCHAR, natural key)
- Index: `last_fetch_date`
- `updated_at` with `onupdate=func.now()`

### `scheduled_jobs`

- PK: `job_id` (UUID)
- Unique: `name`
- `cron_days`, `cron_dates` as `ARRAY(String)` — PostgreSQL native
- `enabled` Boolean, default `True`

## 6. Auth Repo Rewrite

### Approach

Keep `IcebergUserRepository` facade — same class name, same method
signatures. Replace internals from Iceberg scan to async SQLAlchemy.
Rename to `UserRepository` in ASETPLTFRM-236 (cleanup story).

### File changes

| File | Action |
|------|--------|
| `auth/repo/repository.py` | Rewrite internals, keep interface |
| `auth/repo/user_reads.py` | `select().where()` instead of scan |
| `auth/repo/user_writes.py` | `session.add/merge/delete` instead of overwrite |
| `auth/repo/oauth.py` | Indexed query on `(oauth_provider, oauth_sub)` |
| `auth/repo/catalog.py` | DELETE — no longer needed |
| `auth/repo/schemas.py` | DELETE — ORM models replace PyArrow schemas |

### New modules

| File | Purpose |
|------|---------|
| `auth/repo/ticker_repo.py` | `link()`, `unlink()`, `get_user_tickers()` |
| `auth/repo/payment_repo.py` | `record_transaction()`, `update_status()`, `get_by_user()` |

### Method mapping

| Method | Before | After |
|--------|--------|-------|
| `get_by_email()` | Full-table scan + filter | `select(User).where(User.email == email)` |
| `create()` | Append row to DataFrame, overwrite table | `session.add(User(...))` |
| `update()` | Read all, mutate, overwrite | `session.merge(user)` |
| `delete()` | Read all, set is_active=False, overwrite | `UPDATE ... SET is_active=False` |

### Session injection

Repository takes `async_sessionmaker` in `__init__`, wired at
startup in `backend/bootstrap.py`.

### Callers untouched

`auth/endpoints/`, `backend/routes.py` call the same method
signatures — no changes needed.

## 7. Stocks Repository Rewrite

### Scope

Only `registry` and `scheduled_jobs` methods change in
`stocks/repository.py`. All 12 Iceberg table methods untouched.

### Registry methods

| Method | Before | After |
|--------|--------|-------|
| `get_registry()` | Iceberg scan + filter | `select(StockRegistry).where(...)` |
| `upsert_registry()` | Read all, update DF, overwrite | `INSERT ... ON CONFLICT (ticker) DO UPDATE` |
| `delete_registry()` | Read all, filter out, overwrite | `session.delete()` |

### Scheduler methods

| Method | Before | After |
|--------|--------|-------|
| `get_all_jobs()` | Iceberg scan | `select(ScheduledJob).where(enabled == True)` |
| `upsert_job()` | Read all, update DF, overwrite | `INSERT ... ON CONFLICT (job_id) DO UPDATE` |
| `toggle_job()` | Read all, flip flag, overwrite | `UPDATE ... SET enabled = ?` |

### Constructor

`StockRepository.__init__` takes both `async_sessionmaker` (PG)
and Iceberg catalog (remaining tables). No dual interface — big
bang swap.

### Untouched

- `cached_repository.py` Redis layer
- `_require_repo()` in `tools/_stock_shared.py`
- All 12 Iceberg table methods

## 8. Data Migration

### Script

`scripts/migrate_iceberg_to_pg.py`

### Flow

1. Run `alembic upgrade head` (create tables)
2. Load Iceberg catalog
3. For each table (FK order):
   a. Scan full Iceberg table to PyArrow
   b. Convert to list of dicts
   c. Bulk insert into PostgreSQL (`executemany`)
   d. Verify row counts match
4. Print summary report

### Table order (FK dependencies)

1. `users` — no FK deps
2. `user_tickers` — FK to users
3. `payment_transactions` — FK to users
4. `stock_registry` — no FK deps
5. `scheduled_jobs` — no FK deps

### Validation

- Row count comparison per table
- Spot-check 5 random records per table
- Verify unique constraints hold (email, ticker PK, job name)

## 9. Cut-over Sequence

1. Stop backend: `docker compose stop backend`
2. Run `alembic upgrade head`
3. Run `migrate_iceberg_to_pg.py`
4. Deploy new code (Iceberg -> PG code paths)
5. Start backend: `docker compose up -d backend`
6. Smoke test: login, watchlist, scheduler, payments
7. If OK -> cleanup in ASETPLTFRM-236 (drop Iceberg tables later)
8. If broken -> revert code, Iceberg data still intact

### Rollback safety

Iceberg tables are NOT dropped until ASETPLTFRM-236 (separate
story). During cut-over both data stores have the data. Reverting
the code deploy restores the old Iceberg path.

## 10. Testing Strategy

### Fixtures

- `conftest.py` adds `pg_session` fixture — creates all tables in
  test PostgreSQL, yields session, rolls back after each test
- Existing Iceberg fixtures remain for 14 untouched tables
- No dual-path testing — big bang means only new path

### Existing tests (~620)

- Tests mocking `IcebergUserRepository` — update mock targets,
  same method signatures
- Tests using `StockRepository` registry/scheduler — update to
  async session fixtures

### New tests

- ORM constraint validation: unique email, FK cascade, composite
  PK on user_tickers
- Data migration script: run against test Iceberg data, verify PG
- Repository integration: CRUD through UserRepository, ticker_repo,
  payment_repo

### E2E tests (~219)

- No changes — they hit HTTP endpoints, not repositories
- Run full suite after cut-over

### Coverage rule (CLAUDE.md #12)

- Happy path + 1 error path per migrated method
- FK constraint violation test
- Upsert conflict resolution test

## 11. Jira Stories

| # | Key | Summary | SP |
|---|-----|---------|----|
| 1 | ASETPLTFRM-231 | PostgreSQL + SQLAlchemy 2.0 async + Alembic scaffold | 3 |
| 2 | ASETPLTFRM-232 | Migrate auth.users — ORM model + auth repo rewrite | 5 |
| 3 | ASETPLTFRM-233 | Migrate user_tickers + payment_transactions | 5 |
| 4 | ASETPLTFRM-234 | Migrate stock_registry + scheduled_jobs | 3 |
| 5 | ASETPLTFRM-235 | DuckDB query layer for Iceberg reads | 5 |
| 6 | ASETPLTFRM-236 | Cleanup — drop Iceberg tables, dead code, tests | 3 |
| - | ASETPLTFRM-237 | [DUPLICATE] Merged into 236 | 0 |

**Total: 24 SP**

## 12. Dependencies

```
231 (scaffold)
 ├── 232 (auth.users)
 │    └── 233 (user_tickers + payments) — FK to users
 ├── 234 (registry + scheduler) — independent of auth
 └── 235 (DuckDB) — independent, can parallel with 232-234
236 (cleanup) — after 232, 233, 234 all done
```
