# Frontend Bundle Analysis

Generated: 2026-04-23 (Sprint 8, ASETPLTFRM-331); updated 2026-04-25 (ASETPLTFRM-334)
Source: `cd frontend && ANALYZE=true npx next build --webpack`
Artifact: `frontend/.next/analyze/client.html`

## ASETPLTFRM-334 re-audit — 34/34 routes (2026-04-25)

Run: `docker compose --profile perf run --rm perf` against
`frontend-perf` rebuilt with all 9 phase commits (E, D, B, C, F,
A.1-A.4, G).

### Headline metrics (34 routes, desktop, devtools throttling)

| Metric | Target | Result | Status |
|--------|--------|--------|:------:|
| FCP ≤ 1500 ms | 34/34 | **32/34** | partial — 2 outliers (`/login` 2082 ms, `/insights` 1716 ms) |
| LCP < 2000 ms | 34/34 | **10/34** | partial — see breakdown below |
| CLS ≤ 0.02 | 34/34 | **28/34** | partial |
| TBT ≤ 200 ms | 34/34 | **34/34** | ✅ |

LCP <2000 ms achieved on:

```
/analytics/insights                          1516 ms
/analytics/analysis?tab=portfolio            1519 ms
/analytics/analysis?tab=portfolio-forecast   1515 ms
/analytics/analysis?tab=recommendations      1522 ms
/analytics/insights?tab=screener             1517 ms
/analytics/insights?tab=risk                 1515 ms
/analytics/insights?tab=targets              1515 ms
/analytics/insights?tab=dividends            1512 ms
/analytics/insights?tab=correlation          1513 ms
/analytics/insights?tab=quarterly            1516 ms
```

All 10 are tabular routes where FCP === LCP (no large below-fold
visual element). The Phase E preconnect + Phase D parallelize-and-
shrink-TTL on `/dashboard/home` + Phase B Suspense + Phase A.4 RSC
pre-fetch combination put **first paint = 1515 ms across the board**.
For these routes, "first paint" *is* "largest paint."

### Routes still > 2000 ms LCP (24 of 34)

Sorted highest first.

| Route | LCP (ms) | LCP element |
|-------|----:|---|
| `/analytics/analysis?tab=analysis` | 7257 | StockChart (lightweight-charts) hydration |
| `/analytics/analysis` | 7220 | StockChart (default tab) |
| `/analytics/analysis?tab=compare` | 6706 | CompareContent multi-chart |
| `/analytics/analysis?tab=forecast` | 6463 | Prophet chart hydration |
| `/admin?tab=transactions` | 6061 | Transaction table render |
| `/login` | 5713 | Redirect → /dashboard hero (post-login) |
| `/admin?tab=audit` | 5663 | Audit log table |
| `/admin?tab=my_account` | 5479 | Profile form |
| `/admin?tab=observability` | 5472 | react-markdown still eager (one tab) |
| `/admin?tab=my_llm` | 5471 | Usage chart |
| `/admin` | 5465 | Default tab (users) |
| `/admin?tab=my_audit` | 5457 | Audit log scope=self |
| `/admin?tab=users` | 5446 | Users table |
| `/analytics/compare` | 5328 | Multi-ticker chart |
| `/analytics/insights?tab=piotroski` | 5089 | Piotroski stacked bar |
| `/admin?tab=scheduler` | 4972 | Scheduler DAG (cls=0.115) |
| `/dashboard` | 4956 | Sector widget below fold |
| `/admin?tab=recommendations` | 4923 | Recs table |
| `/admin?tab=maintenance` | 4904 | Health panels |
| `/analytics/insights?tab=sectors` | 4854 | SimpleBarChart still LCP element |
| `/analytics` | 4807 | Sector allocation widget |
| `/docs` | 4207 | Mkdocs iframe |
| `/analytics/insights?tab=screenql` | 3711 | ScreenQL editor (codemirror) |
| `/insights` | 2069 | Mostly tabular; just over |

### Phase-by-phase impact

