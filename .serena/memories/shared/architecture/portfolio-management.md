# Portfolio Management Framework

## Status: Full analytics delivered (Mar 20, 2026) — ASETPLTFRM-124, 125

## Data Model
Append-only transactions table: `stocks.portfolio_transactions`
- transaction_id (UUID), user_id, ticker, side (BUY/SELL/DIVIDEND/SPLIT)
- quantity, price, currency (USD/INR), market (us/india)
- trade_date, fees, notes, created_at
- All fields optional (PyArrow compat — required=False in Iceberg schema)

## Current Holdings (computed on read)
GROUP BY (user_id, ticker), SUM quantities, weighted avg price.
Only tickers from registry allowed (MVP). Enriched with current price from OHLCV.

## API Endpoints
- GET /v1/users/me/portfolio — computed holdings + totals per currency
- POST /v1/users/me/portfolio — add BUY transaction
- PUT /v1/users/me/portfolio/{transaction_id} — edit price/qty/date
- DELETE /v1/users/me/portfolio/{transaction_id} — remove
- **GET /v1/dashboard/portfolio/performance** — daily value + invested series, cash-flow-adjusted metrics
- **GET /v1/dashboard/portfolio/forecast** — weighted aggregation of per-ticker Prophet forecasts

## Portfolio Performance Endpoint
Query params: `period` (1D|1W|1M|3M|6M|1Y|ALL), `currency` (USD|INR)
- Computes daily `value` (market) and `invested_value` (cumulative cost basis) per date
- Trade-date aware: each lot only counts from its trade_date onward
- **Cash-flow-adjusted metrics**: daily return strips capital contributions
  - `pnl = value_today - value_yesterday - cashflow` where `cashflow = invested_today - invested_yesterday`
  - Total return: `(last_value - last_invested) / last_invested`
  - Max drawdown: tracks gain% series `(value - invested) / invested`, not raw value
  - Sharpe/best/worst day use corrected daily returns
- NaN-safe: `_safe_float()` handles NULL Iceberg prices, falls back to OHLCV close
- Cache: `cache:portfolio:perf:{user_id}:{currency}:{period}`, TTL_VOLATILE (60s)

## Portfolio Forecast Endpoint
Query params: `horizon` (3|6|9), `currency` (USD|INR)
- Always fetches 9M forecasts internally (horizon param ignored for Iceberg query)
- Client-side truncation: `maxPoints = ceil(data.length * horizon / 9)`
- Returns `total_invested` from holdings (with NaN fallback to current OHLCV price)
- Cache: `cache:portfolio:forecast:{user_id}:{currency}:{horizon}`, TTL_STABLE (300s)

## Frontend — Analysis Page Tabs
Order: Portfolio Analysis → Portfolio Forecast → Stock Analysis → Stock Forecast → Compare Stocks
- Tab style: underline (matches Insights/Admin pages)
- Tab preference persisted via `usePreferences("chart", { tab })` — remembers last tab

### Portfolio Analysis Tab
- Period selector pills (1D–ALL), currency from preferences
- TradingView `PortfolioChart.tsx`: AreaSeries (market value, indigo) + LineSeries (invested, amber dashed 2px) + HistogramSeries (daily P&L green/red)
- Crosshair tooltip: date, value, invested, gain/loss %, daily P&L
- 6 metrics cards: Total Return, Annualized, Max Drawdown, Sharpe, Best Day, Worst Day
- Legend in chart header: Market Value (solid indigo) + Invested (dashed amber)

### Portfolio Forecast Tab
- Horizon picker (3M/6M/9M) — client-side truncation, no re-fetch
- TradingView `PortfolioForecastChart.tsx`: historical value + invested lines, forecast (dashed green) + confidence band + flat invested projection
- Crosshair tooltip: date, value/predicted, invested, gain/loss %, FORECAST tag
- 4 summary cards: Total Invested, Current Value (with unrealized P&L sub-label), Predicted, Expected Return (on cost)
- Legend in chart header: Market Value, Invested, Forecast

## Cache Invalidation
Portfolio add/edit/delete invalidates: `cache:portfolio:{user_id}`, `cache:portfolio:perf:{user_id}:*`, `cache:portfolio:forecast:{user_id}:*`

## ConfirmDialog (ASETPLTFRM-125)
Reusable `frontend/components/ConfirmDialog.tsx` — danger (red) / warning (amber) variants.
Applied to: delete stock (dashboard), unlink ticker (marketplace), revoke session (individual + all), deactivate user (admin).

## HeroSection Navigation
- "Portfolio Analysis" → /analytics/analysis?tab=portfolio
- "Portfolio Forecast" → /analytics/analysis?tab=portfolio-forecast
- "Link Stock" → /analytics/marketplace

## Future Phases
- Phase 2: Sell transactions, FIFO lot matching, realized P&L
- Phase 3: Dividend tracking, stock splits
- Backlog: Multiple portfolios (Retirement, Trading), benchmark comparison
