# RSI(2) Indicator on Stock Analysis Chart — Design

**Date:** 2026-06-01
**Status:** Draft (pending user review)
**Scope:** Add a toggleable RSI(2) pane to the stock-analysis chart, alongside the existing RSI(14) pane.

---

## 1. Goal

Surface Connors-style RSI(2) (Wilder, 2-period) as an indicator on the stock-analysis chart at `/analytics/analysis`. RSI(2) is a fast mean-reversion oscillator (overbought ≥ 90, oversold ≤ 10) and is already computed inside the algo subsystem (`backend/algo/features/daily_engine.py::wilder_rsi`) — this feature exposes the same signal to humans inspecting individual stocks.

## 2. Non-goals

- No changes to the algo subsystem, backtest engine, or features Iceberg tables.
- No new endpoints, caches, or storage.
- No change to default chart layout — RSI(2) is off by default.
- No support for arbitrary RSI periods (no period picker); RSI(2) only.

## 3. Architecture

Additive, mirrors the existing RSI(14) pipeline exactly. No refactor.

```
backend/tools/_analysis_indicators.py   ← compute RSI_2 column (one new line)
backend/dashboard_models.py             ← IndicatorPoint.rsi_2 field
backend/dashboard_routes.py             ← /chart/indicators wires rsi_2 through
                ↓ JSON
frontend/components/charts/StockChart.types.ts            ← IndicatorVisibility.rsi2
frontend/components/charts/StockChart.tsx                 ← IndicatorRow.rsi_2 + new pane
frontend/app/(authenticated)/analytics/analysis/page.tsx  ← menu option + memo
```

## 4. Backend changes

### 4.1 `backend/tools/_analysis_indicators.py`

Inside `_calculate_technical_indicators`, add one line directly under the existing `RSI_14`:

```python
df["RSI_2"] = ta.momentum.RSIIndicator(close=close, window=2).rsi()
```

### 4.2 `backend/dashboard_models.py`

Add the field to `IndicatorPoint` (currently around line 219), alongside `rsi_14`:

```python
rsi_2: float | None = None
```

### 4.3 `backend/dashboard_routes.py::get_chart_indicators`

In the per-row append (around line 1214), add:

```python
rsi_2=_safe(row.get("RSI_2")),
```

### 4.4 Cache & deploy

- Cache key `cache:chart:indicators:{ticker}` (TTL 300s) is unchanged. Stale cached payloads lack `rsi_2`; Pydantic deserializes the field to `None`, and the frontend treats `null` as "no data" via the existing `filterNull` path. Post-deploy `redis-cli FLUSHALL` per `CLAUDE.md` §4.5 #34 avoids the ≤5 min gap.
- Per `CLAUDE.md` §6.2 (backend restart triggers), adding a new field to a `response_model` requires `./run.sh restart backend` — uvicorn `--reload` alone won't re-register the model.

## 5. Frontend changes

### 5.1 `frontend/components/charts/StockChart.types.ts`

Add `rsi2` to `IndicatorVisibility` and default it to `false`:

```ts
export interface IndicatorVisibility {
  sma50: boolean;
  sma200: boolean;
  bollinger: boolean;
  volume: boolean;
  rsi: boolean;
  rsi2: boolean;          // ← new
  macd: boolean;
  supportResistance: boolean;
}

export const DEFAULT_INDICATORS: IndicatorVisibility = {
  sma50: true,
  sma200: true,
  bollinger: false,
  volume: false,
  rsi: true,
  rsi2: false,            // ← new, off by default
  macd: true,
  supportResistance: false,
};
```

### 5.2 `frontend/components/charts/StockChart.tsx`

1. Add `rsi_2: number | null;` to `IndicatorRow`.
2. Immediately after the existing RSI(14) pane block (around line 645), add a new pane gated on `vis.rsi2` that mirrors the RSI(14) pane structure with three differences:
   - Reads `d.rsi_2` instead of `d.rsi_14`.
   - Uses **90 / 10** horizontal `priceLine`s instead of 70 / 30.
   - Uses line color `#ec4899` (pink) to visually distinguish from RSI(14)'s `#8b5cf6` (violet).

Sketch:

```ts
// ── Pane 3b: RSI(2) ─────────────────────────
if (vis.rsi2) {
  const rsi2Pane = chart.addPane();
  subPanes.push(rsi2Pane);
  const rsi2Series = rsi2Pane.addSeries(LineSeries, {
    color: "#ec4899",
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
    title: "",
  });
  rsi2Series.setData(
    filterNull(
      aggIndicators.map((d) => ({
        time: toTime(d.date),
        value: d.rsi_2,
      })),
    ),
  );
  rsi2Series.createPriceLine({
    price: 90,
    color: "rgba(251,191,36,0.5)",
    lineWidth: 1,
    lineStyle: 2,
    axisLabelVisible: true,
    title: "",
  });
  rsi2Series.createPriceLine({
    price: 10,
    color: "rgba(251,191,36,0.5)",
    lineWidth: 1,
    lineStyle: 2,
    axisLabelVisible: true,
    title: "",
  });
}
```

When `vis.rsi2` is `false` (the default), the pane is never created — runtime cost and chart height are identical to today.

### 5.3 `frontend/app/(authenticated)/analytics/analysis/page.tsx`

Two additions:

