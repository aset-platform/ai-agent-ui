# Dash-to-Next.js Migration — COMPLETE (Mar 19, 2026)

## Status: ALL PAGES MIGRATED. Dash service retired.

## Migrated Pages
| Old Dash Route | New Next.js Route | Ticket |
|---------------|-------------------|--------|
| /home | /dashboard (Portfolio) | Sprint 1 |
| /analysis | /analytics/analysis (TradingView charts) | Sprint 1 |
| /forecast | /analytics/analysis?tab=forecast | Sprint 1 |
| /compare | /analytics/analysis?tab=compare | Sprint 1 |
| /marketplace | /analytics/marketplace | Sprint 1 |
| /insights (7 tabs) | /analytics/insights | ASETPLTFRM-112 |
| /admin (3 tabs) | /admin | ASETPLTFRM-113 |

## What Was Removed (ASETPLTFRM-114)
- DASHBOARD_URL from frontend config
- Dashboard service from run.sh (gunicorn, port 8050)
- Dash link detection in ChatPanel + MarkdownContent
- 4 services now: redis, backend, frontend, docs
- dashboard/ directory kept as archive

## Chart Migration
- Plotly Dash → TradingView lightweight-charts v5 (~45KB)
- 4-pane: Candlestick + Volume + RSI + MACD
- D/W/M interval, indicator toggles, OHLC legend
- Plotly kept for: forecast (fill-between), correlation heatmap, insights bars

## Architecture Difference
- Dash: in-process data (0 hops, 5-min TTL cache)
- Next.js: Browser → FastAPI → Redis/Iceberg (2 hops)
- Mitigated by: Redis write-through cache + SWR + aggregate endpoint
