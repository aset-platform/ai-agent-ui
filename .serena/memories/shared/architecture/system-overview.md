# System Architecture Overview

## Services

| Service | Port | Entry point | Stack |
|---------|------|-------------|-------|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19, TypeScript |
| Dashboard | 8050 | `dashboard/app.py` | Plotly Dash (FLATLY theme) — being migrated to Next.js |
| Docs | 8000 | `mkdocs serve` | MkDocs Material |

## Frontend Architecture (Post-Overhaul)

### Route Structure
```
frontend/app/
├── layout.tsx                         (root — fonts, dark mode script)
├── (authenticated)/                   (route group — shared layout)
│   ├── layout.tsx                     (AppLayout: sidebar + header + chat + FAB)
│   ├── dashboard/page.tsx             (Portfolio — native widgets)
│   ├── analytics/page.tsx             (Dashboard Home — stock cards)
│   ├── analytics/analysis/page.tsx    (Tabbed: Analysis+Forecast+Compare)
│   ├── analytics/compare/page.tsx     (Compare — also embedded in Analysis tab)
│   ├── analytics/insights/page.tsx    (Insights — Dash iframe, pending migration)
│   ├── analytics/marketplace/page.tsx (redirects to /analytics)
│   ├── docs/page.tsx                  (MkDocs iframe)
│   └── admin/page.tsx                 (Admin — Dash iframe, pending migration)
├── login/page.tsx
└── auth/oauth/callback/page.tsx
```

### State Management
- ChatProvider: messages, panel open/close, agentId, sessionId, WebSocket
- LayoutProvider: sidebar collapsed, mobile menu
- No Redux/Zustand — contexts + prop-drilling

### Sidebar Navigation
```
Portfolio           → /dashboard (native)
Dashboard ▾         → collapsible group
  ├─ Home           → /analytics (native)
  ├─ Analysis       → /analytics/analysis (native, tabbed)
  ├─ Insights       → /analytics/insights (Dash iframe → pending migration)
  └─ Insights       → /analytics/insights (native)
Docs                → /docs (MkDocs iframe)
Admin               → /admin (native — 6 tabs: Users, Audit, LLM Obs, Maintenance, Transactions, Scheduler)
```

### Charts
- react-plotly.js with dynamic import (ssr: false)
- PlotlyChart wrapper with auto dark/light theming
- Unified subplot chart for Analysis (Price+Volume+RSI+MACD shared x-axis)
- Chart builders in `frontend/components/charts/chartBuilders.ts`

## Backend API Endpoints

### Dashboard Endpoints (new)
- GET /v1/dashboard/watchlist — user's linked tickers with prices
- GET /v1/dashboard/forecasts/summary — latest forecast targets
- GET /v1/dashboard/analysis/latest — analysis signals
- GET /v1/dashboard/llm-usage — LLM cost/latency/model breakdown
- GET /v1/dashboard/registry — all registered tickers
- GET /v1/dashboard/compare?tickers=X,Y,Z — normalized comparison
- GET /v1/dashboard/chart/ohlcv?ticker=X — OHLCV time series
- GET /v1/dashboard/chart/indicators?ticker=X — technical indicators
- GET /v1/dashboard/chart/forecast-series?ticker=X&horizon=9

### Audit Endpoints (new)
- POST /v1/audit/chat-sessions — save chat transcript on logout
- GET /v1/audit/chat-sessions — list past sessions (filtered by user)

## Iceberg Tables (15)
stocks.registry, stocks.company_info, stocks.ohlcv, stocks.dividends,
stocks.technical_indicators, stocks.analysis_summary, stocks.forecast_runs,
stocks.forecasts, stocks.quarterly_results, stocks.llm_pricing,
stocks.llm_usage, stocks.chat_audit_log,
stocks.portfolio_transactions, stocks.scheduled_jobs (new),
stocks.scheduler_runs (new)
