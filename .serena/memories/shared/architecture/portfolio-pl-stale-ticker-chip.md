# Portfolio P&L Stale-Ticker Chip — transparency UX pattern

UI pattern for surfacing "this aggregate value depends on stale upstream data" without breaking the chart or hiding the issue. Shipped on the dashboard P&L panel; mirrored on News & Sentiment for the analogous case.

## Problem class

Backend serves a portfolio aggregate (P&L, sentiment, etc.) computed from per-ticker time series. Some upstream rows are missing or stale (Yahoo NaN closes, market_fallback sentiment scores). Three failure modes:

1. **Hard truncation** — drop the date entirely → chart "ends" earlier than the latest trading day for some users
2. **Silent dip** — date shown but understated → user thinks portfolio crashed
3. **Lying smoothness** — forward-fill silently → user has no idea data is stale

This pattern picks **option 3 + transparency chip**: smooth aggregate (no dip, no truncation) + amber chip listing exactly which holdings are being held flat.

## Implementation

### Backend response shape

Two examples in `backend/dashboard_models.py`:

```python
class StalePriceTicker(BaseModel):
    ticker: str
    last_valid_close_date: str
    days_stale: int

class PortfolioPerformanceResponse(BaseModel):
    data: list[PortfolioDailyPoint] = ...
    metrics: PortfolioMetrics | None = None
    currency: str = "USD"
    stale_tickers: list[StalePriceTicker] = []  # populated when ticker.last_valid < series_end
```

Same shape on `PortfolioNewsResponse.unanalyzed_tickers: list[str]` for the sentiment-source-fallback case.

### Backend logic (`_build_portfolio_performance`)

1. Build `close_maps[t]` per ticker, sorted by date
2. Capture `last_valid_close_date[t]` BEFORE forward-filling (this is the most recent date with a real close)
3. ffill within existing rows (defense 2)
4. Compute `sorted_dates` = union across all tickers
5. **Extend each ticker's close_map forward to `series_end` with its last known close** (defense 4 — lets each ticker contribute on every series date even if its own data ended earlier)
6. After building `points[]`, populate `stale_tickers`: each held ticker where `last_valid_close_date[t] < series_end`

### Frontend chip component (reusable pattern)

`PLTrendWidget.tsx::StaleTickerChip` and `NewsWidget.tsx::UnanalyzedChip` follow the same shape:

```tsx
function StaleTickerChip({ stale }: Props) {
  const [open, setOpen] = useState(false);
  if (stale.length === 0) return null;
  return (
    <div className="relative">
      <button onClick={() => setOpen(v => !v)}
              onMouseEnter={() => setOpen(true)}
              onMouseLeave={() => setOpen(false)}
              className="inline-flex items-center gap-1
                         rounded-md bg-amber-50 dark:bg-amber-900/20
                         text-amber-700 dark:text-amber-400
                         px-2 py-0.5 text-xs font-medium
                         border border-amber-200 dark:border-amber-800/50">
        <WarningTriangleIcon /> {stale.length} holding{s} using previous close
      </button>
      {open && (
        <div role="tooltip" className="absolute right-0 top-full mt-1 z-20
                                       min-w-[240px] rounded-lg border ... shadow-lg p-3 text-xs">
          {/* Header sentence + ul of tickers + cleanup-clause footer */}
        </div>
      )}
    </div>
  );
}
```

Placed in the panel-title row, left of the existing sentiment/return pill.

### Visual design choices

- **Amber not red** — this is informational, not a blocker
- **Hover OR click** to open tooltip — both supported (chip is a button, hover triggers, click toggles for touch devices)
- **Auto-clears** when `stale.length === 0` — no dismiss button (would risk staleness of the dismissal itself)
- **Right-aligned tooltip** with `right: 0; top: 100%` so it doesn't push other panel content
- **Footer line** in tooltip explains auto-clear behaviour ("Upstream data hasn't settled. Auto-clears on next refresh.")

## When to apply

Any aggregate that shows ONE number/trend/mood derived from N per-entity values, where some of those entities can have stale/missing inputs. Apply when:

- The aggregate is user-trust-bearing (portfolio value, sentiment label)
- The miss rate is non-trivial (>5% of contributing entities) for at least some users
- Hiding it (silent ffill / mean imputation) is misleading; showing the raw gap (truncation / dip) is alarming

## Don't apply when

- The aggregate is an explicitly-best-effort estimate (forecast confidence intervals already encode uncertainty)
- The "stale" condition is shared across all users — surface it once at the top of the page, not on every panel

## Related patterns

- `shared/architecture/data-health-fix-panel` — admin-side equivalent (more detail, explicit fix actions, not user-facing)
- News widget 21-day max-age filter — paired with the unanalyzed chip; the chip says "we have nothing", the filter ensures we don't surface stale articles to fill the gap
- ASETPLTFRM-320 — sentiment-side parent ticket where the unanalyzed chip + max-age filter shipped
