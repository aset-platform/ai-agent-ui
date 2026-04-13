# CLAUDE.md — AI Agent UI

> Slim project instructions for Claude Code. Detailed knowledge
> lives in Serena shared memories — run `list_memories` to browse.

---

## Session Startup (do these on every new session)

1. **Activate Serena** — `activate_project ai-agent-ui`
   (required before `read_memory` / `write_memory` / `list_memories`)
2. **Check Ollama** — SessionStart hook reports model status;
   run `ollama-profile coding` if delegation needed
3. **Superpowers skills** — always check for applicable skill
   before starting work (brainstorming, TDD, executing-plans)
4. **SuperClaude commands** — use `/sc:` prefix for git, build,
   test, analyze, implement, troubleshoot workflows

### Available MCP tools (verify with `list_memories`)

| Server | Purpose | Activation |
|--------|---------|------------|
| Serena | Code analysis, shared memories, symbol navigation | `activate_project` |
| Ollama | Local LLM delegation (Qwen for code gen) | auto (SessionStart hook) |
| Context7 | Library/framework docs lookup | auto |
| Playwright | Browser automation, E2E testing | auto |
| Chrome DevTools | Page inspection, performance, screenshots | auto |
| Atlassian (Jira) | Sprint/ticket management | auto |
| Sequential Thinking | Multi-step reasoning | auto |

---

## Project Overview

Fullstack agentic chat app with stock analysis and Prophet forecasting.
Native portfolio dashboard with TradingView lightweight-charts +
react-plotly.js. Memory-augmented chat with pgvector semantic
retrieval. Dual payment gateways (Razorpay INR + Stripe USD).
All pages fully migrated from Dash to Next.js.

| Service | Port | Entry point | Stack |
|---------|------|-------------|-------|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x, SQLAlchemy 2.0 async |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19, lightweight-charts |
| PostgreSQL | 5432 | Docker | pgvector/pgvector:pg16 (OLTP: 18 tables + pgvector) |
| Redis | 6379 | Docker | Redis 7 Alpine |
| Docs | 8000 | Docker | MkDocs Material 9 (squidfunk) |
| Alembic | — | `backend/db/migrations/` | Schema migrations for PostgreSQL |

```bash
# run.sh — Docker Compose wrapper (preferred)
./run.sh start                              # all services via docker compose
./run.sh stop                               # docker compose down
./run.sh restart                            # stop + start all
./run.sh restart frontend                   # restart only frontend
./run.sh restart backend                    # restart only backend
./run.sh stop redis                         # stop only redis
./run.sh rebuild frontend                   # rebuild image + restart (after code changes)
./run.sh rebuild backend                    # rebuild image + restart
./run.sh build                              # build all images (no restart)
./run.sh status                             # service health table
./run.sh logs backend                       # Docker service logs
./run.sh logs backend -f                    # follow Docker logs
./run.sh logs --errors                      # errors across all services
./run.sh doctor                             # diagnostic checks

# Direct Docker Compose (also works)
docker compose up -d                        # all services
docker compose build backend               # rebuild after requirements.txt changes
docker compose ps                           # health check
docker compose down                         # stop all

# Ollama (host-native, not containerized)
ollama-profile coding                       # load Qwen for code gen
ollama-profile reasoning                    # load GPT-OSS 20B
ollama-profile embedding                    # load nomic-embed-text (memory vectors)
ollama-profile status                       # check loaded model
```

**Key dirs**: `backend/` (agents, tools, config), `backend/pipeline/` (stock data pipeline, 19 CLI commands), `backend/jobs/` (scheduler executors, pipeline chaining, gap filler), `backend/db/` (ORM models, async engine, Alembic migrations, DuckDB layer), `auth/` (JWT + RBAC + OAuth PKCE), `stocks/` (Iceberg — 12 OLAP tables), `frontend/` (SPA), `dashboard/` (legacy Dash callbacks — all pages migrated to Next.js), `hooks/` (pre-commit, pre-push).

**Docker files**: `Dockerfile.backend`, `Dockerfile.frontend`,
`Dockerfile.docs`, `docker-compose.yml`,
`docker-compose.override.yml` (dev hot-reload),
`.env.example` (template), `.env` (secrets, gitignored).

**Config**: `pyproject.toml` + `.flake8` (79 chars), `frontend/eslint.config.mjs`.

