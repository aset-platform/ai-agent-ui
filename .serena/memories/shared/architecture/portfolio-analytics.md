# Portfolio Analytics — Performance & Forecast Endpoints

## Endpoints
- `GET /v1/dashboard/portfolio/performance?period=6M&currency=INR` — daily returns, P&L, cash-flow-adjusted metrics
- `GET /v1/dashboard/portfolio/forecast?horizon=9&currency=INR` — Prophet-based portfolio forecast

## Cash-Flow-Adjusted Metrics
Portfolio returns strip capital contributions to avoid inflating performance:
- Daily return = (value_today - value_yesterday - cash_in_today) / (value_yesterday + cash_in_today)
- Uses `_safe_float()` with `math.isnan()` guard for Iceberg NULL prices

## Forecast Architecture
- Always fetches 9M from Prophet; client truncates for 3M/6M via horizon picker
- Per-ticker forecasts weighted by portfolio quantity
- Falls back to OHLCV current price if avg_price is missing/zero

## Refresh Flow
- Portfolio Analysis tab: refreshes all holdings in parallel
- Portfolio Forecast tab: refreshes all holdings
- Stock Analysis/Forecast: refreshes selected ticker
- WatchlistWidget: per-ticker refresh button on portfolio rows
- After refresh: both `/dashboard/home` AND `/users/me/portfolio` SWR keys invalidated

## TradingView Chart Components
- `PortfolioChart` — daily P&L line + area
- `PortfolioForecastChart` — forecast with confidence bands
- `ForecastChart` — single-ticker Prophet forecast
- `CompareChart` — multi-ticker normalized comparison
- `useDomDark.ts` — MutationObserver hook for dark mode sync (SSR hydration safe)

## Key Files
- `backend/dashboard_routes.py` — `_build_portfolio_performance()`, `_build_portfolio_forecast()`
- `backend/tools/portfolio_tools.py` — `_current_price()` (NaN-safe)
- `frontend/components/charts/` — TradingView chart components