```ts
// 1. INDICATOR_OPTIONS array — insert after the RSI (14) entry:
{ key: "rsi2", label: "RSI (2)" },

// 2. chartIndicators memo — add rsi_2 mapping inside the map():
rsi_2: d.rsi_2,
```

The existing menu rendering uses `data-testid={`stock-analysis-indicator-${opt.key}`}`, so the new toggle automatically gets `data-testid="stock-analysis-indicator-rsi2"` — no further E2E selector wiring needed.

### 5.4 Preference persistence

The `rsi2` toggle rides on the existing `prefs.indicators` localStorage path used by every other indicator. No new persistence code.

## 6. Data flow

```
ta.RSIIndicator(window=2)
        │
        ▼
compute_indicators(ticker)                ← already called for RSI_14
        │   (~200ms, cached 300s in Redis under cache:chart:indicators:{T})
        ▼
GET /v1/dashboard/chart/indicators?ticker=…
        │   IndicatorsResponse { data: [{date, rsi_14, rsi_2, …}] }
        ▼
useSWR in analysis/page.tsx (2-min dedup, existing hook)
        │
        ▼
chartIndicators memo            ← adds rsi_2 field
        │
        ▼
StockChart props.indicators[i].rsi_2
        │
        ▼
if (vis.rsi2) → addPane() → LineSeries (#ec4899) + 90/10 priceLines
```

## 7. Error handling & edge cases

| Case | Behavior |
|---|---|
| First 2 bars of history | `ta.RSIIndicator(window=2)` returns NaN → `_safe()` → `None` → frontend `filterNull()` strips → pane is empty for those dates (same as RSI(14) for bars 1–13). |
| Stale Redis cache after deploy | Pydantic defaults `rsi_2` to `None`. Pane shows no data until TTL (≤ 5 min) expires. `FLUSHALL` post-deploy avoids the gap. |
| `vis.rsi2 = false` (default) | Pane is not instantiated. Zero runtime cost; chart height unchanged. |
| Ticker with no OHLCV (404) | Existing `IndicatorsResponse(ticker=t_upper)` empty-data path is unchanged. |

## 8. Testing

### 8.1 Backend unit test

Create a new test file `tests/backend/test_analysis_indicators.py` (none exists today):

- Build a synthetic OHLCV `DataFrame` and call `_calculate_technical_indicators(df)`.
- Assert the returned frame has an `RSI_2` column.
- Assert the first 2 rows of `RSI_2` are NaN; subsequent non-NaN rows fall in `[0, 100]`.
- This satisfies the `CLAUDE.md` §4.4 #26 happy-path-plus-error-path rule (happy: bars ≥ 2; error: bars < 2).

### 8.2 Backend route test

Extend the `/chart/indicators` test in `tests/backend/test_dashboard_routes.py`:

- Assert response payload includes `rsi_2` per `IndicatorPoint`.
- Assert `rsi_2 is None` for pre-warmup dates and a `float` for the most recent bar.

### 8.3 Frontend test

Deferred. No `StockChart.test.tsx` exists in the repo today; introducing a chart-level test harness solely for this additive pane is out of scope. Visual smoke test via §11 rollout step covers the frontend.

### 8.4 E2E (deferred)

Optional follow-up: in an existing analysis Playwright spec, click `data-testid="stock-analysis-indicators-menu"`, toggle `stock-analysis-indicator-rsi2`, assert the chart container's `boundingBox` height grows. Defer to a follow-up ticket unless explicitly requested.

## 9. Performance

- Backend: one additional `ta.momentum.RSIIndicator(window=2)` call inside `compute_indicators`. Wilder RSI is O(n) over close prices — sub-millisecond on the ~5 000-row history typical here, well within the existing ~200 ms budget for this endpoint.
- Network: one additional `float | None` field per row in `/chart/indicators` JSON. Order of 5 000 rows × ~10 bytes ≈ +50 KB uncompressed, ~10 KB gzipped — well under the §5.15 per-route 500 KB JS budget (this is response payload, not bundle).
- Frontend: when toggled on, one extra TradingView pane with a single `LineSeries` + 2 `priceLine`s. When off (default), zero cost.

No expected impact on the `/analytics/analysis` route's §5.15 Lighthouse budget (Perf ≥ 75, LCP ≤ 3.0 s).

## 10. Out of scope / future work

- Period picker (RSI of arbitrary length).
- Plotting RSI(2) buy/sell markers from a strategy.
- Surfacing RSI(2) in the Insights table or screener.
- Mobile-specific layout (pane stacking) — inherits whatever the existing RSI(14) pane does today.

## 11. Rollout

1. Land PR on `dev` (squash merge per `CLAUDE.md` §4.4 #27).
2. After merge to `dev`: `./run.sh restart backend` + `redis-cli FLUSHALL`.
3. Smoke test: open `/analytics/analysis?ticker=RELIANCE.NS`, toggle RSI (2) in the indicators menu, verify a new pane renders with 90 / 10 reference lines and a pink line.

## 12. References

- `CLAUDE.md` §5.3 (Frontend SWR + theme patterns), §5.15 (perf budgets), §6.2 (backend restart triggers).
- Existing RSI(14) implementation: `backend/tools/_analysis_indicators.py:51`, `backend/dashboard_routes.py:1162-1230`, `frontend/components/charts/StockChart.tsx:606-645`.
- Existing Wilder RSI(2) in algo: `backend/algo/features/daily_engine.py:83`.