**Data**: `~/.ai-agent-ui/` (override: `AI_AGENT_UI_HOME`). Paths in `backend/paths.py`.

---

## LLM Cascade Architecture

`FallbackLLM` in `backend/llm_fallback.py` — N-tier cascade:

| Tier | Provider | Model | When |
|------|----------|-------|------|
| 1-6 | Groq (free) | Round-robin pools: [70b, kimi-k2, qwen3-32b] → [gpt-oss-120b, gpt-oss-20b] → scout-17b | All (chat + batch) |
| N-1 | Ollama (local) | gpt-oss:20b | Fallback (`ollama_first=False` everywhere) |
| N | Anthropic (paid) | claude-sonnet-4-6 | Final fallback |

- `OllamaManager` (`backend/ollama_manager.py`): TTL-cached health probe,
  load/unload profiles. If Ollama unavailable, cascade skips it.
- `RoundRobinPool` (`backend/token_budget.py`): per-pool atomic
  counter, `get_token_budget()` singleton seeded from Iceberg.
  `ROUND_ROBIN_ENABLED=false` reverts to legacy sequential.
- Admin API: `GET/POST /v1/admin/ollama/{status,load,unload}`
- `ollama-profile` CLI: `coding`, `reasoning`, `embedding`, `unload`
- Observability: `provider="ollama"` in `ObservabilityCollector`

---

## Stock Data Pipeline

- **Module:** `backend/pipeline/` — 19 CLI commands via
  `PYTHONPATH=.:backend python -m backend.pipeline.runner`
- **Source strategy:** yfinance primary (bulk/daily),
  jugaad-data fallback (retry/correct), racing (chat).
- **Ticker format:** ALL Indian stocks use `.NS` suffix
  everywhere (Iceberg, PG, frontend, scheduler). Never
  store canonical format (no suffix) for data operations.
- **Market detection:** Import `detect_market` from
  `backend/market_utils.py`. NEVER add local suffix checks.
- **Docs:** `docs/backend/stock-pipeline.md` (usage guide),
  spec at `docs/superpowers/specs/2026-04-02-stock-pipeline-design.md`.

---

## Hybrid DB Architecture

OLTP/OLAP split — PostgreSQL for row-level CRUD, Iceberg for
append-only analytics.

### PostgreSQL tables (SQLAlchemy 2.0 async ORM)

| Table | Module | Pattern |
|-------|--------|---------|
| `auth.users` | `backend/db/models/user.py` | CRUD via `UserRepository` |
| `auth.user_tickers` | `backend/db/models/user_ticker.py` | Insert + delete |
| `auth.payment_transactions` | `backend/db/models/payment.py` | Insert + update |
| `stocks.registry` | `backend/db/pg_stocks.py` | Upsert |
| `stocks.scheduled_jobs` | `backend/db/pg_stocks.py` | Upsert (has `force` column) |
| `stocks.scheduler_runs` | `backend/db/pg_stocks.py` | Insert + row-level UPDATE |
| `stocks.recommendation_runs` | `backend/db/models/recommendation.py` | Smart Funnel run metadata |
| `stocks.recommendations` | `backend/db/models/recommendation.py` | Individual recs with data_signals JSONB |
| `stocks.recommendation_outcomes` | `backend/db/models/recommendation.py` | 30/60/90d outcome checkpoints |
| `stocks.market_indices` | `backend/db/models/market_index.py` | Single-row ticker cache (Nifty+Sensex) |
| `public.user_memories` | `backend/db/models/memory.py` | pgvector semantic memory (768-dim) |
| `public.conversation_contexts` | `backend/db/models/conversation_context.py` | Chat context persistence (cross-session) |
| `stock_master` | `backend/db/models/stock_master.py` | Pipeline universe (symbol, yf_ticker, ISIN) |
| `stock_tags` | `backend/db/models/stock_tag.py` | Temporal tagging (nifty50/100/500) |
| `ingestion_cursor` | `backend/db/models/ingestion_cursor.py` | Keyset pagination cursor |
| `ingestion_skipped` | `backend/db/models/ingestion_skipped.py` | Failed ticker log + retry |
| `pipelines` | `backend/db/models/pipeline.py` | Pipeline chain definitions |
| `pipeline_steps` | `backend/db/models/pipeline.py` | Ordered steps within pipelines |

