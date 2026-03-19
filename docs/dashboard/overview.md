# Dashboard (Retired)

!!! warning "Dash Service Retired"
    The Plotly Dash dashboard has been fully migrated to native Next.js pages.
    The `dashboard/` directory is kept as archive for reference only.
    All pages are now served by the Next.js frontend at `http://localhost:3000`.

## Migrated Pages

| Old Dash Route | New Next.js Route | Status |
|---------------|-------------------|--------|
| `/home` | `/dashboard` (Portfolio) | Native |
| `/analysis` | `/analytics/analysis` | Native (TradingView charts) |
| `/forecast` | `/analytics/analysis?tab=forecast` | Native |
| `/compare` | `/analytics/analysis?tab=compare` | Native |
| `/marketplace` | `/analytics/marketplace` | Native |
| `/insights` | `/analytics/insights` (7 tabs) | Native |
| `/admin` | `/admin` (3 tabs) | Native |

## Architecture Changes

- **Charts**: Plotly Dash → TradingView lightweight-charts v5 (~45KB)
- **Data**: Direct Iceberg reads → FastAPI endpoints + Redis cache
- **Auth**: Query param token → JWT via `apiFetch` auto-refresh
- **Caching**: In-process TTL dicts → Redis write-through + SWR browser cache
- **Service**: `run.sh` no longer starts Dash (4 services: redis, backend, frontend, docs)