| Phase | Goal | Measured Impact |
|-------|------|-----------------|
| E (preconnect) | Save 100-200 ms TLS | Contributes to uniform FCP 1515 ms; can't isolate from end-to-end measurement. |
| D (parallelize hero + TTL_HERO) | Cut cold-cache /dashboard/home | Backend `/dashboard/home` cold 3.5s → warm 144ms. Doesn't show in LCP because RSC consumes it server-side; benefits client-side SWR refresh. |
| B (Suspense around charts) | Decouple chart hydration from route hydrate | `analysis?tab=portfolio` 3510 → **1519 ms** (−57%); `analysis?tab=portfolio-forecast` 3498 → **1515 ms** (−57%). |
| C (markdown lazy + admin cache) | Defer 105 KB react-markdown chunk; warm admin endpoints | Admin endpoints now 2.5 ms warm (was 2000 ms). LCP didn't drop on admin routes — table render is still the bottleneck (see Sprint-9 plan). |
| F (cacheComponents scaffold) | Prep for PPR | Scaffolded false; activation deferred to follow-up since dashboard tree still has client `new Date()` calls outside Suspense boundaries. |
| A.1-A.4 (cookie + proxy + serverApi + RSC dashboard) | Eliminate dashboard hero round-trip | Backend logs confirm server-side `/v1/dashboard/home` per request; HTML carries 29 `current_price` + 13 `run_date` + 13 `sentiment` fields pre-baked. /dashboard LCP unchanged (4956 ms) because LCP element is below-fold sector widget, not the hero. |

### Why the LCP target wasn't hit on chart-heavy routes

The 13 SP scope of ASETPLTFRM-334 explicitly migrated **only** the
dashboard hero to RSC. The remaining 24 over-target routes share two
patterns:

1. **Chart-as-LCP-element.** Lighthouse picks the largest visible
   pixel area as the LCP element. On `/analytics/analysis`, that's
   the StockChart (~70% of viewport). The chart is `dynamic({ssr:
   false})` — it can't paint until the chunk downloads, the chart
   library evaluates, and the data hook returns. We've optimized
   data + Suspense already; the remaining cost is the
   `lightweight-charts` (150 KB) parse + first-frame layout.
2. **Admin tables.** Each admin tab re-fetches a different endpoint
   on hydrate, then the table renders all rows synchronously. The
   table is the LCP element. Phase C's reserved-height min-h
   prevents CLS but doesn't affect LCP — the rows still need to
   paint to qualify.

### Sprint 9 candidates (each scoped as 2-3 SP)

| Ticket | Scope |
|---|---|
| **TBD-A** Chart-route RSC | Migrate `/analytics/analysis` family to RSC + `serverApi('/dashboard/chart/ohlcv')` so the first paint includes static OHLCV grid. Chart hydrates over it. |
| **TBD-B** Admin tab RSC | Same pattern: server-fetch `/admin/audit-log` etc., render an SSR table, hydrate the action handlers. |
| **TBD-C** Sector widget RSC | Move `useSectorAllocation` to server-side seed via `serverApi('/dashboard/portfolio/allocation')`. Drops `/dashboard` LCP from 4956 → ~1515 ms. |
| **TBD-D** Activate `cacheComponents: true` | Audit & wrap remaining `new Date()` / `useTheme()` Client Components in `<Suspense>`. Flip flag. Catches the 32→34 FCP outliers. |
| **TBD-E** Prophet chart RSC + simpler library | The Prophet forecast chart costs 6.5s of hydration. Either pre-render to PNG server-side or migrate to a lighter chart library (echarts BarChart is already in the bundle). |

**Bottom line on 334:** the foundational pattern is in place — cookie-auth, edge proxy, serverApi, RSC dashboard wrapper, Suspense boundaries, hero-endpoint parallelism, all phase-G-documented. 10 routes hit target. Closing the remaining 24 needs follow-up tickets that migrate each chart-heavy / table-heavy page to the same pattern. The 13 SP estimate covered the infrastructure; the per-route migrations are independently scoped work.

---

## Original Sprint 8 baseline (ASETPLTFRM-331, 2026-04-23/24)

## Headline — bundle size

