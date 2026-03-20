# Project Index: AI Agent UI

> Generated: 2026-03-17 | 304 source files | 77 test files | Python + TypeScript

---

## 📁 Project Structure

```
ai-agent-ui/
├── backend/              # FastAPI API server (51 files, port 8181)
│   ├── agents/           # LangChain agent loop (9)
│   ├── tools/            # Stock analysis tools (22)
│   ├── dashboard_routes.py  # 12 dashboard/chart endpoints
│   ├── audit_routes.py      # Chat audit endpoints
│   └── *.py              # Routes, models, config, WS, observability
├── auth/                 # JWT + RBAC + OAuth PKCE (32 files)
│   ├── endpoints/        # 7 route modules (~35 endpoints)
│   ├── repo/             # Iceberg data access (8)
│   └── models/           # Request/response schemas (2)
├── stocks/               # Iceberg data layer (8 files, 13 tables)
├── dashboard/            # Plotly Dash — LEGACY (45 files, port 8050)
│   ├── callbacks/        # 26 callback modules
│   └── layouts/          # 13 page layouts
├── frontend/             # Next.js 16 + React 19 (56 files, port 3000)
│   ├── app/              # App Router pages (14 routes)
│   ├── components/       # UI components (23) + charts (2)
│   ├── hooks/            # Custom hooks (11)
│   ├── providers/        # ChatProvider, LayoutProvider (2)
│   └── lib/              # apiFetch, auth, config, types (6)
├── tests/                # Backend + Dash tests (54 files)
├── e2e/                  # Playwright E2E (23 specs)
├── docs/                 # MkDocs Material (28 .md files)
├── scripts/              # Data seeding, docs gen (12)
└── hooks/                # Git pre-commit/pre-push (3)
```

## 🚀 Entry Points

| Service | Port | Entry | Stack |
|---------|------|-------|-------|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19, react-plotly.js |
| Dashboard | 8050 | `dashboard/app.py` | Plotly Dash (legacy — Insights + Admin only) |
| Docs | 8000 | `mkdocs serve` | MkDocs Material |

```bash
./run.sh start      # all services
./run.sh status     # health check
./run.sh doctor     # diagnose issues
source ~/.ai-agent-ui/venv/bin/activate
```

## 🗂️ Frontend Routes

```
/                              → redirect to /dashboard
/login                         → login page
/dashboard                     → Portfolio (native widgets)
/analytics                     → Dashboard Home (stock cards)
/analytics/analysis            → Tabbed: Analysis + Forecast + Compare
/analytics/compare             → Compare stocks (react-plotly.js)
/analytics/insights            → Insights (Dash iframe — pending)
/analytics/marketplace         → Link Ticker (native)
/docs                          → MkDocs (iframe)
/admin                         → Admin (Dash iframe — pending)
```

**Sidebar**: Portfolio, Dashboard ▾ (Home, Analysis, Insights, Link Ticker), Docs, Admin

## 🔌 API Endpoints (~55 total)

| Group | Count | Prefix | Key Endpoints |
|-------|-------|--------|---------------|
| Core | 4 | `/v1/` | chat, chat/stream, agents, health |
| Dashboard | 9 | `/v1/dashboard/` | watchlist, forecasts, analysis, llm-usage, registry, compare |
| Chart data | 3 | `/v1/dashboard/chart/` | ohlcv, indicators, forecast-series |
| Audit | 2 | `/v1/audit/` | chat-sessions (POST + GET) |
| Auth | 7 | `/v1/auth/` | login, logout, refresh, register |
| Users | 5 | `/v1/users/` | CRUD (superuser) |
| Profile | 3 | `/v1/auth/me` | get, update, avatar |
| OAuth | 3 | `/v1/auth/oauth/` | providers, authorize, callback |
| Sessions | 3 | `/v1/auth/sessions/` | list, revoke, revoke-all |
| Tickers | 3 | `/v1/users/me/tickers/` | list, link, unlink |
| Admin | 4 | `/v1/admin/` | metrics, tier-health, tier-toggle, retention |
| Bulk | 2 | `/v1/bulk-` | import, export |
| WebSocket | 1 | `/ws/chat` | Streaming agent responses |

## 📦 Core Modules

### Backend
- `routes.py` — Router registration (v1, auth, dashboard, audit, bulk, WS)
- `dashboard_routes.py` — 12 endpoints for widgets + charts
- `dashboard_models.py` — 20 Pydantic models
- `llm_fallback.py` — N-tier Groq cascade + Anthropic fallback
- `observability.py` — LLM usage tracking to Iceberg
- `agents/loop.py` + `stream.py` — Agentic execution + NDJSON streaming
- `agents/report_builder.py` — Deterministic report synthesis

### Frontend
- `providers/ChatProvider.tsx` — Messages, WS, panel state, audit flush
- `providers/LayoutProvider.tsx` — Sidebar, mobile menu
- `components/charts/PlotlyChart.tsx` — SSR-safe chart wrapper (auto dark/light)
- `components/charts/chartBuilders.ts` — Price, RSI, MACD, comparison, forecast builders
- `hooks/useDashboardData.ts` — Generic API fetcher with loading/error states
- `lib/apiFetch.ts` — JWT auto-refresh HTTP client

### Data Layer
- `stocks/repository.py` — 42 public methods, 13 Iceberg tables
- Key tables: ohlcv, technical_indicators, analysis_summary, forecast_runs, forecasts, llm_usage, chat_audit_log

## 🧪 Tests

| Suite | Files | Tests | Command |
|-------|------:|------:|---------|
| Backend | 47 | 330+ | `python -m pytest tests/ -v` |
| Frontend | 7 | 22 | `cd frontend && npx vitest run` |
| E2E | 23 | ~91 | `cd e2e && npm test` |

## 🔗 Key Dependencies

**Python**: FastAPI, LangChain, Groq, Anthropic, Prophet, PyIceberg, Redis, Argon2
**Node**: Next.js 16, React 19, Tailwind CSS 4, react-plotly.js, react-markdown, vitest

## 🔧 Configuration

| File | Purpose |
|------|---------|
| `pyproject.toml` | Black (79 chars), isort, pytest, flake8 |
| `CLAUDE.md` | Claude Code project instructions |
| `mkdocs.yml` | Docs site + auto-gen plugins |
| `.pyiceberg.yaml` | Iceberg catalog config |
| `backend/.env` → `~/.ai-agent-ui/backend.env` | Backend secrets (symlink) |

## 📋 Sprint Status

**Sprint 2** (due 2026-03-20) — branch: `feature/sprint2-planning`
- ✅ ASETPLTFRM-82 to 111: Dashboard overhaul + Dash migration (30 tickets Done)
- ⬜ ASETPLTFRM-112: Insights migration (8 SP)
- ⬜ ASETPLTFRM-113: Admin migration (5 SP)
- ⬜ ASETPLTFRM-114: Retire Dash service (2 SP)
