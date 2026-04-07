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
| PostgreSQL | 5432 | Docker | pgvector/pgvector:pg16 (OLTP: 10 tables + pgvector) |
| Redis | 6379 | Docker | Redis 7 Alpine |
| Docs | 8000 | Docker | MkDocs Material 9 (squidfunk) |
| Alembic | — | `backend/db/migrations/` | Schema migrations for PostgreSQL |

```bash
# Docker (preferred — mirrors production)
docker compose up -d                        # all services
docker compose build backend               # rebuild after requirements.txt changes
docker compose ps                           # health check
docker compose logs -f backend              # tail logs (IST timestamps)
docker compose logs backend | grep 429      # check Groq rate limits
docker compose down                         # stop all

# Native (legacy, still works)
./run.sh start                              # all services
source ~/.ai-agent-ui/venv/bin/activate      # Python virtualenv

# Ollama (host-native, not containerized)
ollama-profile coding                       # load Qwen for code gen
ollama-profile reasoning                    # load GPT-OSS 20B
ollama-profile embedding                    # load nomic-embed-text (memory vectors)
ollama-profile status                       # check loaded model
```

**Key dirs**: `backend/` (agents, tools, config), `backend/pipeline/` (stock data pipeline, 12 CLI commands), `backend/db/` (ORM models, async engine, Alembic migrations, DuckDB layer), `auth/` (JWT + RBAC + OAuth PKCE), `stocks/` (Iceberg — 14 OLAP tables), `frontend/` (SPA), `dashboard/` (Dash callbacks, imported by backend), `hooks/` (pre-commit, pre-push).

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
| 0 | Ollama (local) | gpt-oss:20b | Sentiment/batch (`ollama_first=True`) |
| 1-6 | Groq (free) | Round-robin pools: [70b, kimi-k2, qwen3-32b] → [gpt-oss-120b, gpt-oss-20b] → scout-17b | Interactive chat |
| N-1 | Ollama (local) | gpt-oss:20b | Chat fallback (`ollama_first=False`) |
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

- **Module:** `backend/pipeline/` — 12 CLI commands via
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
| `stocks.scheduled_jobs` | `backend/db/pg_stocks.py` | Upsert |
| `public.user_memories` | `backend/db/models/memory.py` | pgvector semantic memory (768-dim) |
| `stock_master` | `backend/db/models/stock_master.py` | Pipeline universe (symbol, yf_ticker, ISIN) |
| `stock_tags` | `backend/db/models/stock_tag.py` | Temporal tagging (nifty50/100/500) |
| `ingestion_cursor` | `backend/db/models/ingestion_cursor.py` | Keyset pagination cursor |
| `ingestion_skipped` | `backend/db/models/ingestion_skipped.py` | Failed ticker log + retry |

### Iceberg tables (14 — append / scoped-delete)

`audit_log`, `usage_history`, `company_info`, `dividends`, `ohlcv`,
`technical_indicators`, `analysis_summary`, `forecast_runs`,
`forecasts`, `quarterly_results`, `llm_pricing`, `llm_usage`,
`scheduler_runs` (stocks ns) + portfolio_transactions (stocks ns)

### Key components

- `backend/db/engine.py` — async `session_factory` (asyncpg driver,
  `pool_pre_ping=True`)
- `backend/db/models/` — 10 SQLAlchemy ORM models (FK cascade,
  JSONB, composite PK, indexes, pgvector)
- `backend/db/migrations/` — Alembic async migrations
- `auth/repo/repository.py` — `UserRepository` facade
  (session_factory injection, per-call sessions)
- `backend/db/pg_stocks.py` — registry + scheduler PG functions
- `backend/db/duckdb_engine.py` — DuckDB query layer foundation
  (reads Iceberg parquet directly for analytics)
- `scripts/migrate_iceberg_to_pg.py` — one-time data migration

---

## Hard Rules (NON-NEGOTIABLE)

These rules MUST be followed in every interaction:

1. **Line length 79 chars** — black, isort, flake8 aligned.
2. **No bare `print()`** — use `logging.getLogger(__name__)`.
3. **`X | None`** not `Optional[X]` (Python 3.12, PEP 604).
4. **No module-level mutable globals** — all state in class instances.
   Exception: `_logger = logging.getLogger(__name__)` is OK.
5. **No bare `except:`** — always `except Exception` or specific.
6. **Branch off `dev`** — NEVER push to `dev`, `qa`, `release`, `main`.
7. **`apiFetch`** not `fetch` — auto-refreshes JWT.
8. **`<Image />`** not `<img>` — enforced by ESLint.
9. **Patch at SOURCE module** — not the importing module.
10. **Iceberg writes MUST NOT be silenced** — let errors propagate.
11. **Update `PROGRESS.md`** after every session (dated entry).
12. **Test-after-feature** — happy path + 1 error path minimum.
13. **Co-Authored-By in commits** — always use:
    `Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>`
14. **No `@traceable` on `FallbackLLM.invoke()`** — breaks LangChain
    tool call parsing. Inner ChatGroq/ChatAnthropic are auto-traced.
