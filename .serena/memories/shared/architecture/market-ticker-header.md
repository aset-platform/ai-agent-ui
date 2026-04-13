# Market Ticker — Header Inline Component

## Overview
Real-time Nifty 50 + Sensex ticker in AppHeader center gap.
30s polling during market hours, PG-persisted data off-hours.

## Files
- `backend/market_routes.py` — endpoint + NSE/Yahoo fetchers + cache/PG logic
- `backend/db/models/market_index.py` — `MarketIndex` ORM (single-row, stocks schema)
- `frontend/components/MarketTicker.tsx` — ticker display + 30s polling
- `frontend/components/AppHeader.tsx` — mounts `<MarketTicker />` in center

## Data Sources
- **NSE India** `/api/allIndices` — primary for Nifty 50 (cookie session, httpx)
- **Yahoo Finance** v7 quote `^BSESN` — Sensex (cookie + crumb auth)
- **Yahoo fallback** `^NSEI` — Nifty fallback when NSE fails

## Caching
- Redis key: `market:indices`, TTL 30s (open) / 300s (closed)
- PG table: `stocks.market_indices` (single row, id=1 check constraint)
- Fallback: Redis → PG → upstream → stale PG → 503

## Market Hours
- `_is_market_open()`: Mon-Fri 09:00-15:30 IST
- Off-hours: zero upstream calls (serves PG data)
- First-call-of-day: seeds PG once even off-hours if `fetched_at` is stale

## API
```
GET /v1/market/indices (JWT required)
→ { nifty: IndexData, sensex: IndexData, market_state, timestamp, stale }
```

## Frontend
- `hidden md:flex` — hidden on mobile
- Uses `${API_URL}/market/indices` via `apiFetch`
- Shows change % always; appends "Closed" label off-hours
