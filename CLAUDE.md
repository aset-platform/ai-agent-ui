# CLAUDE.md — AI Agent UI

> Slim project instructions for Claude Code. Detailed knowledge
> lives in Serena shared memories — run `list_memories` to browse.

---

## Project Overview

Fullstack agentic chat app with stock analysis and Prophet forecasting.
Native portfolio dashboard with TradingView lightweight-charts +
react-plotly.js. Dual payment gateways (Razorpay INR + Stripe USD).
All pages fully migrated from Dash to Next.js.

| Service | Port | Entry point | Stack |
|---------|------|-------------|-------|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x, SQLAlchemy 2.0 async |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19, lightweight-charts |
| PostgreSQL | 5432 | Docker | PostgreSQL 16 Alpine (OLTP: 5 tables) |
| Redis | 6379 | Docker | Redis 7 Alpine |
| Docs | 8000 | `mkdocs serve` | MkDocs Material |
| Alembic | — | `backend/db/migrations/` | Schema migrations for PostgreSQL |

```bash
# Docker (preferred — mirrors production)
docker compose up -d                        # all services
docker compose ps                           # health check
docker compose logs -f backend              # tail logs
docker compose down                         # stop all

# Native (legacy, still works)
./run.sh start                              # all services
source ~/.ai-agent-ui/venv/bin/activate      # Python virtualenv

# Ollama (host-native, not containerized)
ollama-profile coding                       # load Qwen for code gen
ollama-profile reasoning                    # load GPT-OSS 20B
ollama-profile status                       # check loaded model
```

**Key dirs**: `backend/` (agents, tools, config), `backend/db/` (ORM models, async engine, Alembic migrations, DuckDB layer), `auth/` (JWT + RBAC + OAuth PKCE), `stocks/` (Iceberg — 14 OLAP tables), `frontend/` (SPA), `dashboard/` (Dash callbacks, imported by backend), `hooks/` (pre-commit, pre-push).

**Docker files**: `Dockerfile.backend`, `Dockerfile.frontend`,
`docker-compose.yml`, `docker-compose.override.yml` (dev hot-reload),
`.env.example` (template), `.env` (secrets, gitignored).

**Config**: `pyproject.toml` + `.flake8` (79 chars), `frontend/eslint.config.mjs`.

**Data**: `~/.ai-agent-ui/` (override: `AI_AGENT_UI_HOME`). Paths in `backend/paths.py`.

**Env**: `.env` (Docker Compose, gitignored), `.env.example` (template).

---

## LLM Cascade Architecture

`FallbackLLM` in `backend/llm_fallback.py` — N-tier cascade:

| Tier | Provider | Model | When |
|------|----------|-------|------|
| 0 | Ollama (local) | gpt-oss:20b | Sentiment/batch (`ollama_first=True`) |
| 1-4 | Groq (free) | llama-3.3-70b → kimi-k2 → gpt-oss-120b → llama-4-scout | Interactive chat |
| N-1 | Ollama (local) | gpt-oss:20b | Chat fallback (`ollama_first=False`) |
| N | Anthropic (paid) | claude-sonnet-4-6 | Final fallback |

- `OllamaManager` (`backend/ollama_manager.py`): TTL-cached health probe,
  load/unload profiles. If Ollama unavailable, cascade skips it.
- Admin API: `GET/POST /v1/admin/ollama/{status,load,unload}`
- `ollama-profile` CLI: `coding` (Qwen), `reasoning` (GPT-OSS), `unload`
- Observability: `provider="ollama"` in `ObservabilityCollector`

---

## Hybrid DB Architecture

OLTP/OLAP split — PostgreSQL for row-level CRUD, Iceberg for
append-only analytics.

### PostgreSQL tables (SQLAlchemy 2.0 async ORM)

