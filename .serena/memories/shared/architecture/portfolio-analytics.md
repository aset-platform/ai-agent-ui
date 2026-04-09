# Portfolio Analytics Dashboard Architecture

## Dashboard Layout (Sprint 6)

```
Row 1: [Sector Allocation] [Asset Performance] [Recommendations]  ← 3-col grid
Row 2: [P&L Trend + News (2 cols)] [LLM Usage (1 col)]
Row 3: [Watchlist (wider)] [Analysis Signals]
Row 4: [Forecast Chart — full width]
```

Responsive: xl:3 cols, md:2 cols, sm:1 col stack.
Sidebar defaults to collapsed (62px). Dashboard submenus accessible via hover flyout.

## Backend Endpoints

| Endpoint | Auth | Cache | Market Filter |
|----------|------|-------|---------------|
| `GET /dashboard/portfolio/allocation?market=` | JWT | 300s | Yes |
| `GET /dashboard/portfolio/performance?currency=&period=` | JWT | 60s | Yes (via currency) |
| `GET /dashboard/portfolio/news?market=` | JWT | 900s | Yes |
| `GET /dashboard/portfolio/recommendations?market=` | JWT | 300s | Yes |
| `GET /dashboard/chart/forecast-backtest?ticker=` | JWT | 300s | N/A (per-ticker) |

All new endpoints follow existing dashboard_routes.py patterns: Redis caching, user-scoped keys, Pydantic response models.

## Frontend Components

| Widget | File | Chart Lib | Data Source |
|--------|------|-----------|-------------|
| SectorAllocationWidget | `components/widgets/` | ECharts donut | `/portfolio/allocation` |
| AssetPerformanceWidget | `components/widgets/` | ECharts bar | Portfolio holdings (existing) |
| PLTrendWidget | `components/widgets/` | ECharts area | `/portfolio/performance` (SWR refetch on period change) |
| NewsWidget | `components/widgets/` | None (list) | `/portfolio/news` |
| RecommendationsWidget | `components/widgets/` | None (cards) | `/portfolio/recommendations` |

## ECharts Setup
- Tree-shaken: `frontend/lib/echarts.ts` registers pie, bar, line only (~200KB vs 800KB)
- Dark/light: Asset Performance uses `MutationObserver` on `<html>` class (fixes useTheme hydration lag)
- All charts use `notMerge={true}` + `key` prop for reliable updates via `next/dynamic`

## Forecast Backtest Overlay
- Prophet `df_cv` cross-validation pairs persisted as `horizon_months=0` in existing `forecasts` Iceberg table
- Actual price stored in `lower_bound` column (avoids schema changes)
- Orange dashed line on ForecastChart (TradingView lightweight-charts LineSeries)
- Crosshair tooltip shows backtest predicted + deviation %
- Extended metrics: directional accuracy %, P50/P90 error %, max error %

## Recommendation Engine (Current: Rule-Based)
Uses yfinance sector names (Technology, Financial Services, Healthcare — NOT IT/Financials).
Thresholds: overweight >20%, sector concentration >35%, underperformer <-15% + bearish signals.
To be replaced by LLM-powered engine (ASETPLTFRM-298).

## SWR Hooks (`frontend/hooks/useDashboardData.ts`)
- `useSectorAllocation(market)` 
- `usePortfolioPerformance(market, period)` — period changes trigger refetch
- `usePortfolioNews(market)`
- `useRecommendations(market)`
- `useForecastBacktest(ticker)` — null-key SWR (skips when no ticker)
