# Project Index: ai-agent-ui

> AI-agent-optimised codebase map. For human onboarding, see `docs/`.
> Last refreshed: 2026-03-29 (Sprint 4 + Containerization)

---

## Project Structure

```
ai-agent-ui/
├── backend/              # FastAPI + LangChain + LangGraph (Python 3.12)
│   ├── main.py           # ASGI entry, ChatServer, startup wiring
│   ├── config.py         # Pydantic Settings (env-based, database_url, ollama_*)
│   ├── routes.py         # HTTP chat + streaming + admin endpoints
│   ├── ws.py             # WebSocket /ws/chat
│   ├── bootstrap.py      # Tool + agent + graph registration
│   ├── llm_fallback.py   # N-tier Ollama → Groq → Anthropic cascade
│   ├── ollama_manager.py # Local LLM lifecycle (health probe, load/unload)
│   ├── token_budget.py   # Sliding-window TPM/RPM tracker
│   ├── message_compressor.py  # 3-stage context compression
│   ├── observability.py  # Tier health, cascade counts → Iceberg
│   ├── tracing.py        # LangSmith + LangFuse setup
│   ├── agents/           # LangGraph supervisor + 5 sub-agents
│   │   ├── graph.py      # 11-node StateGraph builder
│   │   ├── sub_agents.py # Factory + dynamic context injection
│   │   ├── configs/      # portfolio, stock_analyst, forecaster, research, sentiment
│   │   └── nodes/        # guardrail, router, llm_classifier, supervisor, synthesis, log_query
│   ├── tools/            # 26 LangChain @tool modules
│   │   ├── stock_data_tool.py      # 7 stock data tools
│   │   ├── price_analysis_tool.py  # Technical analysis + chart
│   │   ├── forecasting_tool.py     # Prophet forecast pipeline
│   │   ├── news_tools.py           # Tiered news (yfinance → RSS → SerpAPI)
│   │   ├── portfolio_tools.py      # 7 portfolio tools
│   │   ├── sentiment_agent.py      # 3 sentiment tools (ollama_first=True)
│   │   ├── _sentiment_sources.py   # 3-source headline fetcher + dedup
│   │   ├── _sentiment_scorer.py    # FallbackLLM scoring + weighted avg
│   │   └── _forecast_model.py      # Prophet training
│   ├── jobs/             # Background schedulers
│   │   ├── gap_filler.py           # Batch sentiment with Ollama auto-load/unload
│   │   ├── scheduler_service.py    # Admin UI scheduler (catch-up, monthly)
│   │   └── executor.py             # Job execution engine
│   ├── dashboard_routes.py  # /v1/dashboard/* (LLM usage widget with provider)
│   ├── insights_routes.py   # /v1/insights/*
│   └── audit_routes.py      # /v1/audit/*
├── auth/                 # JWT + RBAC + OAuth PKCE + Subscriptions
│   ├── service.py        # AuthService
│   ├── dependencies.py   # get_current_user, require_tier
│   ├── endpoints/        # auth, oauth, ticker, subscription, admin routes
│   ├── repo/             # Iceberg user CRUD (copy-on-write)
│   └── create_tables.py  # 5 auth Iceberg tables
├── stocks/               # Iceberg data layer
│   ├── repository.py     # StockRepository (~4000 lines)
│   └── create_tables.py  # 15 stocks Iceberg tables
├── frontend/             # Next.js 16 + React 19 + Tailwind CSS 4
│   ├── app/              # App Router (12 routes)
│   │   ├── (authenticated)/dashboard/    # Portfolio dashboard
│   │   ├── (authenticated)/analytics/    # Analysis, Compare, Insights
│   │   ├── (authenticated)/admin/        # Admin (6 tabs)
│   │   └── login/
│   ├── components/       # 44 React components
│   │   ├── ChatPanel.tsx       # Chat panel (scroll, focus, markdown)
│   │   ├── ChatInput.tsx       # Input (readOnly during loading, autoFocus)
│   │   ├── widgets/            # HeroSection, WatchlistWidget, LLMUsageWidget
│   │   ├── charts/             # StockChart, ForecastChart, CompareChart
│   │   └── admin/              # SchedulerTab, UserModal
│   ├── hooks/            # 19 custom hooks
│   │   ├── useSendMessage.ts   # Streaming NDJSON + tool calls header
│   │   └── useDashboardData.ts # SWR data fetching
│   └── lib/              # apiFetch, config, types, auth, constants
├── dashboard/            # Plotly Dash (imported by backend for callbacks)
├── scripts/              # 25 utility scripts
├── tests/backend/        # 52 test files (~620 test cases)
├── e2e/                  # Playwright E2E (~219 tests)
├── docs/                 # MkDocs Material
│   └── dev/              # changelog, how-to-run, decisions, e2e-testing
│
│ ── Docker ──────────────────────────────────────────
├── Dockerfile.backend    # 2-stage: builder (gcc) → runtime (slim)
├── Dockerfile.frontend   # 3-stage: deps → build → runner (standalone)
├── docker-compose.yml    # backend + frontend + postgres:16 + redis:7
├── docker-compose.override.yml  # Dev hot-reload (source mounts)
├── .env.example          # Env var template (committed)
├── .env                  # Secrets (gitignored)
└── .dockerignore         # Build context exclusions
```