### Iceberg tables (14 — append / scoped-delete)

`audit_log`, `usage_history`, `company_info`, `dividends`, `ohlcv`,
`analysis_summary`, `forecast_runs`, `forecasts`, `quarterly_results`,
`llm_pricing`, `llm_usage`, `portfolio_transactions`,
`piotroski_scores`, `sentiment_scores` (stocks ns)
Note: `technical_indicators` exists but is empty/unused — indicators
computed on-the-fly from OHLCV in `_analysis_shared.py`.
Note: `scheduler_runs` and `scheduled_jobs` migrated to PG
(ASETPLTFRM-301).

### Key components

- `backend/db/engine.py` — async `session_factory` (asyncpg driver,
  `pool_pre_ping=True`)
- `backend/db/models/` — 18 SQLAlchemy ORM models (FK cascade,
  JSONB, composite PK, indexes, pgvector)
- `backend/db/migrations/` — Alembic async migrations
- `auth/repo/repository.py` — `UserRepository` facade
  (session_factory injection, per-call sessions)
- `backend/db/pg_stocks.py` — registry + scheduler + pipeline PG functions
- `backend/db/models/pipeline.py` — Pipeline + PipelineStep ORM
- `backend/jobs/pipeline_executor.py` — sequential chain execution
  with skip-on-failure and resume-from-step
- `backend/db/duckdb_engine.py` — DuckDB query engine (primary
  Iceberg read path). Has in-memory metadata cache; call
  `invalidate_metadata()` after writes (auto-wired in repo).
- `scripts/migrate_iceberg_to_pg.py` — one-time data migration

---

## Hard Rules (NON-NEGOTIABLE)

These rules MUST be followed in every interaction.

### Performance (think throughput-first)

1. **Batch reads, not per-ticker loops** — single DuckDB
   `SELECT ... WHERE ticker IN (...)` before parallel loops.
   Pre-load into dict. Never N individual Iceberg reads.
2. **Bulk writes** — accumulate results in memory, write in
   1-2 Iceberg commits after the loop. Never per-ticker
   `_append_rows` inside workers.
3. **Iceberg = append-only analytics only** — row-level
   `update` does full table scan + overwrite (~9s). Use
   PostgreSQL for mutable state (status, counters, timestamps).
4. **NullPool for sync→async PG** — `_pg_session()` uses
   `NullPool`. Never thread-local or per-call pooled engines.
   See `shared/debugging/pg-nullpool-sync-async-bridge`.
5. **No nested parallelism** — outer `ThreadPoolExecutor`
   workers must NOT spawn inner `ProcessPoolExecutor`.
   Prophet CV: `parallel=None`. `workers = cpu_count // 2`.
6. **Cache scope-level data** — VIX, index, macro regressors
   identical across tickers in same scope. Cache with TTL.
   Only per-ticker data (sentiment) fetched individually.
7. **Throttle expensive I/O** — if an update costs >100ms,
   batch into finalize or use time-based intervals.
8. **No OHLCV full-table scans** — 1.4M rows. Use
   `ROW_NUMBER() OVER (PARTITION BY ticker ...)` or
   `WHERE ticker IN (...)` with date filters.

### Code Style

9. **Line length 79 chars** — black, isort, flake8 aligned.
10. **No bare `print()`** — use `logging.getLogger(__name__)`.
11. **`X | None`** not `Optional[X]` (Python 3.12, PEP 604).
12. **No module-level mutable globals** — all state in class
    instances. Exception: `_logger` is OK.
13. **No bare `except:`** — always `except Exception` or
    specific type.
14. **`apiFetch`** not `fetch` — auto-refreshes JWT.
15. **`<Image />`** not `<img>` — enforced by ESLint.
16. **Patch at SOURCE module** — not the importing module.

### Data & Writes

17. **Iceberg writes MUST NOT be silenced** — let errors
    propagate. Never swallow append/overwrite exceptions.
18. **Scoped deletes** — delete by `In("ticker", batch)` not
    `EqualTo("score_date")`. Prevents cross-market overwrites.
19. **Ticker format** — ALL Indian stocks `.NS` suffix
    everywhere. Import `detect_market` from `market_utils.py`.

### Process & Git

20. **Branch off `dev`** — NEVER push to `dev`, `qa`,
    `release`, `main`.
