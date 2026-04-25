---
name: db-table-inventory
description: Complete inventory — 18 PostgreSQL OLTP tables + 16 Iceberg OLAP tables. Source of truth for "what lives where".
type: architecture
---

# DB table inventory (PG OLTP + Iceberg OLAP)

Hybrid architecture. Mutable state → PG (asyncpg + SQLAlchemy 2.0).
Append-only analytics → Iceberg (PyIceberg writes, DuckDB reads).
Decision rule: any row that needs UPDATE goes to PG; per-event /
per-day immutable facts go to Iceberg.

## PostgreSQL — 18 OLTP tables

### Auth schema

| Table | ORM | Pattern |
|---|---|---|
| `auth.users` | `db/models/user.py` | CRUD via `UserRepository` |
| `auth.user_tickers` | `db/models/user_ticker.py` | Insert + delete |
| `auth.payment_transactions` | `db/models/payment.py` | Insert + update |
| `user_llm_keys` | `db/models/byo_key.py` | BYO Fernet-encrypted |

### Stocks schema

| Table | ORM | Pattern / notes |
|---|---|---|
| `stocks.registry` | `db/pg_stocks.py` | Upsert; `ticker_type` ∈ {stock, etf, index, commodity} |
| `stocks.recommendation_runs` | `db/models/recommendation.py` | Smart Funnel meta. `run_type` ∈ {manual, chat, scheduled, admin, admin_test}. `admin_test` hidden from user reads via `exclude_test=True` |
| `stocks.recommendations` | `db/models/recommendation.py` | `data_signals` JSONB. `acted_on_date` auto-set by portfolio-mutation hook |
| `stocks.recommendation_outcomes` | `db/models/recommendation.py` | 30/60/90 day price-check checkpoints |
| `stocks.market_indices` | `db/models/market_index.py` | Single-row Nifty + Sensex cache |

### Public schema (NOT stocks — common confusion)

| Table | ORM | Notes |
|---|---|---|
| `public.scheduled_jobs` | `db/pg_stocks.py` | Upsert (`force` field). Schema is `public`, NOT `stocks` |
| `public.scheduler_runs` | `db/pg_stocks.py` | Insert + UPDATE. Schema is `public`. **Migrated from Iceberg** for 640× speedup (9s → 14ms per update) |
| `public.user_memories` | `db/models/memory.py` | pgvector 768-dim |
| `public.conversation_contexts` | `db/models/conversation_context.py` | Cross-session chat ctx |
| `public.pipelines` | `db/models/pipeline.py` | Pipeline definitions |
| `public.pipeline_steps` | `db/models/pipeline.py` | Steps per pipeline |
| `public.sentiment_dormant` | `db/models/sentiment_dormant.py` | Per-ticker headline-fetch dormancy. Capped exponential cooldown 2/4/8/16/30d. 5% probe re-test by oldest `last_checked_at` |
| `stock_master` | `db/models/stock_master.py` | symbol, yf_ticker, ISIN |
| `stock_tags` | `db/models/stock_tag.py` | nifty50/100/500 temporal |
| `ingestion_cursor` | `db/models/` | Keyset pagination |
| `ingestion_skipped` | `db/models/` | Retry log |

## Iceberg — 16 active OLAP tables

Catalog: SQLite at `~/.ai-agent-ui/data/iceberg/catalog.db`. Warehouse:
`~/.ai-agent-ui/data/iceberg/warehouse/`. Daily compaction via the
`iceberg_maintenance` step in both India + USA pipelines.

### Hot (compacted daily)

