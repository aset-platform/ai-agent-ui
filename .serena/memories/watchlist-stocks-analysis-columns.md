# Watchlist Stocks — Analysis Tab Columns & Filters

Added 2026-06-20 on `feature/watchlist-offhours-rsi`.

## Backend: `backend/insights_routes.py` → `_watchlist_stocks()`

### Market hours gate
`_is_indian_market_hours()` (09:00–15:30 IST, Mon–Fri weekdays) — gates `yf.download()`.
Off-hours: `current_rsi_2 = rsi_2` from OHLCV history (no yfinance call).

### Computed columns (all in per-ticker loop)
- **sharpe_ratio**: annualised Sharpe over last 126 bars. `(mean(rets) / std(rets)) * √252`. Min 20 bars.
- **rs_6m**: `stock_6M_return% − nifty_6M_return%`. Nifty fetched once via `query_iceberg_df("stocks.ohlcv", "WHERE ticker='^NSEI' ... LIMIT 135")` before loop.
- **atr_pct**: `ATR_14 / close * 100`.
- **dist_sma200**: `(close − SMA_200) / SMA_200 * 100`.
- **score**: computed POST-loop; all inputs cross-stock percentile-ranked (0–100):
  `0.5×SharpePercentile + 0.3×RSPercentile + 0.2×ATRPercentile`

### Model: `backend/insights_models.py` → `WatchlistStockRow`
Fields: ticker, market, close, rsi_2, current_rsi_2, sma_200, sma_50, sma_20, sharpe_ratio, atr_pct, rs_6m, dist_sma200, score.

## Frontend: `frontend/app/(authenticated)/analytics/analysis/page.tsx`

### Filter types
- `AtrFilter`: lt0 / gt0lte1_5 / gt1_5lte4 / gt2lte5 / gt2lte6 / gt5
- `SharpeFilter`: lte0 / gt0lte080 / gt080lte2 / gt080lte10 / gt1
- `RsFilter`: lt25 / gte25
- `DistSma200Filter = DistSma200Bucket[]` (multi-select, OR logic)
  Buckets: lt0 / gt0lte5 / gt5lte15 / gt15lte35 / gt35lte50 / gt50lte80 / gt80
- All numeric filter comparisons use `Math.round(v * 100) / 100` (2dp) to match display precision

### Layout
Row 1: market toggle + all dropdowns (RSI2, Curr RSI2, ATR%, Sharpe, RS6M, Dist SMA200)
Row 2: toggle chips (Golden Cross, LTP>SMA50, LTP>SMA200)

### Floating tooltip component: `ColumnTooltip`
Portal-based (`createPortal` into `document.body`), `position:fixed`, `getBoundingClientRect()` on hover.
Opens below if <220px from top, above otherwise. Left edge clamped to viewport.
Used on: Sharpe(6M), RS(6M), ATR%, Dist SMA200, Score column headers.

### Multi-select component: `DistSma200MultiSelect`
Custom checkbox dropdown. `min-w-full w-max` panel. Single-line option: `"5% – 15% (Early trend · ✅ Good)"`.
Close on outside click via `mousedown` listener. "Clear all" shortcut.

### SortKey type
"ticker" | "close" | "rsi_2" | "current_rsi_2" | "sma_200" | "sma_50" | "sma_20" | "sharpe_ratio" | "atr_pct" | "rs_6m" | "dist_sma200" | "score"
