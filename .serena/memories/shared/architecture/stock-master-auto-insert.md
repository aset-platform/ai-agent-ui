# stock_master Auto-Insert from Chat Agent

## Problem
When chat agent analyses a new ticker (e.g. NVDA, PLTR) via yfinance, the data goes into Iceberg tables (ohlcv, company_info) but NOT into `stock_master` PG table. This means:
- Pipeline scheduler never picks up the ticker for daily refresh
- No Piotroski scoring, sentiment, forecast updates
- Data goes stale after initial chat fetch
- Missing from recommendation engine Stage 1 pre-filter

## Solution (Apr 13, 2026)
`_ensure_stock_master(ticker, info=None)` in `backend/tools/stock_data_tool.py`:
- Upserts into stock_master after successful yfinance fetch
- Called from `fetch_stock_data` (basic entry) and `get_stock_info` (enriched with sector/industry/market_cap)
- Uses async NullPool + thread offload pattern
- Detects market via `detect_market()` — sets exchange=US/NSE, currency=USD/INR
- If row exists, updates metadata if richer (sector, industry, market_cap, name)

## Call Sites
- `fetch_stock_data()` line ~343 — after `repo.insert_ohlcv()`, only if `not master` (new ticker)
- `get_stock_info()` line ~501 — after `repo.insert_company_info()`, with full yfinance info dict

## Impact
Chat-discovered tickers now automatically:
- Appear in pipeline batch jobs (OHLCV refresh, analytics, forecast, sentiment)
- Get Piotroski F-Score computed
- Eligible for Smart Funnel recommendations
- Show index tags if applicable
