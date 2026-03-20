# Dashboard (Retired)

!!! warning "Dash Service Retired"
    The Plotly Dash dashboard has been fully migrated to native Next.js pages.
    The `dashboard/` directory is kept as archive for reference only.
    All pages are now served by the Next.js frontend at `http://localhost:3000`.

## Migrated Pages

| Old Dash Route | New Next.js Route | Status |
|---------------|-------------------|--------|
| `/home` | `/dashboard` (Portfolio) | Native |
| `/analysis` | `/analytics/analysis?tab=analysis` (Stock Analysis) | Native (TradingView) |
| `/forecast` | `/analytics/analysis?tab=forecast` (Stock Forecast) | Native (TradingView) |
| `/compare` | `/analytics/analysis?tab=compare` (Compare Stocks) | Native (TradingView) |
| — | `/analytics/analysis?tab=portfolio` (Portfolio Analysis) | New (TradingView) |
| — | `/analytics/analysis?tab=portfolio-forecast` (Portfolio Forecast) | New (TradingView) |
| `/marketplace` | `/analytics/marketplace` (Link Stock) | Native |
| `/insights` | `/analytics/insights` (7 tabs) | Native |
| `/admin` | `/admin` (3 tabs) | Native |

## Analysis Page — 5 Tabs

1. **Portfolio Analysis** — daily value vs invested (AreaSeries + LineSeries), cash-flow-adjusted metrics
2. **Portfolio Forecast** — weighted Prophet forecast with confidence band, explainable summary cards
3. **Stock Analysis** — multi-pane candlestick (OHLC + Volume + RSI + MACD)
4. **Stock Forecast** — per-ticker Prophet forecast with confidence band
5. **Compare Stocks** — normalized price comparison (multi-line)

## Architecture Changes

- **Charts**: Plotly Dash → TradingView lightweight-charts v5 (~45KB) on all pages except Insights
- **Data**: Direct Iceberg reads → FastAPI endpoints + Redis cache
- **Auth**: Query param token → JWT via `apiFetch` auto-refresh
- **Caching**: In-process TTL dicts → Redis write-through + SWR browser cache
- **Destructive actions**: `ConfirmDialog` component on delete/unlink/revoke/deactivate flows
- **Refresh**: Per-ticker refresh buttons on Portfolio Analysis, Portfolio Forecast, Stock Analysis tabs
- **Dark mode**: `useDomDark` MutationObserver hook ensures TradingView charts match page theme
- **Service**: `run.sh` no longer starts Dash (4 services: redis, backend, frontend, docs)

## Testing

```bash
source ~/.ai-agent-ui/venv/bin/activate   # Python 3.12 required
python -m pytest tests/backend/ -v        # 416+ backend tests
cd frontend && npx vitest run             # 61 frontend tests
```

Note: `~/.ai-agent-ui/venv` is a symlink to `backend/demoenv`. Do NOT use system Python (conda 3.9) — PEP 604 syntax requires 3.12.
