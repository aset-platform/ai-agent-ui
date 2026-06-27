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

## Live SMA columns (added 2026-06-23)

During market hours (`_is_indian_market_hours()`), `insights_routes.py` appends a synthetic LTP bar
and computes intraday indicators. Three new fields on `WatchlistStockRow`:
- `current_sma_200`, `current_sma_50`, `current_sma_20: float | None`

Frontend shows `prev | current` in each SMA cell (blue pipe-separated live value).
Filter chips (Golden Cross, LTP > SMA 50, LTP > SMA 200) use **prev-day close** (`_ltp_close = _safe(last["Close"])`),
NOT the live LTP — keeps chips stable intraday.

## Default filter state (set 2026-06-24 to match Abhay's working config)

On page load: RSI(2) ≤ 25, ATR 2–6%, Sharpe ≥ 1, RS ≥ 25%,
Dist SMA200 = ["gt5lte15", "gt15lte35", "gt35lte50"],
Golden Cross + LTP > SMA 50 + LTP > SMA 200 = all ON.

## Add to Strategy modal (`AddToStrategyModal.tsx`, added 2026-06-24)

`frontend/components/algo-trading/AddToStrategyModal.tsx`
"Add to Strategy" button in Watchlist Stocks toolbar (disabled when 0 filtered results).

Flow:
1. Strategy dropdown — **live + non-archived only** (`s.mode === "live" && !s.archived_at`)
2. Two columns: existing `allowed_tickers` (left) vs current filter tickers (right); duplicates amber, new green
3. Merge button → deduped union sorted alphabetically
4. Final chip list (green = newly added) + Save button → `upsertLiveCaps`
5. Back button to revise before saving; Escape/backdrop closes without saving

Preserves all other caps fields (max_inr, max_orders_per_day, gtt_limit_headroom_pct) on save.