21. **Co-Authored-By in commits** — always use:
    `Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>`
22. **Update `PROGRESS.md`** after every session (dated entry).
23. **Commit Serena memories** — always `git add .serena/`
    before final push. Shared memories are checked into git.
24. **Test-after-feature** — happy path + 1 error path minimum.
25. **Jira story points** — update BOTH `customfield_10016`
    AND `customfield_10036`.

### Infra & Config

26. **`NEXT_PUBLIC_BACKEND_URL` = `http://localhost:8181`** —
    never `127.0.0.1`. Hostname mismatch breaks cookies.
27. **No `@traceable` on `FallbackLLM.invoke()`** — breaks
    LangChain tool call parsing.
28. **Ollama for experiments only** — host-native, not
    containerized. Cascade falls back to Groq/Anthropic.
29. **LHCI can't audit authenticated routes** — use
    `npm run perf:full` (Playwright) instead.

---

## Serena Shared Memories

Run `list_memories` to browse all topics. Key categories:
`shared/architecture/`, `shared/conventions/`,
`shared/debugging/`, `shared/onboarding/`.

---

## Gotchas (learned the hard way)

### Data & Pipeline

- **yfinance pre-market flat candles**: Fetching before
  settlement returns O=H=L with NaN close. Pipelines at
  08:00 IST. Must delete NaN rows and re-fetch.
- **Forecast backtest convention**: `horizon_months=0` in
  `forecasts` table. Actual price in `lower_bound`. Batch
  executor must persist when CV runs.
- **Prophet CV from stdin**: `parallel="processes"` fails
  with `FileNotFoundError: /app/<stdin>`. Use `parallel=None`.
- **DuckDB metadata cache**: `invalidate_metadata()` called
  in `_retry_commit()`. If stale reads after write, check
  invalidation is wired.
- **Iceberg `company_info` is upsert**: deletes existing
  row for ticker before appending. One row per ticker.
- **Iceberg concurrent writes**: SQLite catalog conflicts
  under Semaphore(10). Fundamentals job uses Semaphore(1).
- **Iceberg flush window**: `ObservabilityCollector` flushes
  every 30s. Restarts lose unflushed events. Seed on startup.
- **yfinance sector names**: "Technology" not "IT",
  "Financial Services" not "Financials".
- **jugaad-data timeout**: No built-in timeout. NseSource
  wraps in `asyncio.wait_for(timeout=60.0)`.

### Database & PG

- **`_pg_session()` NullPool**: ~2-5ms per call. Don't use
  in hot loops — batch via DuckDB or bulk PG writes.
- **asyncpg `pool_pre_ping=True`**: Required in
  `engine.py` — stale connections crash on uvicorn reload.
- **Iceberg NaT/NaN → PG**: Sanitize before PG insert —
  PG rejects NaT timestamps and NaN floats.
- **Sync→async PG bridges**: See memory
  `shared/debugging/pg-nullpool-sync-async-bridge`.
- **UserMemory `extend_existing`**: Dual import causes
  "Table already defined". Fix: `extend_existing=True`.
- **FastAPI Query default**: Pass `Query()` params
  explicitly in internal calls (`ticker=None`).

### Docker & Infra

- **Docker health check**: `/v1/health` not `/health`.
- **Docker Iceberg mount**: Mount `~/.ai-agent-ui` at SAME
  path inside container. SQLite catalog stores absolute paths.
- **Ollama in Docker**: Use `host.docker.internal:11434`.
- **`.pyiceberg.yaml` in Docker**: Mount at
  `/app/.pyiceberg.yaml:ro`. Without it, reads fail silently.
- **Frontend Docker**: `node:22-slim` (glibc required).
  `HOSTNAME=0.0.0.0` to bind all interfaces.
- **Docker seed script**: Set `PYICEBERG_CATALOG__LOCAL__URI`
  before pyiceberg import. Mount `fixtures/` volume.
- **Redis cache**: After code changes: `redis-cli FLUSHALL`.

### LLM & Chat

- **TokenBudget**: Use `reserve()`/`release()` (atomic),
  not `can_afford()`/`record()` (TOCTOU race).
- **Hallucination guardrail**: `_is_hallucinated()` rejects
  3+ stock patterns with zero tool_done events.
