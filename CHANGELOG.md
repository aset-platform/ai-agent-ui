# CHANGELOG

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — feature/sprint4

### Fixed — 2026-03-31: Stale Prices, Intent Routing, Anti-Hallucination (ASETPLTFRM-257, 259, 260)

**Stale data fix (ASETPLTFRM-257)**

- Removed file-based cache from `_analysis_shared.py` and
  `_forecast_shared.py`; added `_is_ohlcv_stale()` + yfinance
  auto-fetch fallback to `_load_ohlcv()`.
- Iceberg freshness gate now compares analysis_date vs latest OHLCV date.
- Forecast NaN accuracy guard (`math.isnan` check).
- Currency defaults to INR for `.NS`/`.BO` tickers (was USD).

**Intent-aware routing (ASETPLTFRM-257)**

- Extracted `best_intent()` / `score_intents()` from `router_node.py`.
- Guardrail follow-up: keyword check before LLM classifier; only
  reuse agent on same intent.
- `_merge_tickers()` + `_build_clarification()` for ambiguous switches.

**Anti-hallucination (ASETPLTFRM-257)**

- Query cache skips responses without tool_events.
- Hallucination guardrail rejects data-heavy responses with zero
  tool calls.
- Stock analyst: mandatory `get_ticker_news` +
  `get_analyst_recommendations` in Step 3.
- Tool call ID sanitization for Anthropic cascade
  (`_sanitize_tool_ids` in `llm_fallback.py`).

### Added — 2026-03-31: Interactive Stock Discovery (ASETPLTFRM-259)

- `suggest_sector_stocks` tool with Iceberg scan + popular fallback
  (8 sectors, ~40 stocks).
- `get_stocks_by_sector()` on `StockRepository`.
- DISCOVERY PIPELINE section in stock_analyst + portfolio agent prompts.
- Actions extraction (`<!--actions:[]-->`) in synthesis node;
  `response_actions` in graph state + WS `final` event.
- Frontend `ActionButtons` component + `sendDirect` hook.

### Changed — 2026-03-31: Token Optimization (ASETPLTFRM-260)

- Fixed iteration counter passthrough from sub_agents ReAct loop to
  `FallbackLLM` (compression was never triggered).
- Tool result truncation reduced: 2000 → 800 chars default,
  progressive 500 → 300.
- Summary-based context injection: raw history (~3K tokens) replaced
  with `ConversationContext.summary` (~100 tokens) for sub-agents.
- Intent switch sends system prompt + user query only (no prior
  agent history).

### Infrastructure — 2026-03-31

- IST timestamps in backend logs (`logging_config.py`).
- Removed `/app/.next` anonymous volume from
  `docker-compose.override.yml` (Turbopack cache corruption fix).
- "sector"/"sectors" added to `_STOCK_KEYWORDS` in `router.py`.
- `MAX_ITERATIONS` increased from 15 to 25.
- 18 new routing tests; 718-719 total passing, 2 pre-existing failures.

### Added — 2026-03-29: Hybrid DB Migration (ASETPLTFRM-225, Epic 24 SP)

**New components**

- `backend/db/engine.py` — SQLAlchemy 2.0 async engine with asyncpg
  driver; `session_factory` used across all PG repositories.
- `backend/db/models.py` — 5 ORM models: `User`, `UserTicker`,
  `PaymentTransaction`, `StockRegistry`, `ScheduledJob`.
  FK cascade, composite PK, JSONB columns, covering indexes.
- `backend/db/migrations/` — Alembic async migration environment;
  initial schema migration applied to Docker PostgreSQL.
- `backend/db/user_repository.py` — `UserRepository` facade
  replacing `IcebergUserRepository` for all OLTP auth operations.
- `backend/db/pg_stocks.py` — async upsert functions for
  `stocks.registry` and `stocks.scheduled_jobs`.
- `backend/db/duckdb_engine.py` — DuckDB query layer foundation
  for running analytical queries directly against Iceberg parquet.
- `scripts/migrate_iceberg_to_pg.py` — one-time migration script
  that moves 5 tables from Iceberg → PostgreSQL.

**Migrated to PostgreSQL (OLTP)**

- `auth.users`, `auth.user_tickers`, `auth.payment_transactions`
- `stocks.registry`, `stocks.scheduled_jobs`

**Stays on Iceberg (OLAP — 14 tables)**

- All analytics and append-only tables: `ohlcv`, `company_info`,
  `dividends`, `technical_indicators`, `analysis_summary`,
  `forecast_runs`, `forecasts`, `quarterly_results`, `llm_pricing`,
  `llm_usage`, `scheduler_runs`, `audit_log`, `usage_history`,
  `portfolio_transactions`

**Auth async conversion**

- 37 functions across 11 files converted to `async def`.
- All auth endpoints, OAuth handlers, and callers updated.
- `IcebergUserRepository` retained as façade; internally delegates
  to `UserRepository` (SQLAlchemy) for OLTP tables.

**Health check**

- `GET /v1/health` now includes `postgresql` connectivity status.

**Tests**

- 30 new tests added (all passing).
- 652/666 existing tests passing; 14 failures are pre-existing
  and unrelated to the migration.

---

## [0.5.0] — 2026-03-29: Ollama + Containerization (Sprint 4, 43 SP)

### Added

- Ollama local LLM as Tier 0 in `FallbackLLM` cascade
  (`backend/ollama_manager.py`, `backend/llm_fallback.py`)
- `ollama-profile` CLI for switching between Qwen (coding) and
  GPT-OSS 20B (reasoning) profiles
- Docker containerization: `Dockerfile.backend`,
  `Dockerfile.frontend`, `docker-compose.yml`,
  `docker-compose.override.yml`, `.env.example`
- Chat UX improvements: auto-scroll, input focus, markdown
  formatting, tool calls header
- Admin REST endpoints: `GET/POST /v1/admin/ollama/{status,load,unload}`

### Fixed

- Billing redirect session loss (SameSite cookie on payment return)
- Payment success handler no longer blocks on token refresh
- Forecast chart null price crash on crosshair hover

---

## [0.4.0] — 2026-03-28: Scheduler Overhaul (Sprint 4)

### Added

- Scheduler catch-up on startup (ASETPLTFRM-216)
- Scheduler timezone fix — removed erroneous IST→UTC conversion
  (ASETPLTFRM-217)
- Scheduler edit jobs UI (ASETPLTFRM-218)
- Day-of-month scheduling support (ASETPLTFRM-219)
- Admin Transactions bug fix (ASETPLTFRM-220)
- Auto-create Iceberg tables on startup (ASETPLTFRM-221)

---

## [0.3.0] — 2026-03-16: Dashboard Overhaul + Dash→Next.js Migration

### Added

- Native Next.js portfolio dashboard (TradingView lightweight-charts
  + react-plotly.js); Dash iframe removed from main routes
- Dual payment gateways: Razorpay (INR) + Stripe (USD)
- Per-ticker refresh, Redis cache layer, subscription billing
- Full RBAC + OAuth PKCE auth flows

---

## [0.2.0] — 2026-03-09: Agentic Framework + LangGraph

### Added

- LangGraph supervisor with Portfolio, Stock Analyst, Forecaster,
  and Research sub-agents
- N-tier Groq → Anthropic LLM cascade with token budget
- LangSmith observability integration

---

## [0.1.0] — Initial release

- FastAPI backend with basic LangChain agentic loop
- Next.js frontend with chat panel
- Apache Iceberg data layer for all stock + auth data
- JWT authentication with Redis deny-list
