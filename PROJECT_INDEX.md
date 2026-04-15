# Project Index: AI Agent UI

> AI-agent-optimised codebase map. For human onboarding, see `docs/`.
> Last refreshed: 2026-04-15 (Sprint 7 — Forecast Enrichment + FinBERT Sentiment + XGBoost Features)

---

## Project Structure

```
ai-agent-ui/
├── backend/               # FastAPI application (:8181)
│   ├── main.py            # Entry point
│   ├── agents/            # LangGraph agentic framework
│   │   ├── configs/       # 7 sub-agent configs (stock, portfolio, forecast, rec, etc.)
│   │   ├── nodes/         # 10 graph nodes (guardrail, router, synthesis, etc.)
│   │   ├── graph.py       # LangGraph state graph
│   │   ├── sub_agents.py  # Sub-agent tool-calling loop factory
│   │   └── conversation_context.py  # PG-persisted multi-turn context
│   ├── tools/             # 35 LLM-callable tool modules
│   ├── jobs/              # 7 scheduler executors + pipeline chaining
│   ├── pipeline/          # CLI data pipeline (19 commands, 21 files)
│   │   ├── runner.py      # CLI entry point
│   │   ├── sources/       # yfinance, NSE, racing
│   │   ├── jobs/          # ohlcv, fundamentals, fill_gaps, seed
│   │   └── screener/      # Piotroski F-Score
│   ├── db/                # ORM models, migrations, DuckDB
│   │   ├── models/        # 18 SQLAlchemy models
│   │   ├── migrations/    # 11 Alembic async migrations (+ forecast_runs schema v2)
│   │   ├── engine.py      # Async session factory
│   │   ├── duckdb_engine.py # Iceberg read engine + metadata cache
│   │   └── pg_stocks.py   # PG CRUD (registry, scheduler, pipeline, recs)
│   ├── config.py          # Settings (Pydantic)
│   ├── routes.py          # Chat API + admin endpoints
│   ├── ws.py              # WebSocket chat handler
│   ├── market_routes.py   # Market ticker (Nifty/Sensex, NSE+Yahoo)
│   ├── dashboard_routes.py # Dashboard/chart API
│   ├── insights_routes.py # Screener/analytics API
│   ├── observability.py   # LLM usage collector + Iceberg flush
│   ├── llm_fallback.py    # N-tier LLM cascade (Groq → Ollama → Anthropic)
│   ├── token_budget.py    # Per-model TPM/RPM/TPD/RPD sliding windows
│   └── bootstrap.py       # Tool + agent registration
├── auth/                  # JWT + RBAC + OAuth PKCE
├── stocks/                # Iceberg repository (5,200+ lines)
│   ├── repository.py      # All Iceberg reads (DuckDB-first) + writes
│   └── cached_repository.py # TTL-cached wrapper
├── frontend/              # Next.js 16 SPA (:3000)
│   ├── app/               # 12 pages (App Router)
│   ├── components/        # 30+ components (admin, charts, insights, widgets)
│   ├── hooks/             # 19 SWR data hooks
│   ├── providers/         # Chat, Layout context providers
│   └── lib/               # Types, config, apiFetch
├── e2e/                   # 54 Playwright specs
├── tests/                 # 96 pytest files (~755+ tests)
├── scripts/               # 28 data/migration/seed scripts + poc_forecast_comparison.py
├── docs/                  # 56 MkDocs Material pages (15 dirs)
└── docker-compose.yml     # 5 services (backend, frontend, PG, Redis, docs)
```

---

## Entry Points

| Entry | Path | Port |
|-------|------|------|
| Backend API | `backend/main.py` | 8181 |
| Frontend SPA | `frontend/app/page.tsx` | 3000 |
| Pipeline CLI | `backend/pipeline/runner.py` | — |
| Scheduler | `backend/jobs/scheduler_service.py` | daemon |
| Docs | `docs/` via MkDocs | 8000 |

---

## Database (Hybrid PG + Iceberg)

**PostgreSQL (18 tables)**: users, user_tickers, payments, registry,
scheduled_jobs, scheduler_runs, recommendation_runs, recommendations,
recommendation_outcomes, market_indices, user_memories (pgvector
768-dim), conversation_contexts, stock_master, stock_tags,
ingestion_cursor, ingestion_skipped, pipelines, pipeline_steps.

