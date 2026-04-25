# AuthenticatedLayout SSR unlock — 10-route LCP win

Removing the `mounted` state gate from `frontend/app/(authenticated)/layout.tsx` dropped **10 authenticated routes under the 2 s LCP target** in a single diff. Zero backend changes. Measured 2026-04-24 after ASETPLTFRM-331.

## What changed

Sprint 7's layout returned a loading shell early, gated on client-side mount:

```tsx
const [mounted, setMounted] = useState(false);
useEffect(() => setMounted(true), []);
if (!mounted) return <LoadingShell />;
return <LayoutProvider>...<ChatProvider>...<children>...</LayoutProvider>;
```

Rationale: "prevent hydration mismatches from context providers, localStorage reads, and WebSocket."

Commit `8c2d1b8` removed the gate after verifying each provider is SSR-safe:

- **LayoutProvider**: `useState(true)` stable default; localStorage read inside `useEffect`
- **ChatProvider**: `crypto.randomUUID` guarded by `typeof window !== "undefined"`; WebSocket created inside `useEffect` via `useWebSocket`
- **PortfolioActionsProvider**: pure `useState` modal state, no side effects at render time

## Measured impact

| Route | LCP before | LCP after | Δ |
|---|---:|---:|---:|
| `/insights?tab=targets` | 3 501 | **1 514** | −57% |
| `/insights?tab=correlation` | 3 493 | **1 514** | −57% |
| `/insights?tab=dividends` | 3 549 | **1 520** | −57% |
| `/insights?tab=risk` | 3 488 | **1 517** | −57% |
| `/insights?tab=quarterly` | 3 486 | **1 517** | −57% |
| `/insights?tab=screener` | 3 505 | **1 516** | −57% |
| `/analysis?tab=portfolio` | 3 510 | **1 520** | −57% |
| `/analysis?tab=portfolio-forecast` | 3 498 | **1 520** | −57% |
| `/analysis?tab=recommendations` | 3 508 | **1 516** | −57% |
| `/analytics/insights` | 3 493 | **1 637** | −53% |
| `/insights` | 6 556 | 2 091 | −68% |

Perf scores up **+12 to +14 points** on the under-2 s routes.

## Why it worked

The mount-gate replaced the children's SSR output with a standalone loading shell. Every route's server-rendered HTML was effectively empty from Lighthouse's perspective — the browser paints the shell, React hydrates (taking ~2 s with the framework + main bundle on desktop throttled), THEN the real page renders. LCP lands on whatever widget renders last, 3–5 s after FCP.

Removing the gate lets the full server-rendered HTML ship in the initial response. For routes where the LCP element is an SSR-friendly table or text block (insights + analysis table tabs), the biggest paint IS in the first HTML — LCP fires at FCP time.

## Why it didn't work everywhere

Routes still above 2 s after the fix:
- `/dashboard` (4 749 ms): LCP element is a data-driven widget (watchlist / AssetPerformance / ForecastChart). Hero SSR alone doesn't help because the widget is bigger than anything SSR can paint today.
- `/analytics/analysis*` (6 300–7 178 ms): ForecastChart + PortfolioForecastChart hydration cost dominates, LCP lands on the rendered chart.
- `/admin?tab=*` (4 659–5 975 ms): admin tables hydrate after SSR with larger footprint; also react-markdown (105 KB) eager in ObservabilityTab.
- `/insights?tab=sectors` / `?tab=piotroski` (4 622 / 4 883): echarts chart still large enough to be LCP.

These need per-route treatment: RSC data fetch (cookie-auth + middleware), `<Suspense>` boundaries around charts, react-markdown lazy-load, reserved-height admin table shells. Tracked in **ASETPLTFRM-334**.

## How to apply

When considering a hydration-gate / mount-gate / "render-after-useEffect" pattern in a layout or shell component:

1. **Audit every provider rendered inside** for SSR safety:
   - `useState` with literal defaults = safe
   - localStorage / sessionStorage reads = must be in `useEffect`
   - `window.*` / `document.*` access = `typeof window` guard or `useEffect`
   - `crypto.randomUUID` = both (guard + secure-context check — see `shared/debugging/lighthouse-fcp-text-heuristic` sibling)
   - WebSocket / EventSource = must be in `useEffect`
2. If all providers pass: **remove the gate**. Add a minimal SSR-safe loading shell (per `shared/debugging/lighthouse-fcp-text-heuristic`) that paints during the server-response phase.
3. Accept that client-only features (chat panel, WebSocket state) will hydrate after the shell — they still work, just arrive after first paint.

## Pre-existing concern: hydration mismatches

If removed without the audit, the browser will attempt to reconcile server HTML that doesn't match what the client first renders. React's `hydrationMismatch` warning surfaces as console noise but rarely breaks the page. Watch the browser console in dev.

## Related

- `shared/debugging/lighthouse-fcp-text-heuristic` — the FCP fix that preceded this; removing the mount-gate without the text-inclusive shell leaves the FCP floor at ~3.5 s.
- ASETPLTFRM-334 — follow-up initiative to push all 34 routes under 2 s LCP.
