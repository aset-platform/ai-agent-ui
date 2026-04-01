# Project Index: AI Agent UI

> AI-agent-optimised codebase map. For human onboarding, see `docs/`.
> Last refreshed: 2026-03-30 (Sprint 4 — Context-Aware Chat + Recency News)

## Project Structure

```
ai-agent-ui/
├── backend/          105 .py — FastAPI, LangChain agents, tools, ORM
│   ├── agents/       LangGraph nodes, configs, conversation context
│   │   └── nodes/    guardrail, router, supervisor, topic_classifier
│   ├── tools/        25 tool implementations + _date_utils
│   ├── db/           SQLAlchemy models, engine, Alembic, DuckDB
│   └── jobs/         Scheduler service, executor, gap filler
├── auth/             34 .py — JWT, OAuth PKCE, endpoints, repos
│   ├── endpoints/    9 route handlers
│   └── repo/         UserRepository facade (async PG)
├── stocks/           8 .py — Iceberg (14 OLAP), repository
├── frontend/         89 .tsx — Next.js 16, React 19, Tailwind 4
│   ├── app/          18 pages (dashboard, analytics, admin, portfolio)
│   ├── components/   Charts, widgets, admin, chat UI
│   ├── hooks/        18 hooks (data, auth, chat, portfolio)
│   └── providers/    ChatProvider (session_id), ThemeProvider
├── tests/            60+ .py — pytest (712 tests)
├── e2e/              55 .ts — Playwright E2E
├── scripts/          39 — seed, migrate, backfill, perf
└── docs/             40+ .md — MkDocs Material site
```

## Entry Points

- **Backend:** `backend/main.py` → uvicorn :8181
- **Frontend:** `frontend/app/page.tsx` → Next.js :3000
- **Docs:** Docker `docs` service → MkDocs :8000
- **Tests:** `pytest tests/ -v` (712), `cd e2e && npm test` (E2E)

## Data Architecture (Hybrid)

**PostgreSQL (5 OLTP):** users, user_tickers, payment_transactions,
stock_registry, scheduled_jobs — `backend/db/models/`, Alembic

**Iceberg (14 OLAP):** ohlcv, indicators, forecasts, dividends,
company_info, analysis_summary, forecast_runs, quarterly_results,
llm_pricing, llm_usage, scheduler_runs, audit_log, usage_history,
portfolio_transactions — `stocks/repository.py`

**DuckDB:** In-process analytics — `backend/db/duckdb_engine.py`

## Core Modules

| Module | Path | Purpose |
|--------|------|---------|
| Routes | `backend/routes.py` | Main HTTP API + context update |
| Dashboard | `backend/dashboard_routes.py` | Dashboard + watchlist |
| Insights | `backend/insights_routes.py` | Analytics endpoints |
| WebSocket | `backend/ws.py` | Real-time chat streaming |
| LLM Fallback | `backend/llm_fallback.py` | Multi-tier cascade |
| Token Budget | `backend/token_budget.py` | Cost-aware LLM routing |
| Observability | `backend/observability.py` | OpenTelemetry + usage |
| Agent Graph | `backend/agents/graph.py` | LangGraph supervisor |
| Conv Context | `backend/agents/conversation_context.py` | Session context + summary |
| Topic Classifier | `backend/agents/nodes/topic_classifier.py` | Follow-up detection |
| Date Utils | `backend/tools/_date_utils.py` | News recency filtering |
| Sentiment | `backend/tools/_sentiment_scorer.py` | Time-decay scoring |
| Auth Service | `auth/service.py` | JWT + bcrypt |
| User Repo | `auth/repo/repository.py` | Async PG user ops |
| Stock Repo | `stocks/repository.py` | Iceberg + PG wrappers |

## LLM Cascade (FallbackLLM)

| Tier | Provider | Model | Use |
|------|----------|-------|-----|
| 0 | Ollama | gpt-oss:20b | Sentiment/batch |
| 1-4 | Groq | llama-3.3-70b → scout-17b | Interactive |
| N | Anthropic | claude-sonnet-4-6 | Final fallback |

## Context-Aware Chat (Phase 1)

- `ConversationContext` in-memory store (1hr TTL)
- Topic classifier: "follow_up" or "new_topic" via LLM
- Rolling summary updated after each turn (Ollama/Groq)
- [Conversation Context] block injected into system prompts
- Frontend passes `session_id` in HTTP + WebSocket

## Recency-Aware News

- `_date_utils.py`: parse Unix/RFC2822/ISO8601 dates
- `days_back=7` default on news + sentiment tools
- Time-decay: 1.0 (0-2d), 0.5 (3-7d), 0.25 (8-30d), 0.1 (>30d)

## Configuration

| File | Purpose |
|------|---------|
| `pyproject.toml` | Black/isort/pytest (79 chars) |
| `docker-compose.yml` | 5 services: backend, frontend, PG, Redis, docs |
| `docker-compose.override.yml` | Dev hot-reload + fixtures mount |
| `Dockerfile.backend` | 2-stage Python 3.12-slim |
| `Dockerfile.frontend` | 3-stage Next.js standalone |
| `Dockerfile.docs` | MkDocs Material 9 |
| `alembic.ini` | PG schema migrations |
| `.pyiceberg.yaml` | Iceberg SQLite catalog |
| `mkdocs.yml` | Documentation site |

## Key Dependencies

**Backend:** FastAPI, SQLAlchemy 2.0 async, LangChain 1.2,
LangGraph 1.0, asyncpg, Alembic, DuckDB, PyIceberg, pandas,
Prophet, yfinance, feedparser, Stripe, Razorpay, Redis, OTel

**Frontend:** Next.js 16, React 19, TailwindCSS 4, ECharts,
lightweight-charts, Plotly, SWR, Playwright

## Quick Start

```bash
docker compose up -d                    # 5 services
docker compose exec backend \
  python scripts/seed_demo_data.py      # Seed data
# Visit http://localhost:3000
# Admin: admin@demo.com / Admin123!
# User:  test@demo.com  / Test1234!
```

## Stats

| Metric | Count |
|--------|-------|
| Python modules | 165 |
| TypeScript files | 89 |
| Backend tests | 712 (60+ files) |
| E2E tests | ~219 (55 files) |
| Docker services | 5 (+ Ollama host-native) |
| Perf score | 94/100 (Sprint 3 = Sprint 4) |
| Sprint 4 tickets | 32 (31 Done) |
