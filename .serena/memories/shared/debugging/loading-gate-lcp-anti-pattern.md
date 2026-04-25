# Premature loading-gate skeletons hide LCP candidates

## Symptom

Lighthouse reports LCP = 5–7 s on a route, FCP = ~1.5 s, TBT under
200 ms. Phase breakdown: TTFB ~5 ms, Load Delay 0, Load Time 0,
**Render Delay = 100% of LCP**. The LCP element exists in the final
DOM, but it didn't *exist* in the DOM during the window between FCP
and LCP — something replaced it with a skeleton.

## Root cause

Common React anti-pattern: gate the entire structural render on a
single SWR loading state, even when most of the displayed text is
*static or comes from a different prop*:

```tsx
function HeroSection({ watchlist, profile, portfolioTotals }) {
  if (watchlist.loading) return <Skeleton h-56 />;  // ❌
  return (
    <div>
      <p className="text-3xl">Welcome back, {profile.name}</p>
      <p className="text-4xl">{portfolioTotals.value}</p>
    </div>
  );
}
```

The "Welcome back" text + portfolio value come from `profile` and
`portfolioTotals`, not from `watchlist`. The watchlist gate hides
the LCP candidate (largest text on the page) until SWR resolves,
costing 3–5 s of LCP.

## Diagnosis

For each problem route, extract from the Lighthouse JSON
(`.lighthouseci/pw-{route}.json`):

```python
items = audits["largest-contentful-paint-element"]["details"]["items"]
node = items[0]["items"][0]["node"]
print(node["selector"], node.get("nodeLabel"))
phases = {p["phase"]: p["timing"] for p in items[1]["items"]}
# expect TTFB=ms, Load Delay=0, Load Time=0, Render Delay=LCP-FCP
```

If `Render Delay` is 100% of LCP and the `nodeLabel` is recognizable
static-or-prop-driven text, look for a `if (X.loading) return
<Skeleton/>` gate above the JSX that contains the LCP element.

## Fix shape

Remove the gate. Render structure always. Use inline mini-skeletons
or `?? 0` placeholders only on the data-bound bits:

```tsx
function HeroSection({ watchlist, profile, portfolioTotals }) {
  if (watchlist.error) return <WidgetError />;
  return (
    <div>
      <p className="text-3xl">Welcome back, {profile.name}</p>
      <p className="text-4xl">{portfolioTotals.value ?? 0}</p>
      ...
    </div>
  );
}
```

## When to KEEP the gate (counterpoint)

The gate is correct when removing it lets data-bound content hijack
the LCP/CLS measurement. Four cases caught in iter2 of the
2026-04-25 audit (had to be reverted in iter4):

**(a) Conditional charts.** `{rows.length > 0 && <Chart/>}` patterns
— the chart pops in mid-page after data arrives, pushing
everything below it down. CLS spikes to 0.254. *Example: SectorsTab
renders a SimpleBarChart only when rows are non-empty.*

**(b) Wide table cells.** A table cell whose text width dominates
the static h1 fallback (long stock names like "Ahluwalia Contracts
(India) Limited") becomes the LCP element and paints AFTER the h1.
LCP shifts from h1 (1.5 s) to the cell (5 s). *Example:
PiotroskiTab.*

**(c) Heatmap / canvas / large visual element.** Same intrinsic-
LCP issue as (b) — canvas element is large, becomes LCP candidate,
paints after data load. *Example: CorrelationTab heatmap.*

**(d) Many empty-stat cards re-painting on data arrival.** Cards
render with `?? 0` placeholders, then re-render with real numbers.
Each re-paint is a layout shift. *Example: ObservabilityTab summary
cards (5 cards × 4 stats).*

In all four cases the page-level `<Suspense fallback>` already
supplies the SSR LCP win (see
`shared/debugging/suspense-fallback-null-ssr-hole`). The inner gate
keeps data-bound elements out of the LCP/CLS window during load.

**Tabs that MUST keep their gate today** (per 04-25 iter4): Sectors,
Quarterly, Piotroski, Correlation, Observability.

## CLS budget verification

Every loading-gate removal MUST also verify CLS held (≤ 0.02
budget). Iter2 of the 04-25 audit dropped mean LCP -36% but
introduced 0.254 CLS regressions on Sectors/Quarterly that iter4
had to revert. Always diff both LCP and CLS per route after a
rebuild + audit cycle:

```bash
cp .lighthouseci/pw-lh-summary.json \
   .lighthouseci/pw-lh-summary-iter{N}.json
# rebuild + audit
docker compose --profile perf build frontend-perf
docker compose --profile perf up -d frontend-perf
docker compose --profile perf run --rm perf
# diff with python script that walks both files and prints LCP/CLS
# delta per route (see session/2026-04-25-sprint8-lcp-improvements
# for the full compare_full.py shape)
```

## Reference

Sprint 8 LCP iteration (commit b1c816e, 2026-04-25). 13 routes
dropped LCP -62% to -75%, mean LCP across 34 routes 4202 → 2786 ms.
Iter2 of the iteration loop introduced two CLS regressions (sectors,
quarterly: 0.000 → 0.254) which iter4 reverted by restoring those
specific tab-level gates. See also
`shared/debugging/suspense-fallback-null-ssr-hole`.
