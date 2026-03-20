# Per-Ticker Refresh Pipeline (Dashboard)

## Backend API
- `POST /v1/dashboard/refresh/{ticker}` — starts background refresh
- `GET /v1/dashboard/refresh/{ticker}/status` — polls status (idle/pending/success/error)

## Implementation
- Uses `RefreshManager` from `dashboard/callbacks/refresh_state.py` (ThreadPoolExecutor, max 2 workers)
- Calls `run_full_refresh(ticker, horizon_months=9)` from `dashboard/services/stock_refresh.py`

## 6-Step Pipeline (`run_full_refresh`)
1. **OHLCV fetch** — full re-fetch from yfinance, Iceberg dedup on (ticker, date) — CRITICAL
2. **Company info** — `stock_data_tool.get_stock_info()` — non-critical
3. **Dividends** — `stock_data_tool.get_dividend_history()` — non-critical
4. **Technical analysis** — `price_analysis_tool.analyse_stock_price()` — non-critical
5. **Quarterly results** — `stock_data_tool.fetch_quarterly_results()` — non-critical
6. **Prophet forecast** — trains model, computes MAE/RMSE/MAPE, saves to forecast_runs + forecasts — CRITICAL

## Freshness Gates (Skip Conditions)
- **OHLCV**: skipped if `latest_date >= date.today()` — only skip if today's data exists. Do NOT use `today - 1 day` — that skips fetches when yesterday's close is the latest and today's market data is available.
- **Technical analysis**: skipped if analysis_date == today (inside `analyse_stock_price`)
- **Forecast**: skipped if last run < 7 days old (Prophet training is expensive)

## Frontend Refresh Buttons

### WatchlistWidget (Dashboard)
Per-ticker refresh icon on each row (both Portfolio and Watchlist tabs):
- ↻ (idle) → spinner (pending, polls every 2s) → ✓ (success, 3s) → ↻
- On success: calls `onRefresh()` to reload dashboard data via SWR mutate
- On error: shows ✗ for 5s then resets

### Portfolio Analysis Tab
Single refresh button in chart header (next to period pills):
- Fetches all portfolio tickers from `GET /users/me/portfolio`
- Triggers `POST /dashboard/refresh/{ticker}` for each holding in parallel
- Polls all tickers until all complete
- On success: increments `refreshKey` → `useEffect` re-fetches performance data

### Portfolio Forecast Tab
Same pattern as Portfolio Analysis — refreshes all holdings, re-fetches both performance + forecast data via `fcRefreshKey`.

### Stock Analysis / Stock Forecast Tabs
Single refresh button next to ticker dropdown:
- Refreshes the currently selected ticker only
- On success: increments `tickerRefreshKey` → both AnalysisTab and ForecastTab re-mount via React `key` prop

## Cache Invalidation
On successful refresh, the backend status endpoint invalidates all related cache keys:
`cache:dash:*`, `cache:chart:ohlcv:{ticker}`, `cache:chart:indicators:{ticker}`,
`cache:chart:forecast:{ticker}:*`, `cache:insights:*`
