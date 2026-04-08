# Portfolio Analytics Dashboard — Design Spec

**Date:** 2026-04-08
**Sprint:** 6 (2026-04-09 to 2026-04-15)
**Status:** Draft

---

## Context

The dashboard page (`/dashboard`) currently shows portfolio value,
per-ticker holdings, single-ticker signals, LLM usage, and forecasts.
Users lack portfolio-level analytics: sector allocation, asset
performance comparison, P&L trends, news/sentiment, and actionable
rebalancing recommendations. All this data exists in Iceberg tables
and backend services but isn't surfaced on the dashboard.

**Goal:** Add 5 new widget sections above the existing dashboard grid
to provide comprehensive portfolio analytics without leaving the page.
Analytics widgets appear first (portfolio context), then the existing
operational widgets (Hero, Watchlist, Signals, LLM, Forecast).

---

## Architecture

### Layout

```
┌──────────────────┬──────────────────┬──────────────────┐
│ W1: Sector       │ W2: Asset        │ W5: Recommend-   │
│ Allocation       │ Performance      │ ations           │
│ (1 col)          │ (1 col)          │ (1 col)          │
├──────────────────┴──────────────────┴──────────────────┤
│ W3: Portfolio P&L Trend (3 cols — full width)          │
├──────────────────┴─────────────────┬───────────────────┤
│ W4: News & Sentiment (2 cols)      │ (empty / future)  │
├────────────────────────────────────┴───────────────────┤
═════════════════════════════════════════════════════════
[Existing Dashboard Grid — Hero, Watchlist, Signals, LLM, Forecast]
```

- **3-column grid** (`1fr 1fr 1fr`) — replaces the old 2-column layout
- Column spans driven by data density and usage:
  - **Row 1 (1+1+1):** W1, W2, W5 — compact, equal-weight panels
  - **Row 2 (3 cols):** W3 P&L Trend — time-series needs full width
  - **Row 3 (2+1):** W4 News — headlines need more room; third col
    reserved for future widget or collapses on smaller screens
- Widgets sit **above** the existing dashboard grid so users see
  portfolio-level analytics first, then drill into per-ticker details
- Lazy-loaded: widgets use intersection observer for deferred render
- Market filter (India/US) applies to all new widgets
- Responsive breakpoints:
  - **≥1280px:** 3 columns as shown
  - **768–1279px:** 2 columns (W5 wraps below W1+W2)
  - **<768px:** 1 column stack

### Charting Library

**ECharts** via `echarts-for-react` (already in bundle, tree-shaken).
Consistent with existing correlation heatmap. Register only needed
components: pie, bar, line, grid, tooltip, legend.

### Data Flow

```
useDashboardHome() → existing consolidated API
  ↓
New SWR hooks (independent, lazy):
  useSectorAllocation()    → GET /dashboard/portfolio/allocation
  usePortfolioPerformance() → GET /dashboard/portfolio/performance (exists)
  usePortfolioNews()       → GET /dashboard/portfolio/news
  useRecommendations()     → GET /dashboard/portfolio/recommendations
```

Asset performance (W2) uses holdings data already in
`useDashboardHome()` — no new endpoint needed.

---

## Widget Specifications

### W1: Sector Allocation (Donut Chart)

**Purpose:** Show portfolio weight distribution by sector.

**Data source:**
- Holdings from `portfolio_transactions` (ticker, quantity, avg_price)
- Sector from `stocks.company_info` (latest per ticker)
- Current price from OHLCV/registry

**Backend endpoint:** `GET /dashboard/portfolio/allocation`

**Response schema:**
```python
class AllocationItem(BaseModel):
    sector: str
    value: float          # current market value
    weight_pct: float     # % of total portfolio
    stock_count: int
    tickers: list[str]    # tickers in this sector

class AllocationResponse(BaseModel):
    sectors: list[AllocationItem]
    total_value: float
    currency: str
```

**Implementation:**
- Group holdings by `company_info.sector`
- Compute `value = quantity * current_price` per holding
- Aggregate by sector, compute weight %
- Sort by weight descending

**Frontend:**
- ECharts donut with center text: total portfolio value
- Hover tooltip: sector name, value (currency), weight %, stock count
- Legend below chart with sector colors
- Responsive: collapse legend on mobile

**Cache:** Redis, key `cache:dash:allocation:{user_id}:{market}`,
TTL 300s. Invalidate on portfolio CRUD.

---

### W2: Asset-wise Performance (Horizontal Bar Chart)

**Purpose:** Compare unrealized P&L % across all holdings.

**Data source:** Holdings from existing `/users/me/portfolio` response
(already includes `current_price`, `avg_price`, `gain_loss_pct`).

**Backend:** No new endpoint. Use existing holdings data.