| Table | Rows | Pattern |
|---|---|---|
| `stocks.ohlcv` | ~1.5M | Per-ticker per-day. NaN-replaceable upsert pattern. Smart-delta freshness via `date_range_end` |
| `stocks.sentiment_scores` | ~810 active tickers × N days | `source` ∈ {finbert, llm, market_fallback, none}. Source-aware delete (`In("source", ["market_fallback", "none"])`) prevents force-runs from clobbering finbert/llm rows |
| `stocks.company_info` | ~830 (one row/ticker) | Upsert deletes existing ticker row before append. Snapshot bloat managed via `overwrite()` |
| `stocks.analysis_summary` | ~830 | Daily TA aggregate per ticker. `rsi_14` extracted via regex from `rsi_signal` (NOT a standalone column) |

### Production (append + occasional compact)

| Table | Notes |
|---|---|
| `stocks.dividends` | Quarterly/annual events. 90-day freshness window |
| `stocks.forecast_runs` | 27 cols incl. regime, transform, regressors, completeness. Dedup by `computed_at` (UTC ts) NOT `run_date` |
| `stocks.forecasts` | Per-day rows under each forecast_run |
| `stocks.quarterly_results` | Per-period income/balance/cashflow |
| `stocks.piotroski_scores` | F-Score per ticker per snapshot |
| `stocks.llm_pricing` | Per-model $/token ledger |
| `stocks.llm_usage` | Per-request usage. `key_source` column ∈ {platform, user, NULL} — null = platform (legacy) |
| `stocks.portfolio_transactions` | User add/edit/delete event ledger |

### Logging / audit

| Table | Notes |
|---|---|
| `stocks.chat_audit_log` | Per-chat-turn record |
| `stocks.query_log` | LangChain query trace |
| `stocks.data_gaps` | Detected gaps in OHLCV / fundamentals |

### Catalog-shell (also lives in PG)

| Table | Notes |
|---|---|
| `stocks.registry` | Iceberg copy of PG `stocks.registry`. Read-side performance for screener / DuckDB joins |

## Recently dropped (2026-04-25)

`stocks.scheduler_runs`, `stocks.scheduled_jobs`,
`stocks.technical_indicators` — see commit `c0447dc`. PG
`public.scheduler_runs/scheduled_jobs` are the canonical sources;
`backend/insights/_analysis_indicators.py` computes TA on demand.
86 orphan metadata files for technical_indicators removed.
**`DEAD_TABLES` in `iceberg_maintenance.py` is now empty.**

## Decision rule — PG vs Iceberg

Use PG when ANY of:

- Row needs UPDATE (mutable state)
- Foreign-key relationships
- Sub-second single-row reads from API endpoints
- Transactional consistency across tables
- pgvector / full-text search

Use Iceberg when ALL of:

- Append-only or per-(key, date) replace
- Time-series or per-event facts
- Bulk reads via DuckDB acceptable (>50ms is fine)
- Schema evolution may add columns over time

## Schema migrations

- PG: Alembic in `backend/db/migrations/versions/`. Run via
  `PYTHONPATH=. alembic upgrade head`. Never delete migration files;
  always create a new revision.
- Iceberg: `tbl.update_schema().add_column(...)` executed via
  `evolve_*` functions in `stocks/create_tables.py`. After every
  evolution: backend container restart + Redis FLUSHALL (see
  `shared/conventions/backend-restart-triggers`).

## Read patterns

- PG hot path: `_pg_session()` with NullPool engine — see
  `shared/debugging/pg-nullpool-sync-async-bridge`.
- Iceberg hot path: DuckDB via `query_iceberg_df()` /
  `query_iceberg_multi()`. Always `invalidate_metadata()` after
  any write before the next read.
- Iceberg under concurrent writes: prefer `tbl.refresh().scan(filter)`
  over DuckDB filesystem-glob to avoid latest-snapshot race.

## Related

- `shared/architecture/hybrid-db-postgresql-iceberg` — original
  decision rationale
- `shared/architecture/scheduler-runs-pg-migration` — the migration
  that proved Iceberg is wrong for mutable state
- `shared/conventions/iceberg-freshness-checks` — chat-tool
  freshness windows per table
- `shared/architecture/iceberg-maintenance` — compaction + cleanup