- **Chat clarification**: Question ending with `?` bypasses
  keyword gate. See `_is_clarification()` in `guardrail.py`.
- **ReAct iteration**: `sub_agents.py` MUST pass
  `iteration=iteration+1`.
- **Groq tool call IDs**: `_sanitize_tool_ids()` cleans
  before Anthropic fallback.
- **`bind_tools` model_lookup**: Must rebuild after
  `FallbackLLM.bind_tools()`.
- **Groq TPD limits**: 6 models ~2.3M combined TPD.
  `TokenBudget` seeds from Iceberg on restart.

### Frontend

- **ChatInput `readOnly` not `disabled`**: Keeps focus.
- **Sidebar collapsed by default**: Hover flyout for submenus.
- **ECharts dark/light**: Use `MutationObserver` on `<html>`
  class, not `useTheme()`. Set `notMerge={true}` + `key`.
- **Perf script login**: Use `type()` not `fill()` — React
  `onChange` needs keystroke events.
- **`apiFetch` requires full URL**: Always use
  `${API_URL}/path` not relative `/path`. Relative paths
  hit Next.js (port 3000), not the backend (port 8181).
- **Market ticker**: `MarketTicker` in `AppHeader.tsx` center.
  NSE India + Yahoo Finance dual-source, 30s poll, PG+Redis.
  Off-hours: zero upstream calls (serves PG data).

### Testing & Config

- **Test mock dates**: Never hardcode — use
  `str(int(time.time()) - 86400)` for "yesterday".
- **`settings.local.json`**: No `()` in Bash patterns.
- **slowapi rate limiter**: `limiter.enabled = False` in
  test fixtures, not `limiter.reset()`.
- **`get_settings().debug`**: Use `getattr()` with fallback.
- **StockRepository**: Always use `_require_repo()`.
- **E2E passwords**: `admin@demo.com` / `Admin123!`.
  Run `seed_demo_data.py` if login fails.
- **Superuser insights**: `_get_user_tickers()` shows all
  registry for superusers, watchlist-only for general.
---

## Quick Reference

```bash
# Lint
black backend/ auth/ stocks/ scripts/
isort backend/ auth/ stocks/ scripts/ --profile black
flake8 backend/ auth/ stocks/ scripts/
cd frontend && npx eslint . --fix

# Test
python -m pytest tests/ -v        # all (~755 tests)
cd frontend && npx vitest run     # frontend (18 tests)
cd e2e && npm test                # E2E (~219 tests, needs live services)

# Database migrations (PostgreSQL)
PYTHONPATH=. alembic upgrade head              # apply all migrations
PYTHONPATH=. alembic revision --autogenerate -m "desc"  # new migration
PYTHONPATH=backend python scripts/migrate_iceberg_to_pg.py  # one-time data migration

# Seed (required before first E2E run)
PYTHONPATH=backend python scripts/seed_demo_data.py
# Docker seed (when running via Docker Compose)
docker compose exec backend python scripts/seed_demo_data.py

# Stock Data Pipeline
PYTHONPATH=.:backend python -m backend.pipeline.runner download    # fetch Nifty 500 CSV
PYTHONPATH=.:backend python -m backend.pipeline.runner seed --csv data/universe/nifty500.csv
PYTHONPATH=.:backend python -m backend.pipeline.runner bulk-download  # yfinance batch OHLCV
PYTHONPATH=.:backend python -m backend.pipeline.runner fill-gaps   # patch company_info gaps
PYTHONPATH=.:backend python -m backend.pipeline.runner status      # check cursor progress
PYTHONPATH=.:backend python -m backend.pipeline.runner analytics --scope india   # compute analysis summary
PYTHONPATH=.:backend python -m backend.pipeline.runner sentiment --scope india   # LLM sentiment scoring
PYTHONPATH=.:backend python -m backend.pipeline.runner forecast --scope india    # Prophet forecasts
PYTHONPATH=.:backend python -m backend.pipeline.runner screen      # Piotroski F-Score
PYTHONPATH=.:backend python -m backend.pipeline.runner refresh --scope india --force  # full pipeline chain

# Performance (run from frontend/)
npm run perf:check                # LHCI on /login (pre-PR gate)
npm run perf:audit                # Playwright 10-route quick check
npm run perf:full                 # Full 42-point surface audit
npm run analyze                   # Bundle treemap (ANALYZE=true)
```