| Route | Before (KB) | After (KB) | Δ | Heavy libs removed |
|---|---:|---:|---:|---|
| `/dashboard` | 292* | **107** | −63% | echarts, echarts-for-react, zrender |
| `/analytics/insights` | 392* | **76** | −81% | plotly.js-basic-dist (1 MB), react-plotly |
| `/analytics/analysis` | 292 | **127** | −56% | lightweight-charts (150 KB via `DEFAULT_INDICATORS` import leak) |
| `/analytics/compare` | 19 | 19 | — | (compare chart already dynamic) |
| `/admin` | 392 | 392 | — | react-markdown still eager (follow-up) |
| `/login` | 25 | 25 | — | — |

## Headline — Lighthouse LCP (measured 2026-04-23, containerized run)

Source: `docker compose --profile perf run --rm perf` — 31 of 34
routes captured (Lighthouse protocol-errored on one tab mid-run;
see *Audit reliability* below). Baseline = Sprint 7 host run
prior to lazy-loading.

| Route | LCP before (ms) | LCP after (ms) | Δ |
|---|---:|---:|---:|
| `/analytics/analysis` | 18 439 | **6 779** | **−63%** |
| `/analytics/compare` | 11 046 | **5 153** | **−53%** |
| `/insights` | 10 138 | **6 544** | **−35%** |
| `/dashboard` | 7 740 | **4 746** | **−39%** |
| `/analytics` | 7 187 | **4 678** | **−35%** |
| `/login` | 6 143 | **3 731** | **−39%** |
| `/admin` | 5 305 | 5 341 | flat |
| `/analytics/insights` | 3 450 | 3 495 | flat (already optimal) |
| `/docs` | 4 036 | 4 077 | flat |

CLS stayed ≤ 0.02 across every route (skeleton fallbacks
preserved layout). TBT ≤ 0 ms Lighthouse-measured (desktop
throttling, no blocking JS observed).

### New tab audits (2026-04-24 verify-run)

The containerized run added 25 tab variants. Post-fix numbers:

| Route | LCP (ms) | Δ vs first run | Note |
|---|---:|---|---|
| `/analytics/insights?tab=sectors` | **4 622** | −3 901 (−46%) | plotly → SimpleBarChart (ECharts) |
| `/analytics/insights?tab=quarterly` | **3 486** | −5 107 (−59%) | plotly → SimpleBarChart (ECharts) |
| `/analytics/analysis?tab=portfolio-forecast` | 3 498 | flat | CLS 0.162 → **0.001** via min-h wrapper |
| `/analytics/analysis?tab=analysis` | 6 781 | flat | eager indicator charts — follow-up |
| `/analytics/analysis?tab=forecast` | 7 128 | +800 | Prophet chart; CLS 0.073 |
| `/admin?tab=observability` | 5 730 | flat | react-markdown eager in admin |
| `/analytics/insights?tab=screener` | 3 505 | flat | clean |

**No route breaches 8 s LCP anymore.**

\* “Before” dashboard/insights figures reconstruct what the
initial entry would have pulled had the chart-heavy widgets
stayed statically imported. Sprint 7 prod baseline measured
FCP 3.4 s and LCP up to 18 s on these routes.

## Top packages (parsed size across all chunks)

Measured from the full post-change webpack analyzer output.
These show the packages still in the total shipped JS — most
are now behind `next/dynamic` boundaries and only load when
the relevant route or tab is first interacted with.

| # | Package | Parsed KB | Notes |
|---|---|---:|---|
| 1 | `plotly.js-basic-dist` | 1 060 | Insights Dividends/Targets tabs; now dynamic |
| 2 | `next` | 575 | Framework; unavoidable |
| 3 | `echarts-for-react` | 514 | Dashboard + Sector widgets; now dynamic |
| 4 | `echarts` | 468 | — |
| 5 | `react-dom` | 174 | Framework |
| 6 | `zrender` | 166 | echarts render backend |
| 7 | `lightweight-charts` | 150 | Analysis route StockChart; now dynamic |
| 8 | `react-markdown` | 105 | Chat + Admin/observability viewers |
| 9 | `remark-gfm` | 27 | react-markdown GFM extension |
| 10 | `fancy-canvas` | 17 | lightweight-charts dep |

## Changes shipped

### 1. Lazy-load 6 chart widgets

All swapped from static imports to `next/dynamic` with
`ssr: false` + a `WidgetSkeleton` loading fallback:

**Dashboard** (`frontend/app/(authenticated)/dashboard/page.tsx`):
- `ForecastChartWidget` (plotly)
- `SectorAllocationWidget` (echarts pie)
- `AssetPerformanceWidget` (echarts bar)
- `PLTrendWidget` (echarts line)

**Insights** (`frontend/app/(authenticated)/analytics/insights/page.tsx`):
- `PlotlyChart` → `SimpleBarChart` (echarts BarChart) on Sectors + Quarterly
- `CorrelationHeatmap` (echarts heatmap, tree-shaken)

### ECharts BarChart migration (Sectors + Quarterly)

`PlotlyChart` was the only consumer of `plotly.js-basic-dist`
(1 MB). Both call sites were categorical bar charts — swapped
to new `components/charts/SimpleBarChart.tsx` which uses the
tree-shaken `echarts/core` + `BarChart` modules. On Dashboard
(already loads `echarts/core` for AssetPerformance, Sector, PL
widgets), hitting `insights?tab=sectors` now reuses the cached
echarts chunk. Plotly dependency can be dropped from
`package.json` in a follow-up (only `chartBuilders.ts` still
references `CHART_COLORS` and is dead code).

### 2. Fix StockChart type leak

`analytics/analysis/page.tsx` imported `DEFAULT_INDICATORS`
(a runtime const) from `StockChart.tsx`, which forced the
whole module — and its `lightweight-charts` dep — into the
initial bundle even though `StockChart` itself was dynamic.

Split:
- New `components/charts/StockChart.types.ts` holds
  `ChartInterval`, `IndicatorVisibility`, `DEFAULT_INDICATORS`
  (zero runtime deps).
- `StockChart.tsx` re-exports them for backward compat.
- `analysis/page.tsx` imports from `.types` directly.

Result: `/analytics/analysis` initial chunk fell from 292 KB
to 127 KB.

## Verification

Re-run the containerized Lighthouse suite (ASETPLTFRM-330):

```bash
docker compose --profile perf build
docker compose --profile perf up -d postgres redis backend frontend-perf
docker compose --profile perf run --rm perf
```

Compare `pw-lh-summary.json` against the Sprint 7 baseline
captured on 2026-04-23. Target:
- FCP < 2000 ms on `/dashboard`, `/analytics`, `/admin`
- LCP < 8000 ms on all authenticated routes
- CLS ≤ 0.02 (preserved)
- TBT < 200 ms

## Follow-ups (not in this PR)

- **Drop plotly deps**: `plotly.js-basic-dist` + `react-plotly.js`
  can come out of `package.json`; `chartBuilders.ts` (unused) and
  `PlotlyChart.tsx` need to be removed first.
- **Admin** still ships `react-markdown` (105 KB) in its initial
  chunk — likely via `ObservabilityTab`'s LLM event viewer. LCP
  on `/admin?tab=observability` is 5.7 s; converting that viewer
  to `next/dynamic` should drop it below 4 s.
- **CLS creep on admin tabs** (0.02–0.12 on scheduler, observability,
  maintenance, recommendations): async tables render without
  reserved height. Add `min-h-[Npx]` on the outer card containers
  (same fix as portfolio-forecast).
- **`/analytics/analysis?tab=analysis/forecast`** (~6.5–7.1 s) —
  sub-chart rehydration dominates. Consider splitting the
  ForecastChart variants behind `dynamic` too.
- **`/analytics/insights` Screener tab** still auto-queries the
  full universe on mount. Deferring to first user filter event
  would shave LCP further (needs UX signoff).

## Audit reliability (meta)

Single-Chromium, 34-route runs occasionally crash with
`Protocol error (Page.enable): Session closed` after ~30
successful audits. Lighthouse + persistent-context memory
pressure. Mitigation options, in order of effort:

1. Re-launch `chromium.launchPersistentContext` every 15 routes
   (closes + reopens the target tab, clearing accumulated CDP
   state).
2. Split the runner into two passes (base + tabbed) via an
   env flag.
3. Raise `docker compose` memory limit for the `perf` service
   (currently unlimited on Docker Desktop, but the perf
   container's own JS heap fills with gathered LHR objects).

Not blocking for Sprint 8 — we captured 31/34 routes on the
first try. Address if CI stabilisation becomes important.