15. **`NEXT_PUBLIC_BACKEND_URL` = `http://localhost:8181`** — never
    `127.0.0.1`. Hostname mismatch breaks HttpOnly refresh cookies.
16. **Jira story points** — update BOTH `customfield_10016`
    (estimate) AND `customfield_10036` (display). Bug/Task
    types may reject `customfield_10036` — that's OK.
17. **Ollama for experiments only** — host-native, not
    containerized. If absent, cascade falls back to
    Groq/Anthropic. No Docker service for Ollama.
18. **LHCI can't audit authenticated routes** — Lighthouse clears
    localStorage per navigation. Use `npm run perf:full` (Playwright)
    instead. See `PERFORMANCE.md`.

---

## Serena Shared Memories

For detailed architecture, conventions, debugging, and onboarding
knowledge, use Serena's shared memories. Run `list_memories` to
see all available topics.

| Category | Topics |
|----------|--------|
| `shared/architecture/` | system-overview, agent-init-pattern, groq-chunking, iceberg-data-layer, auth-jwt-flow, langsmith-observability, lighthouse-performance-workflow, subscription-billing, portfolio-analytics, currency-aware-agent, token-budget-concurrency, payment-transaction-ledger, sentiment-agent, iceberg-column-projection, llm-cascade-profiles, docker-containerization, redis-cache-layer, per-ticker-refresh, api-versioning, security-hardening-patterns, hybrid-db-postgresql-iceberg, context-aware-chat, intent-aware-routing, summary-based-context, interactive-stock-discovery, memory-augmented-chat, round-robin-cascade, ollama-local-llm, **stock-data-pipeline** |
| `shared/conventions/` | python-style, typescript-style, git-workflow, testing-patterns, performance, error-handling, llm-tool-forcing, jira-mcp-usage, security-hardening, e2e-test-patterns, isort-black-exclude-virtualenv, git-push-workflow, **ticker-format-standard** |
| `shared/debugging/` | common-issues, mock-patching-gotchas, chat-session-recording, cookie-hostname-mismatch, ohlcv-nan-close-price, razorpay-integration-gotchas, iceberg-epoch-dates, portfolio-watchlist-sync, iceberg-table-corruption-recovery, razorpay-customer-exists, playwright-react19-dash-patterns, asyncpg-sync-async-bridge, llm-hallucination-guardrail, sync-async-migration-patterns, payment-cookie-redirect, bind-tools-model-lookup, **pipeline-common-issues** |
| `shared/onboarding/` | setup-guide, test-venv-setup, tooling |
| `shared/api/` | streaming-protocol |

Load any memory with `read_memory` when you need the details.

---

## Gotchas (learned the hard way)

- **`settings.local.json` deny rules**: No parentheses in
  `Bash(...)` patterns — Claude Code parser treats `()` as
  pattern delimiters. Fork bomb rule crashed the CLI.
- **slowapi rate limiter**: Module-level singleton — state
  bleeds across test files. Use `limiter.enabled = False` in
  test fixtures, not `limiter.reset()`.
- **`get_settings().debug`**: May not exist in test context.
  Use `getattr(_get_settings(), "debug", True)` with fallback.
- **TokenBudget**: Use `reserve()`/`release()` (atomic), not
  `can_afford()`/`record()` (TOCTOU race). See memory
  `shared/architecture/token-budget-concurrency`.
- **StockRepository**: Always use `_require_repo()` from
  `tools/_stock_shared.py` — never instantiate directly.
- **E2E demo passwords**: Run `seed_demo_data.py` if login
  fails. Previous test runs may have changed passwords.
- **Iceberg table corruption**: If `FileNotFoundError` on
  parquet files, run `scripts/check_tables.py` to identify
  corrupted tables. Fix: drop + recreate via `create_tables.py`.
- **Portfolio ↔ Watchlist sync**: Adding a portfolio stock
  auto-links to watchlist. If unlinking fails, the ticker
  may be portfolio-only (pre-auto-link). See
  `scripts/backfill_portfolio_links.py`.
- **Razorpay "customer already exists"**: After DB rebuild,
  checkout self-heals by searching Razorpay for the existing
  customer by email.
- **ChatInput `readOnly` not `disabled`**: During loading,
  use `readOnly={loading}` to keep browser focus. `disabled`
  drops focus and requires manual click to re-engage.
- **Docker health check**: Backend health is at `/v1/health`
  (not `/health`). Routes are prefixed with `/v1`.
- **Docker Iceberg mount**: SQLite catalog stores absolute
  host paths in metadata. Mount `~/.ai-agent-ui` at the
  SAME path inside the container — not `/app/data`.
- **Ollama in Docker**: Backend reaches host Ollama via
  `host.docker.internal:11434`, not `localhost`. Set via
  `OLLAMA_BASE_URL` env var.
- **asyncpg `pool_pre_ping=True`**: Required when uvicorn
  reloads — stale connections from the old process cause
  "SSL connection has been closed unexpectedly". Always set
  in `backend/db/engine.py`.