**Frontend:**
- ECharts horizontal bar chart
- Y-axis: ticker symbols, sorted by gain/loss % (best → worst)
- X-axis: unrealized P&L %
- Bar colors: green (#22c55e) for positive, red (#ef4444) for negative
- Hover tooltip: ticker, P&L %, absolute P&L value, quantity, weight %
- Cap at 15 holdings; if more, show top 7 + bottom 7 + "N more" label

**Max holdings limit:** If portfolio has >15 stocks, show the 7 best
and 7 worst performers with a divider. Full list available on click.

---

### W3: Portfolio P&L Trend (Area Chart)

**Purpose:** Show portfolio value trajectory over time.

**Data source:** `GET /dashboard/portfolio/performance` (already exists).

**Response (existing):**
```python
class PortfolioDailyPoint(BaseModel):
    date: str
    value: float
    invested_value: float
    daily_pnl: float
    daily_return_pct: float

class PortfolioMetrics(BaseModel):
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    best_day_pct: float
    worst_day_pct: float

class PortfolioPerformanceResponse(BaseModel):
    data: list[PortfolioDailyPoint]
    metrics: PortfolioMetrics
    currency: str
```

**Frontend:**
- ECharts area chart (dual series):
  - Portfolio value: filled area (indigo gradient)
  - Invested value: dashed line (gray)
- Period selector tabs: 1M, 3M, 6M, 1Y, ALL
- Metrics bar above chart: Total Return %, Sharpe Ratio,
  Max Drawdown %, Annualized Return %
- Crosshair tooltip: date, portfolio value, invested, daily P&L
- Responsive: metrics wrap to 2x2 grid on mobile

---

### W4: News & Sentiment (Headlines + Sentiment Gauge)

**Purpose:** Surface relevant financial news for portfolio holdings
with aggregated sentiment.

**Data source:**
- Headlines: yfinance news API (primary), SerpAPI (fallback)
- Sentiment: `stocks.sentiment_scores` (per-ticker, already computed)

**Backend endpoint:** `GET /dashboard/portfolio/news`

**Response schema:**
```python
class NewsHeadline(BaseModel):
    title: str
    url: str
    source: str           # "Yahoo Finance", "Google News", etc.
    published_at: str     # ISO datetime
    ticker: str | None    # related ticker if stock-specific
    sentiment: float      # -1.0 to +1.0

class PortfolioNewsResponse(BaseModel):
    headlines: list[NewsHeadline]   # top 5, sorted by recency
    portfolio_sentiment: float      # weighted avg across holdings
    portfolio_sentiment_label: str  # "Bullish" / "Neutral" / "Bearish"
    market_sentiment: float         # broad market (Nifty 50 proxy)
    market_sentiment_label: str
```

**Implementation:**
1. Fetch latest headlines per portfolio ticker from yfinance
   (`Ticker.news` property — returns title, link, publisher, date)
2. If < 5 headlines, fallback to SerpAPI for broader market news
   (query: "Indian stock market news" or "Nifty 50 news")
3. Score each headline using existing `_sentiment_scorer.py`
4. Aggregate portfolio sentiment: quantity-weighted average of
   per-ticker `sentiment_scores` from Iceberg
5. Market sentiment: latest Nifty 50 / S&P 500 sentiment
6. Cache: Redis, `cache:dash:news:{user_id}:{market}`, TTL 900s
   (15 min — news is less time-sensitive)

**Frontend:**
- **Header:** Two sentiment pills side by side:
  - "Portfolio: Bullish (+0.42)" in green
  - "Market: Neutral (+0.08)" in amber
- **Body:** List of 5 headline cards:
  - Source badge (small, gray)
  - Title text (clickable → `target="_blank"`)
  - Ticker tag if stock-specific (indigo pill)
  - Time ago ("2h ago", "Yesterday")
  - Thin sentiment bar on left edge (green/red/gray)
- **Empty state:** "No recent news for your holdings"

**SerpAPI integration:**
- Only used as fallback when yfinance returns < 5 headlines
- Requires `SERPAPI_KEY` env var (optional — graceful skip if absent)
- Query: `{market} stock market news today`
- Parse: title, link, source, date from organic results

---

### W5: Recommendations (Action Cards)

**Purpose:** Surface actionable rebalancing and risk suggestions.

**Data source:**
- Holdings with current values and weights
- `company_info.sector` for sector analysis
- `analysis_summary` for technical signals (RSI, SMA, MACD)
- Forecast data for upside/downside context

**Backend endpoint:** `GET /dashboard/portfolio/recommendations`

**Response schema:**
```python
class Recommendation(BaseModel):
    type: str             # "overweight" | "sector_concentration"
                          # | "missing_sector" | "underperformer"
    severity: str         # "high" | "medium" | "low"
    title: str            # e.g. "RELIANCE.NS is overweight"
    description: str      # detailed explanation
    ticker: str | None    # related ticker if applicable
    metric_value: float   # the threshold-exceeding value
    threshold: float      # the threshold it exceeded

class RecommendationsResponse(BaseModel):
    recommendations: list[Recommendation]  # sorted by severity
    portfolio_health: str  # "Healthy" | "Needs Attention" | "At Risk"
```

**Recommendation rules:**

| Rule | Trigger | Severity |
|------|---------|----------|
| Single stock overweight | Weight > 20% | High |
| Sector concentration | Sector weight > 35% | High |
| Missing major sector | No holdings in IT/Financials/Healthcare | Medium |
| Underperformer | P&L < -15% AND bearish RSI/MACD signal | Medium |
| Low diversification | < 5 holdings total | Low |
| All-time-high proximity | Stock within 5% of ATH | Low (info) |

**Frontend:**
- **Header:** Portfolio health badge ("Healthy" green /
  "Needs Attention" amber / "At Risk" red) + count of suggestions
- **Body:** Card list, each card with:
  - Severity icon (red triangle / amber diamond / blue circle)
  - Title (bold)
  - Description (gray text, 1-2 lines)
  - Ticker link if applicable (opens stock detail)
- **Empty state:** "Your portfolio looks well-balanced" with
  green checkmark
- Max 6 recommendations shown, sorted by severity desc

---

## New Backend Endpoints Summary

| Endpoint | Method | Auth | New? |
|----------|--------|------|------|
| `/dashboard/portfolio/allocation` | GET | JWT | Yes |
| `/dashboard/portfolio/performance` | GET | JWT | Exists |
| `/dashboard/portfolio/news` | GET | JWT | Yes |
| `/dashboard/portfolio/recommendations` | GET | JWT | Yes |

All new endpoints follow existing patterns in `dashboard_routes.py`:
- `@router.get(...)` with Pydantic response models
- Redis caching with user-scoped keys
- Market filter query param
- Error handling with HTTPException

---

## File Changes

### Backend (Python)
| File | Changes |
|------|---------|
| `backend/dashboard_routes.py` | 3 new endpoints |
| `backend/dashboard_models.py` | 4 new Pydantic models |
| `backend/tools/portfolio_tools.py` | `get_sector_allocation()`, `get_recommendations()` helpers |
| `backend/tools/_sentiment_scorer.py` | Cache headlines (title + URL) in Redis during scoring |
| `backend/tools/_news_fetcher.py` | New module: yfinance news + SerpAPI fallback |

### Frontend (TypeScript)
| File | Changes |
|------|---------|
| `frontend/app/(authenticated)/dashboard/page.tsx` | Add 3-col analytics grid above existing grid |
| `frontend/components/widgets/SectorAllocationWidget.tsx` | New: ECharts donut |
| `frontend/components/widgets/AssetPerformanceWidget.tsx` | New: ECharts horizontal bars |
| `frontend/components/widgets/PLTrendWidget.tsx` | New: ECharts area chart |
| `frontend/components/widgets/NewsWidget.tsx` | New: Headlines + sentiment pills |
| `frontend/components/widgets/RecommendationsWidget.tsx` | New: Action cards |
| `frontend/lib/hooks/usePortfolioAnalytics.ts` | New SWR hooks for new endpoints |
| `frontend/lib/types.ts` | New TypeScript types |

### ECharts Setup
| File | Changes |
|------|---------|
| `frontend/lib/echarts.ts` | New: tree-shaken ECharts import (pie, bar, line, grid, tooltip, legend) |

---

## Story Point Estimates

| Ticket | SP | Description |
|--------|-----|-------------|
| W1: Sector Allocation | 5 | Backend endpoint + ECharts donut + tests |
| W2: Asset Performance | 3 | Frontend-only ECharts bars (data exists) |
| W3: P&L Trend | 3 | Frontend ECharts area (endpoint exists) |
| W4: News & Sentiment | 8 | yfinance news + SerpAPI fallback + sentiment aggregation + frontend |
| W5: Recommendations | 5 | Rule engine + frontend cards + tests |
| Dashboard integration | 3 | 3-col grid above existing widgets, lazy loading, market filter, responsive breakpoints |
| **Total** | **27** | |

---

## Testing

- Backend: pytest for each new endpoint (happy path + empty portfolio)
- Frontend: Vitest for each widget component (render, empty state)
- E2E: Playwright test for dashboard scroll → verify all widgets load
- Manual: verify with demo portfolio (seed_demo_data.py)

---

## Out of Scope (Sprint 6)

- Drill-down by industry within sector
- Portfolio transaction history view (SELL side)
- PDF export of analytics
- Push notifications for recommendations
- Benchmark comparison (vs Nifty 50)
