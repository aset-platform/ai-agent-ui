# Project Index: ai-agent-ui

> AI-agent-optimised codebase map. For human onboarding, see `docs/`.
> Last refreshed: 2026-03-28 (Sprint 3 + Sentiment Agent)

---

## Project Structure

```
ai-agent-ui/
├── backend/              # FastAPI + LangChain + LangGraph (Python 3.12)
│   ├── main.py           # ASGI entry, ChatServer, startup wiring
│   ├── config.py         # Pydantic Settings (env-based)
│   ├── routes.py         # HTTP chat + streaming endpoints
│   ├── ws.py             # WebSocket /ws/chat
│   ├── bootstrap.py      # Tool + agent + graph registration
│   ├── llm_fallback.py   # N-tier Groq cascade + Anthropic fallback
│   ├── token_budget.py   # Sliding-window TPM/RPM tracker
│   ├── message_compressor.py  # 3-stage context compression
│   ├── observability.py  # Tier health, cascade counts → Iceberg
│   ├── tracing.py        # LangSmith + LangFuse setup
│   ├── agents/           # LangGraph supervisor + 5 sub-agents
│   │   ├── graph.py      # 11-node StateGraph builder
│   │   ├── sub_agents.py # Factory + dynamic context injection
│   │   ├── configs/      # portfolio, stock_analyst, forecaster, research, sentiment
│   │   └── nodes/        # guardrail, router, llm_classifier, supervisor, synthesis, log_query, decline
│   ├── tools/            # 26 LangChain @tool modules
│   │   ├── stock_data_tool.py      # 7 stock data tools
│   │   ├── price_analysis_tool.py  # Technical analysis + chart
│   │   ├── forecasting_tool.py     # Prophet forecast pipeline
│   │   ├── news_tools.py           # Tiered news (yfinance → RSS → SerpAPI)
│   │   ├── portfolio_tools.py      # 7 portfolio tools
│   │   ├── sentiment_agent.py      # 3 sentiment tools
│   │   ├── _sentiment_sources.py   # 3-source headline fetcher + dedup
│   │   ├── _sentiment_scorer.py    # FallbackLLM scoring + weighted avg
│   │   ├── _forecast_model.py      # Prophet training
│   │   ├── _forecast_ensemble.py   # XGBoost residual correction
│   │   └── _forecast_shared.py     # Regressor loading from Iceberg
│   ├── jobs/             # Background schedulers
│   │   ├── gap_filler.py           # Daily: gaps, indices, sentiment, purge
│   │   ├── scheduler_service.py    # Admin UI scheduler
│   │   └── executor.py             # Job execution engine
│   ├── dashboard_routes.py  # /v1/dashboard/*
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
│   ├── app/              # App Router pages
│   │   ├── (authenticated)/dashboard/    # Portfolio dashboard
│   │   ├── (authenticated)/analytics/    # Unified analytics
│   │   ├── (authenticated)/admin/        # Admin panel
│   │   └── login/
│   ├── components/       # 44 React components
│   │   ├── widgets/      # HeroSection, WatchlistWidget, ForecastChartWidget
│   │   ├── charts/       # StockChart, ForecastChart, PortfolioChart, CompareChart, CorrelationHeatmap
│   │   ├── admin/        # SchedulerTab, UserModal
│   │   └── insights/     # InsightsTable, InsightsFilters
│   ├── hooks/            # 19 custom hooks
│   └── lib/              # apiFetch, config, types, auth, constants
├── scripts/              # 22 utility scripts
├── tests/backend/        # 49 test files, 613 test cases
├── e2e/                  # Playwright E2E (~219 tests)
├── docs/                 # MkDocs Material
│   ├── design/           # Architecture specs
│   └── workflow/         # Implementation plans
└── dashboard/            # Legacy Dash app (deprecated)
```

## Entry Points

| Entry | Path | Port |
|-------|------|------|
| Backend | `backend/main.py` | 8181 |
| Frontend | `frontend/app/page.tsx` | 3000 |
| Docs | `mkdocs.yml` | 8000 |
| Launcher | `./run.sh start` | all |

## Iceberg Tables (20)

### stocks (15 tables, ~336K rows)
`registry` (52) · `company_info` (62) · `ohlcv` (152K) · `dividends` (1.6K) · `technical_indicators` (130K) · `analysis_summary` (57) · `forecast_runs` (57) · `forecasts` (52K) · `quarterly_results` (653) · `sentiment_scores` (47) · `llm_pricing` (0) · `llm_usage` (0) · `scheduled_jobs` (2) · `scheduler_runs` (2) · `portfolio_transactions` (8)

### auth (5 tables)
`users` (5) · `user_tickers` (11) · `audit_log` (1) · `payment_transactions` (0) · `usage_history` (0)

## LangGraph Supervisor (5 sub-agents)

```
START → guardrail → router → [llm_classifier] → supervisor
  → portfolio | stock_analyst | forecaster | research | sentiment
  → synthesis → log_query → END
```

| Agent | Purpose |
|-------|---------|
| portfolio | Currency-aware holdings, performance, risk metrics |
| stock_analyst | Technical analysis pipeline (fetch → analyse → verdict) |
| forecaster | Prophet + XGBoost ensemble forecasting |
| research | Tiered news search (yfinance → RSS → SerpAPI) |
| sentiment | 3-source headline scoring, market mood, hybrid cached/live |

## LLM Cascade

`llama-3.3-70b → kimi-k2 → gpt-oss-120b → scout-17b → claude-sonnet-4.6`

All via FallbackLLM + TokenBudget + MessageCompressor + LangSmith tracing.

## Background Jobs (gap_filler.py)

| UTC | Job |
|-----|-----|
| 05:30 | Market indices (VIX, GSPC, TNX, CL=F, DX-Y.NYB) |
| 06:00 | Sentiment (all tickers, FallbackLLM, 3 sources) |
| 12:30 | Data gaps (after NSE close) |
| 15:30 | Data gaps (after NYSE close) |
| Sun 04:00 | Purge (>11Y data, expire Iceberg snapshots) |

## Configuration

| File | Purpose |
|------|---------|
| `~/.ai-agent-ui/backend.env` | Master env (secrets, API keys) |
| `backend/config.py` | Pydantic Settings |
| `pyproject.toml` | black, isort, pytest |
| `.flake8` | 79 char line, exclude demoenv |
| `frontend/.env.local` | NEXT_PUBLIC_BACKEND_URL |

## Key Dependencies

**Backend**: FastAPI, LangChain 1.2.10, LangGraph 1.0.10, LangSmith 0.7.10, Prophet, XGBoost, PyIceberg, yfinance, feedparser, Razorpay SDK, Stripe SDK

**Frontend**: Next.js 16, React 19, Tailwind CSS 4, lightweight-charts (TradingView), ECharts, react-plotly.js

## Quick Start

```bash
source ~/.ai-agent-ui/venv/bin/activate
./run.sh start                                    # all services
python -m pytest tests/ -v                        # 613 tests
PYTHONPATH=backend python scripts/check_tables.py # Iceberg health
PYTHONPATH=backend python scripts/seed_demo_data.py # first run
```

## Sprint 3 (94+ SP, all Done)

Sentiment Agent (16 SP) · Unified Analytics (8) · Admin Scheduler (13) · Correlation Heatmap (5) · Security Hardening (24 fixes) · E2E Coverage (46 tests) · LangSmith Observability · Lighthouse Performance (45→87)
