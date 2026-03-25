# Project Index: ai-agent-ui

> AI-agent-optimised codebase map. For human onboarding, see `docs/`.
> Last refreshed: 2026-03-26 (Sprint 3 complete)

## Directory Structure

```
ai-agent-ui/
├── backend/          # FastAPI + LangChain + LangGraph (24 modules)
│   ├── agents/       # LangGraph supervisor + 4 sub-agents (27 files)
│   └── tools/        # LangChain tools — stock, forecast, portfolio, news (25 files)
├── auth/             # JWT + OAuth PKCE + RBAC + Razorpay/Stripe (33 files)
│   ├── endpoints/    # Route handlers (auth, subscription, admin, profile)
│   └── repo/         # IcebergUserRepository (CRUD, audit, OAuth)
├── stocks/           # Iceberg data layer — 11 tables (8 files)
├── frontend/         # Next.js 16 + React 19 SPA (14 pages, 21 components)
│   ├── components/   # Charts (7), widgets (9), admin (2), insights (2)
│   └── hooks/        # 16 custom hooks (auth, data, chat, WS)
├── e2e/              # Playwright — 49 specs + fixtures + POMs
├── tests/            # pytest — 45 files, 548 tests
├── scripts/          # Automation (27 files)
│   └── perf/         # Performance audit modules (10 files)
├── docs/             # MkDocs Material (31 .md, 13 dirs)
├── perf-baselines/   # Sprint performance baselines (JSON)
└── .github/workflows # CI (lighthouse.yml, ci.yml, e2e.yml)
```

## Entry Points

| Service | Port | File | Stack |
|---------|------|------|-------|
| Backend | 8181 | `backend/main.py` | FastAPI, LangChain 1.2, LangGraph 1.0 |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19 |
| Docs | 8000 | `mkdocs.yml` | MkDocs Material |
| Launcher | — | `./run.sh` | Bash (start/stop/status/restart) |

## Backend Core Modules

| Module | Purpose |
|--------|---------|
| `routes.py` | HTTP handlers, CORS, security headers, TracingMiddleware |
| `ws.py` | WebSocket `/ws/chat` with auth-first protocol |
| `llm_fallback.py` | N-tier Groq cascade + Anthropic fallback |
| `token_budget.py` | Sliding-window TPM/RPM per model |
| `message_compressor.py` | 3-stage compression (prompt, history, tools) |
| `observability.py` | Tier health, cascade counts, Iceberg persistence |
| `cache.py` | Redis-backed response cache (22 endpoints) |
| `subscription_config.py` | Tier quotas, usage tracking |
| `agents/graph.py` | LangGraph StateGraph supervisor |
| `agents/loop.py` | Sync agentic tool-calling loop |
| `agents/stream.py` | Streaming NDJSON event emitter |

## Auth Architecture

| File | Purpose |
|------|---------|
| `tokens.py` | JWT create/validate/revoke (JTI deny-list) |
| `dependencies.py` | `get_current_user`, `require_tier`, `check_usage_quota` |
| `endpoints/auth_routes.py` | Login, refresh, logout, password reset |
| `endpoints/subscription_routes.py` | Razorpay + Stripe checkout/cancel/webhooks |
| `endpoints/ticker_routes.py` | Portfolio CRUD, stock linking |

## Frontend Pages (14 routes)

Public: `/login`, `/auth/oauth/callback`
Authenticated: `/dashboard`, `/analytics`, `/analytics/analysis`,
`/analytics/compare`, `/analytics/insights`, `/analytics/marketplace`,
`/admin`, `/docs`, `/insights`

## Iceberg Tables (11)

`stocks`, `ohlcv`, `metadata`, `fundamentals`, `sentiment`,
`price_targets`, `forecast`, `usage_history`, `portfolio`,
`payment_transactions`, `quarterly`

## Observability

- **LangSmith** — auto-traces LLM calls, tools, LangGraph nodes (EU endpoint)
- **ObservabilityCollector** — tier health, cascade monitoring → Iceberg
- **Performance audit** — `npm run perf:full` (42+ Playwright audit points)
- **Baselines** — `perf-baselines/sprint-N-full.json`

## Key Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| langchain | 1.2.10 | Agent orchestration |
| langgraph | 1.0.10 | StateGraph supervisor |
| langsmith | 0.7.10 | LLM tracing (EU) |
| fastapi | 0.115.1 | Web framework |
| pyiceberg | 0.8.1 | Iceberg tables |
| next | 16.1.6 | React framework |
| lightweight-charts | 5.1.0 | TradingView charts |
| playwright | — | E2E testing |

## Test Coverage

| Suite | Count | Command |
|-------|-------|---------|
| Python (pytest) | 548 | `python -m pytest tests/ -v` |
| Frontend (vitest) | 18 | `cd frontend && npx vitest run` |
| E2E (Playwright) | 219 | `cd e2e && npm test` |
| Performance | 42+ | `cd frontend && npm run perf:full` |

## Configuration

| File | Purpose |
|------|---------|
| `pyproject.toml` | black (79), isort, pytest |
| `.flake8` | flake8 (79), E203/W503 ignored |
| `lighthouserc.js` | LHCI budgets |
| `.bundlewatch.config.json` | JS < 500KB |
| `frontend/next.config.ts` | Bundle analyzer |
| `CLAUDE.md` | Session rules, code standards |
| `PERFORMANCE.md` | Perf workflow docs |