- **Sync→async PG bridges**: See memory
  `shared/debugging/sync-async-migration-patterns` for
  `_run_pg()`, `_pg_session()`, missing `await`, `AsyncMock`.
- **Iceberg NaT/NaN → PG insert**: Iceberg timestamps can
  be `NaT` and floats can be `NaN`. Sanitize with
  `pd.Timestamp` checks and `float("nan")` guards before
  inserting into PostgreSQL — PG rejects both.
- **Docker seed script**: `seed_demo_data.py` needs
  `PYICEBERG_CATALOG__LOCAL__URI` set before any pyiceberg
  import. Script now sets it from `paths.py`. Also needs
  `fixtures/` volume mounted in `docker-compose.override.yml`.
- **MkDocs gen-files in Docker**: gen-files scripts need
  backend Python modules unavailable in docs container.
  Pre-generate instead: `python scripts/gen_config_docs.py
  > docs/backend/config-reference.md`.
- **Test mock dates**: Never hardcode dates in test mocks
  (e.g., `"2026-03-21"`) — they go stale. Use
  `str(int(time.time()) - 86400)` for "yesterday".
- **Intent-switch hallucination**: See memory
  `shared/architecture/summary-based-context`. Summary (~100
  tokens) replaces raw history (~3K) on intent switches.
- **Hallucination guardrail**: `synthesis.py:_is_hallucinated()`
  rejects responses with 3+ stock-analysis patterns (CMP:, P/E,
  RSI, SMA) but zero `tool_done` events. Don't use broad
  financial terms in the pattern — causes false positives on
  portfolio sector discussions.
- **ReAct iteration counter**: `sub_agents.py` MUST pass
  `iteration=iteration+1`. See `shared/architecture/round-robin-cascade`.
- **Groq tool call IDs → Anthropic**: Groq models generate
  tool call IDs that may not match Anthropic's
  `^[a-zA-Z0-9_-]+$` pattern. `_sanitize_tool_ids()` in
  `llm_fallback.py` cleans them before the Anthropic fallback.
- **Groq TPD daily limits**: Round-robin pools spread load
  across 6 models (~2.3M combined TPD). Monitor via Admin →
  LLM Observability → Daily Token Budget card. `TokenBudget`
  seeds from Iceberg on restart so counters persist.
- **`bind_tools` model_lookup**: After `FallbackLLM.bind_tools()`,
  `_model_lookup` must be rebuilt. Pool routing uses this dict —
  stale references send requests without tools, causing text-only
  responses instead of tool calls.
- **UserMemory `extend_existing`**: When `UserMemory` ORM model
  is imported both at module level (via `__init__.py`) and lazily
  inside functions, SQLAlchemy's `Base.metadata` raises "Table
  already defined". Fix: `extend_existing=True` in `__table_args__`.
- **Frontend Docker + Turbopack**: `lightningcss` native `.node`
  addons can't resolve inside Turbopack's PostCSS sandbox in Alpine
  containers. Frontend runs natively on host; Docker frontend uses
  `profiles: ["native-frontend"]` so it doesn't start by default.
- **Iceberg flush window**: `ObservabilityCollector` flushes every
  30s. Restarts within that window lose unflushed events.
  `seed_daily_from_iceberg()` on `TokenBudget` and
  `_seed_from_iceberg()` on `ObservabilityCollector` restore
  today's totals on startup.
- **`ollama-profile embedding`**: Uses `/api/embed` (not
  `/api/generate`) for warmup. Only 274MB — coexists with larger
  models in theory but gets evicted under memory pressure.
- **Redis cache poisoning**: `cache_warmup.py` caches bare
  registry on startup (no prices/sparkline). Registry warmup
  is disabled — real endpoint caches enriched data. After code
  changes: `docker compose exec redis redis-cli FLUSHALL`.
- **`.pyiceberg.yaml` in Docker**: Must be mounted at
  `/app/.pyiceberg.yaml:ro` in `docker-compose.override.yml`.
  Without it, Iceberg reads fail silently in the container.
- **FastAPI Query default in internal calls**: When calling
  an endpoint function internally (not via HTTP), pass
  `Query()` params explicitly (e.g., `ticker=None`).
  Otherwise FastAPI injects the Query object, not None.
- **jugaad-data timeout**: `stock_df()` has no timeout.
  NseSource wraps it in `asyncio.wait_for(timeout=60.0)`.
- **Iceberg concurrent writes**: SQLite catalog conflicts
  under Semaphore(10). Fundamentals job uses Semaphore(1).
- **Superuser insights visibility**: `_get_user_tickers()`
  in `insights_routes.py` shows all registry tickers for
  superusers, watchlist-only for general users.

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

# Performance (run from frontend/)
npm run perf:check                # LHCI on /login (pre-PR gate)
npm run perf:audit                # Playwright 10-route quick check
npm run perf:full                 # Full 42-point surface audit
npm run analyze                   # Bundle treemap (ANALYZE=true)
```
