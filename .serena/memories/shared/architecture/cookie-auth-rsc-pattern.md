---
name: cookie-auth-rsc-pattern
description: Pattern for migrating an authenticated route to React Server Components with HttpOnly cookie auth (ASETPLTFRM-334 phase A)
type: architecture
---

# Cookie-auth + RSC pattern

Established in ASETPLTFRM-334 phase A (commits `d97e39c`, `b446b9e`, `2606531`, `2170e48`). Pattern for converting any client-only authenticated route to a Server Component that pre-fetches its data on the server.

## Problem

Client-only authenticated pages hydrate, then issue an XHR to fetch hero data, then paint widgets. LCP is "skeleton until hydration finishes + data round-trip completes" — typically 3-7s on dashboard-shaped routes. Pre-A.1 the dashboard measured 4744 ms LCP.

## Solution shape

```
proxy.ts (edge)         → cookie presence check, redirect on missing
serverApi.ts (RSC)      → reads cookie via next/headers, forwards Bearer
DashboardPage (RSC)     → server-fetches /v1/dashboard/home
DashboardClient (CSR)   → existing client tree, gets initialData prop
useDashboardHome(init?) → seeds SWR fallbackData, no skeleton step
```

## The four pieces

### 1. Backend — HttpOnly cookie on login

`auth/endpoints/auth_routes.py::_set_access_cookie()`:

```python
response.set_cookie(
    key="access_token", value=access_token,
    httponly=True, secure=_is_secure_env(),
    samesite="lax",  # NOT strict — OAuth/payment redirects need lax
    path="/", max_age=60 * 60,  # match JWT TTL
)
```

Wired into `login()`, `refresh()`, `logout()` (clears it). Additive — JSON body still carries the token for client-side `apiFetch`.

### 2. Edge proxy (`frontend/proxy.ts`, Next.js 16)

`middleware.ts` was deprecated in Next 16. New file: `proxy.ts`.

- Checks cookie **presence**, NOT JWT signature. Backend re-verifies on every API call — proxy is UX layer, not security boundary. Avoids shipping `jose` to edge runtime + cross-language secret-sharing.
- Accept **either** `access_token` OR `refresh_token` cookie. Legacy sessions (pre-A.1) only have refresh — they'd infinite-loop without this. Fix landed in hotfix commit `e33172d`.

### 3. `serverApi.ts` helper

`frontend/lib/serverApi.ts`:

```typescript
import { cookies } from "next/headers";

export async function serverApi<T>(path, init = {}) {
  const token = (await cookies()).get("access_token")?.value;
  // ... forwards as Bearer to BACKEND_URL/v1${path}
}
```

`serverApiOrNull<T>` swallows 401/403 → returns null. Use it from RSC pages so a stale-cookie user gets `initialData=undefined` and degrades to client-side fetch instead of 500ing the page.

**`BACKEND_URL` env required** in `docker-compose.override.yml` for the dev frontend (`http://backend:8181`) — `localhost` from inside the container points to the container itself, not the host backend.

### 4. RSC page wrapper

```tsx
// app/(authenticated)/dashboard/page.tsx
export default async function DashboardPage() {
  const data = await serverApiOrNull<DashboardHomeResponse>("/dashboard/home");
  return <DashboardClient initialData={data ?? undefined} />;
}
```

Existing client component takes `initialData?: T` prop, passes to its hook. The hook (e.g. `useDashboardHome`) forwards to SWR's `fallbackData`:

```typescript
useSWR(key, fetcher, {
  revalidateOnFocus: false,
  dedupingInterval: 120_000,
  fallbackData: initialData,
});
```

First render: real data, `isLoading=false`, no skeleton step. SWR still revalidates in the background.

## Verification (commit `2170e48`)

Backend logs show server-side `GET /v1/dashboard/home` per dashboard request. Streamed HTML carries 29 `current_price` + 13 `run_date` + 13 `sentiment` fields baked in (grep on raw HTML).

## Pitfalls observed

- **Existing sessions infinite-loop** without dual-cookie acceptance in proxy — handled by `e33172d`.
- **PPR / `cacheComponents: true`** flagged "new Date() inside Client Component without Suspense" on /dashboard, /analytics, /admin. Don't flip the flag until those Client Components get Suspense wraps. Scaffolded false in commit `bd0aa9c`.
- **`MarketFilter` type re-export** — old page.tsx exported it, RSC can't. Move type to `DashboardClient.tsx` and update consumers (HeroSection, ForecastChartWidget).
- **LCP element is below-fold** on /dashboard (sector widget) — hero RSC migration only fixes hero LCP. To get /dashboard <2s, the largest visual element on initial viewport needs to be server-rendered too.

## When to use this pattern

- Authenticated route with hero data fetched from a single backend endpoint.
- LCP element fits in the initial viewport.
- Client tree has interactive widgets that need to remain client-rendered.

When NOT to use:
- Route's LCP element is a chart (chart libraries are client-only — RSC can't pre-render them). Use `<Suspense>` around the chart instead (phase B pattern).
- Page is fully interactive top-to-bottom (no static hero) — RSC adds complexity for no LCP win.

## Next-applications (Sprint 9 follow-ups in `bundle-analysis.md`)

- Sector widget on /dashboard → server-fetch `/v1/dashboard/portfolio/allocation`
- /analytics/analysis tabs → server-fetch OHLCV grid (chart hydrates over)
- /admin tabs → server-fetch the per-tab endpoint (audit-log, usage-stats, etc.) and SSR the table
- Activate `cacheComponents: true` once remaining `new Date()`/`useTheme()` Client Components are wrapped in Suspense

## References

- `docs/frontend/ssr-patterns.md` — full prose guide (commit `af3badb`, 290 lines)
- Phase commits: `d97e39c` (cookie) → `b446b9e` (proxy) → `2606531` (serverApi) → `2170e48` (dashboard RSC)
- Hotfix: `e33172d` (legacy session compat)