## Entry Points

| Entry | Path | Port | Docker |
|-------|------|------|--------|
| Backend | `backend/main.py` | 8181 | `docker compose up backend` |
| Frontend | `frontend/app/page.tsx` | 3000 | `docker compose up frontend` |
| PostgreSQL | Docker image | 5432 | `docker compose up postgres` |
| Redis | Docker image | 6379 | `docker compose up redis` |
| Ollama | Host-native | 11434 | `ollama-profile reasoning` |
| All services | — | — | `docker compose up -d` |
| Docs | `mkdocs.yml` | 8000 | `mkdocs serve` |

## LLM Cascade

```
Sentiment/Batch (ollama_first=True):
  Ollama gpt-oss:20b → Groq (4 tiers) → Anthropic claude-sonnet-4-6

Interactive Chat (ollama_first=False):
  Groq (4 tiers) → Ollama gpt-oss:20b → Anthropic claude-sonnet-4-6
```

Groq tiers: `llama-3.3-70b → kimi-k2 → gpt-oss-120b → llama-4-scout`

All via FallbackLLM + OllamaManager + TokenBudget + MessageCompressor + LangSmith tracing.

**Ollama CLI**: `ollama-profile coding|reasoning|unload|status`
**Admin API**: `GET/POST /v1/admin/ollama/{status,load,unload}`

## LangGraph Supervisor (5 sub-agents)

```
START → guardrail → router → [llm_classifier] → supervisor
  → portfolio | stock_analyst | forecaster | research | sentiment
  → synthesis → log_query → END
```

| Agent | Purpose | Ollama Priority |
|-------|---------|-----------------|
| portfolio | Currency-aware holdings, performance, risk | After Groq |
| stock_analyst | Technical analysis pipeline | After Groq |
| forecaster | Prophet + ensemble forecasting | After Groq |
| research | Tiered news search | After Groq |
| sentiment | 3-source headline scoring, market mood | **First** |

## Iceberg Tables (20)

### stocks (15 tables) — OLAP, stays on Iceberg
`ohlcv` · `technical_indicators` · `forecasts` · `forecast_runs` · `sentiment_scores` · `analysis_summary` · `company_info` · `dividends` · `quarterly_results` · `llm_usage` · `llm_pricing` · `registry` · `scheduled_jobs` · `scheduler_runs` · `portfolio_transactions`

### auth (5 tables) — planned migration to PostgreSQL
`users` · `user_tickers` · `audit_log` · `payment_transactions` · `usage_history`

## Configuration

| File | Purpose |
|------|---------|
| `.env` / `.env.example` | All env vars (Docker Compose reads this) |
| `backend/config.py` | Pydantic Settings (database_url, ollama_*, groq_*) |
| `pyproject.toml` | black, isort, pytest (79 chars) |
| `.flake8` | Flake8 linter |
| `frontend/next.config.ts` | Next.js (standalone output) |
| `.pyiceberg.yaml` | Iceberg catalog (SQLite) |
| `docker-compose.yml` | Container orchestration |

## Key Dependencies

**Backend**: FastAPI, LangChain 1.2, LangGraph 1.0, langchain-ollama, langchain-groq, langchain-anthropic, PyIceberg, Prophet, Redis, Razorpay/Stripe SDKs

**Frontend**: Next.js 16, React 19, Tailwind CSS 4, lightweight-charts, ECharts, react-plotly.js, SWR

## Quick Start

```bash
# Docker (recommended)
cp .env.example .env              # fill in API keys
docker compose up -d              # start all services
open http://localhost:3000        # frontend

# Ollama (optional — local LLM)
ollama-profile reasoning          # load GPT-OSS 20B

# Tests
source ~/.ai-agent-ui/venv/bin/activate
python -m pytest tests/ -v        # ~620 tests
cd frontend && npx vitest run     # 18 tests
```

## Sprint 4 (43 SP, all Done)

Scheduler overhaul (14 SP) · Ollama LLM integration (11 SP) · Docker containerization (13 SP) · Billing/Iceberg fixes (5 SP)

## Backlog (Sprint 5-6)

- **Epic: Hybrid DB Migration** (31 SP) — PostgreSQL for OLTP, Iceberg for OLAP, DuckDB query engine
- **Epic: Cloud IaC** (21 SP) — Terraform + Kubernetes + CI/CD
