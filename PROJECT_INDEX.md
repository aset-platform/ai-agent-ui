# Project Index: AI Agent UI

> AI-agent-optimised codebase map. For human onboarding, see `docs/`.
> Last refreshed: 2026-03-29 (Sprint 4 + Hybrid DB Migration)

## Project Structure

```
ai-agent-ui/
├── backend/          97 .py — FastAPI, LangChain agents, tools, ORM
│   ├── agents/       LangGraph nodes, configs, registry
│   ├── tools/        23 tool implementations (forecast, sentiment, portfolio)
│   ├── db/           SQLAlchemy models, engine, Alembic, DuckDB, pg_stocks
│   └── jobs/         Scheduler service, executor, gap filler
├── auth/             34 .py — JWT, OAuth PKCE, endpoints, repositories
│   ├── endpoints/    9 route handlers (auth, admin, ticker, subscription)
│   └── repo/         UserRepository facade, reads, writes, oauth, ticker, payment
├── stocks/           8 .py — Iceberg tables (14 OLAP), repository, cached_repository
├── frontend/         109 .tsx — Next.js 16, React 19, TailwindCSS 4
│   ├── app/          18 pages (dashboard, analytics, admin, portfolio, docs)
│   ├── components/   Charts, widgets, admin panels, chat UI
│   ├── hooks/        18 custom hooks (data, auth, chat, portfolio)
│   └── lib/          apiFetch, auth, OAuth, config, types
├── tests/            57 .py — pytest backend tests
├── e2e/              55 .ts — Playwright E2E tests
├── scripts/          39 — seed, migrate, backfill, perf, setup
└── docs/             35+ .md — MkDocs Material site
```

## Entry Points

- **Backend:** `backend/main.py` → uvicorn :8181
- **Frontend:** `frontend/app/page.tsx` → Next.js :3000
- **Tests:** `python -m pytest tests/ -v` (backend), `cd e2e && npm test` (E2E)
- **Docs:** `mkdocs serve` → :8000

## Data Architecture (Hybrid)

**PostgreSQL (5 OLTP tables):** users, user_tickers, payment_transactions,
stock_registry, scheduled_jobs — `backend/db/models/`, Alembic migrations

**Iceberg (14 OLAP tables):** ohlcv, indicators, forecasts, dividends,
company_info, analysis_summary, forecast_runs, quarterly_results,
llm_pricing, llm_usage, scheduler_runs, audit_log, usage_history,
portfolio_transactions — `stocks/repository.py`, PyIceberg

**DuckDB:** In-process analytics on Iceberg — `backend/db/duckdb_engine.py`

## Core Modules

| Module | Path | Purpose |
|--------|------|---------|
| Routes | `backend/routes.py` (1406 LOC) | Main HTTP API |
| Dashboard | `backend/dashboard_routes.py` (1677 LOC) | Dashboard endpoints |
| Insights | `backend/insights_routes.py` (988 LOC) | Analytics endpoints |
| WebSocket | `backend/ws.py` (486 LOC) | Real-time chat |
| LLM Fallback | `backend/llm_fallback.py` (692 LOC) | Multi-tier cascade |
| Token Budget | `backend/token_budget.py` (502 LOC) | Cost-aware LLM |
| Observability | `backend/observability.py` (667 LOC) | OpenTelemetry |
| Auth Service | `auth/service.py` | JWT + bcrypt |
| User Repo | `auth/repo/repository.py` | UserRepository facade |
| Stock Repo | `stocks/repository.py` | Iceberg + PG wrappers |
| Agent Graph | `backend/agents/graph.py` | LangGraph state machine |

## Configuration

| File | Purpose |
|------|---------|
| `pyproject.toml` | Black/isort/pytest (79 chars) |
| `docker-compose.yml` | Backend, Frontend, PG 16, Redis 7 |
| `alembic.ini` | PG schema migrations |
| `.pyiceberg.yaml` | Iceberg SQLite catalog |
| `mkdocs.yml` | Documentation site |
| `.flake8` | Linting rules |

## Key Dependencies

**Backend:** FastAPI 0.135, SQLAlchemy 2.0, LangChain 1.2, LangGraph 1.0,
asyncpg, Alembic, DuckDB, PyIceberg, pandas 3.0, Prophet 1.3, yfinance,
Stripe, Razorpay, Redis 7, OpenTelemetry

**Frontend:** Next.js 16, React 19, TailwindCSS 4, ECharts 6,
lightweight-charts 5, Plotly, SWR, Axios, Vitest

## Quick Start

```bash
docker compose up -d              # Start all services
alembic upgrade head              # Apply PG migrations
PYTHONPATH=backend python scripts/seed_demo_data.py  # Seed data
# Visit http://localhost:3000
```

## Stats

| Metric | Count |
|--------|-------|
| Python modules | 139 |
| TypeScript files | 109 |
| Backend tests | 57 files (~644 tests) |
| E2E tests | 55 files (~219 tests) |
| Backend deps | 180 |
| Frontend deps | 48 |
| Docker services | 4 (+ Ollama host-native) |