| Table | Module | Pattern |
|-------|--------|---------|
| `auth.users` | `backend/db/models.py` | CRUD via `UserRepository` |
| `auth.user_tickers` | `backend/db/models.py` | Upsert + delete |
| `auth.payment_transactions` | `backend/db/models.py` | Insert + read |
| `stocks.registry` | `backend/db/pg_stocks.py` | Upsert |
| `stocks.scheduled_jobs` | `backend/db/pg_stocks.py` | Upsert |

### Iceberg tables (14 — append / scoped-delete)

`audit_log`, `usage_history`, `company_info`, `dividends`, `ohlcv`,
`technical_indicators`, `analysis_summary`, `forecast_runs`,
`forecasts`, `quarterly_results`, `llm_pricing`, `llm_usage`,
`scheduler_runs` (stocks ns) + portfolio_transactions (stocks ns)

### Key components

- `backend/db/engine.py` — async `session_factory` (asyncpg driver,
  `pool_pre_ping=True`)
- `backend/db/models.py` — 5 SQLAlchemy ORM models (FK cascade,
  JSONB, composite PK, indexes)
- `backend/db/migrations/` — Alembic async migrations
- `backend/db/user_repository.py` — `UserRepository` facade
  (replaces `IcebergUserRepository` for OLTP tables)
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
| `shared/architecture/` | system-overview, agent-init-pattern, groq-chunking, iceberg-data-layer, auth-jwt-flow, langsmith-observability, lighthouse-performance-workflow, subscription-billing, portfolio-analytics, currency-aware-agent, token-budget-concurrency, payment-transaction-ledger, sentiment-agent, iceberg-column-projection, llm-cascade-profiles, docker-containerization, redis-cache-layer, per-ticker-refresh, api-versioning, security-hardening-patterns |
| `shared/conventions/` | python-style, typescript-style, git-workflow, testing-patterns, performance, error-handling, llm-tool-forcing, jira-mcp-usage, security-hardening, e2e-test-patterns, isort-black-exclude-virtualenv, git-push-workflow |
| `shared/debugging/` | common-issues, mock-patching-gotchas, chat-session-recording, cookie-hostname-mismatch, ohlcv-nan-close-price, razorpay-integration-gotchas, iceberg-epoch-dates, portfolio-watchlist-sync, iceberg-table-corruption-recovery, razorpay-customer-exists, playwright-react19-dash-patterns |
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
- **`_run_pg()` bridge**: Scheduler threads are sync; use the
  `_run_pg(coro)` helper in `backend/db/pg_stocks.py` to
  run async SQLAlchemy calls from the scheduler event loop.
  Do NOT call `asyncio.run()` directly — it creates a new
  loop and conflicts with uvicorn's loop.
- **Iceberg NaT/NaN → PG insert**: Iceberg timestamps can
  be `NaT` and floats can be `NaN`. Sanitize with
  `pd.Timestamp` checks and `float("nan")` guards before
  inserting into PostgreSQL — PG rejects both.

---

## Quick Reference

```bash
# Lint
black backend/ auth/ stocks/ scripts/
isort backend/ auth/ stocks/ scripts/ --profile black
flake8 backend/ auth/ stocks/ scripts/
cd frontend && npx eslint . --fix

# Test
python -m pytest tests/ -v        # all (~620 tests)
cd frontend && npx vitest run     # frontend (18 tests)
cd e2e && npm test                # E2E (~219 tests, needs live services)

# Database migrations (PostgreSQL)
alembic upgrade head                          # apply all migrations
alembic revision --autogenerate -m "desc"    # generate new migration
PYTHONPATH=backend python scripts/migrate_iceberg_to_pg.py  # one-time data migration

# Seed (required before first E2E run)
PYTHONPATH=backend python scripts/seed_demo_data.py

# Performance (run from frontend/)
npm run perf:check                # LHCI on /login (pre-PR gate)
npm run perf:audit                # Playwright 10-route quick check
npm run perf:full                 # Full 42-point surface audit
npm run analyze                   # Bundle treemap (ANALYZE=true)
```