**Iceberg (14 tables)**: ohlcv (1.4M rows), company_info, dividends,
quarterly_results, analysis_summary, forecast_runs (27 cols, incl.
confidence_score + confidence_components), forecasts, piotroski_scores,
sentiment_scores, llm_pricing, llm_usage, portfolio_transactions,
audit_log, usage_history.

**Rule**: Mutable state → PG. Append-only analytics → Iceberg.
DuckDB for ALL Iceberg reads (metadata cache, auto-invalidated).

---

## Chat Agent Architecture

6 sub-agents: stock_analyst, portfolio, forecaster, research,
sentiment, recommendation. Routed by 2-tier intent classifier
(keyword → LLM fallback).

Key flow: guardrail → router → supervisor → sub-agent (tool loop)
→ synthesis → response.

Context: PG-persisted ConversationContext (cross-session resume).
Memory: pgvector semantic retrieval (nomic-embed-text 768-dim).

LLM Cascade: Groq pools (llama-3.3-70b, kimi-k2, qwen3-32b) →
(gpt-oss-120b, gpt-oss-20b) → scout-17b → Ollama → Anthropic.

---

## Key Modules

| Module | Files | Purpose |
|--------|-------|---------|
| `backend/agents/` | 30+ | LangGraph graph, 8 configs, 11 nodes, context |
| `backend/tools/` | 35 | Stock tools: forecast, analysis, sentiment, portfolio, recs |
| `backend/tools/_forecast_regime.py` | 1 | Volatility regime classification (low/medium/high/extreme) |
| `backend/tools/_forecast_features.py` | 1 | Tier 1/2 feature computation (macro, technical, sentiment) |
| `backend/tools/_sentiment_finbert.py` | 1 | FinBERT batch sentiment scorer (torch CPU, transformers) |
| `backend/jobs/` | 8 | Executor registry, pipeline chaining, batch refresh, recs |
| `backend/pipeline/` | 21 | CLI: download, seed, bulk-download, analytics, forecast, screen |
| `backend/db/models/` | 18 | SQLAlchemy ORM (PG tables) |
| `stocks/repository.py` | 1 (5.2K lines) | Iceberg CRUD + DuckDB reads + PG bridge |
| `frontend/hooks/` | 19 | SWR data fetching for all pages |
| `frontend/components/` | 30+ | Admin, charts, insights, widgets, modals |
| `scripts/poc_forecast_comparison.py` | 1 | POC test harness: baseline vs enriched forecast comparison |

---

## Scheduler & Jobs

6 job types: `data_refresh`, `compute_analytics`, `run_sentiment`,
`run_forecasts`, `run_piotroski`, `recommendations`. All accept
`force=False`. Market ticker runs independently (30s poll, not scheduled).

Freshness gates: daily (OHLCV, analytics, sentiment), weekly
(forecasts), monthly (CV accuracy auto-refresh via 30-day TTL).

Pipeline: sequential steps with skip-on-failure. India (4 steps,
~10 min) and USA (4 steps, ~2 min) daily pipelines.

Chat-discovered tickers auto-inserted into stock_master for
pipeline pickup (scheduler refreshes them daily).

---

## Key Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| FastAPI | 0.135 | REST API |
| Next.js | 16.1 | Frontend |
| LangChain | 1.2 | Agent framework |
| Prophet | 1.3 + CmdStanPy 1.3 | Forecasting |
| SQLAlchemy | 2.0 async | ORM (asyncpg) |
| PyIceberg | 0.11 | Table management |
| DuckDB | 1.2 | Iceberg read engine |
| torch (CPU) | latest | FinBERT inference (Docker: CPU wheel) |
| transformers | latest | FinBERT model (ProsusAI/finbert) |
| SWR | 2.3 | Frontend data hooks |
| lightweight-charts | 5.1 | TradingView |

---

## File Counts

Python: 374 | TypeScript/TSX: 98 | Tests: 96+54 | Docs: 56 pages | Scripts: 28+1 POC

---

## Quick Start

```bash
cp .env.example .env && ./run.sh start
docker compose exec backend python scripts/seed_demo_data.py
# http://localhost:3000 → admin@demo.com / Admin123!
```
