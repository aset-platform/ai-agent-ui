# SSR Patterns

> Companion to `docs/frontend/perf-audit.md` and
> `docs/frontend/bundle-analysis.md`. This file
> documents the patterns that drove dashboard LCP from
> ~4.7 s → target <2 s (ASETPLTFRM-334).

## TL;DR

| Need                         | Use                                  |
|------------------------------|--------------------------------------|
| Static page, no auth         | RSC by default (no `"use client"`)   |
| Auth-gated page, hero data   | RSC + `serverApi()` + client tree    |
| Below-fold heavy chart       | `dynamic({ ssr: false })` + `<Suspense>` |
| Interactive UI (forms, hooks)| Client component (`"use client"`)    |
| Cross-cutting auth check     | `proxy.ts` edge cookie presence      |

---

## Client vs Server Component decision tree

```
Does the component need any of:
  - useState / useEffect / useMemo / useRef
  - browser globals (window, document, localStorage)
  - event handlers (onClick, onChange)
  - third-party UI libs that touch the DOM
  ?
   ├── YES → Client component  ("use client";)
   └── NO  → Server component  (default)
```

**Default to Server Components.** Add `"use client"`
only where you actually need interactivity. Heavier
data-fetch logic stays on the server, which:

- Eliminates client-side waterfall (no XHR after
  hydration).
- Ships less JS to the browser (the data-fetching
  code never crosses the wire).
- Lets the streamed HTML paint LCP-eligible content
  immediately.

### Hybrid pattern (the dashboard)

```
app/(authenticated)/dashboard/
├── page.tsx            ← Server Component (this file)
├── DashboardClient.tsx ← Client Component, big tree
└── loading.tsx         ← Skeleton
```

`page.tsx`:

```tsx
import DashboardClient from "./DashboardClient";
import { serverApiOrNull } from "@/lib/serverApi";

export default async function DashboardPage() {
  const home = await serverApiOrNull<DashboardHomeResponse>(
    "/dashboard/home",
  );
  return <DashboardClient initialData={home ?? undefined} />;
}
```

`DashboardClient.tsx`:

```tsx
"use client";
export default function DashboardClient({
  initialData,
}: { initialData?: DashboardHomeResponse }) {
  const { watchlist, forecasts } = useDashboardHome(
    initialData,
  );
  // ...
}
```

`useDashboardHome` passes `initialData` as SWR's
`fallbackData`, so the first render shows real data
with `isLoading=false`. SWR still revalidates in the
background — freshness preserved.

---

## Cookie-auth flow (RSC fetches)

The backend sets an HttpOnly `access_token` cookie on
`/v1/auth/login` (alongside the JSON-body access token
that client `apiFetch` reads from localStorage):

```
POST /v1/auth/login
Set-Cookie: access_token=eyJ…; HttpOnly; Path=/;
            Max-Age=3600; SameSite=Lax
```

Server Components read it via `next/headers`:

```ts
// frontend/lib/serverApi.ts
import { cookies } from "next/headers";

export async function serverApi<T>(path, init = {}) {
  const token = (await cookies())
    .get("access_token")?.value;
  // … forward as Bearer to BACKEND_URL/v1${path}
}
```

`serverApi` and `serverApiOrNull` (the latter swallows
401/403 and returns `null` so RSCs degrade
gracefully) are the **only** sanctioned way to call
the backend from a server tree. Don't `fetch()`
directly — you'll lose the auth header and the
CLAUDE.md rewrite-target plumbing.

### `BACKEND_URL` resolution

| Var                          | Used by                | Where set                                      |
|------------------------------|------------------------|------------------------------------------------|
| `BACKEND_URL`                | `serverApi` (server)   | `docker-compose.override.yml` (`http://backend:8181`) |
| `NEXT_PUBLIC_BACKEND_URL`    | `apiFetch` (client)    | Same file (`http://localhost:8181`)            |

The container's `localhost` points to the container
itself — never to the host's backend. Always use the
docker network hostname for server-side fetches.

---

## Edge proxy auth gate (`proxy.ts`)

`frontend/proxy.ts` runs on every navigation matched
by `config.matcher`. It:

1. Redirects `/` → `/dashboard`.
2. Redirects unauthenticated users (no `access_token`
   cookie) on protected routes → `/login?next=…`.
3. Redirects authenticated users on `/login` →
   `/dashboard` (don't show the form to logged-in
   users).

It checks **cookie presence**, not JWT signature.
Backend re-verifies the token on every API call, so
a stale or forged cookie can't access data — the
proxy is a UX layer, not the security boundary.

> **Why no `jose`?** Verifying the JWT in the edge
> runtime would mean shipping ~28 KB to every
> navigation and sharing the JWT secret between
> Python (HMAC) and Node (WebCrypto). The
> presence-check pattern avoids both costs at zero
> security cost (backend still authoritative).

---

## `<Suspense>` placement

Wrap **anything that streams** in `<Suspense>` so:

1. The surrounding tree hydrates without waiting.
2. PPR (when enabled, see below) can pre-render the
   shell statically and hold the suspended subtree
   for runtime resolution.

Idiomatic places:

- **Heavy charts**: ForecastChart, PortfolioForecastChart
  on `/analytics/analysis`. The Plotly init + dataset
  processing dominates hydration; `<Suspense>` lets the
  rest of the route paint first.
- **Authenticated data islands** in RSC pages:
  ```tsx
  <Suspense fallback={<HeroSkeleton />}>
    <AuthenticatedHero />   {/* server-fetches data */}
  </Suspense>
  ```
- **Per-tab content** when each tab has its own
  data load.
- **Routes whose inner subtree calls
  `useSearchParams()`** — Next 16 forces such subtrees
  to client-only and the surrounding `<Suspense>`
  fallback is what SSR ships. See "Suspense
  fallback over `useSearchParams`" below.

Don't wrap UI that doesn't suspend (plain HTML, sync
JS) — adds nothing.

### Suspense fallback over `useSearchParams` (admin, insights)

When the inner client subtree calls `useSearchParams()`,
Next 16 forces it to render client-only. **The
`<Suspense>` fallback is what SSR ships** — so a
`fallback={null}` literally blanks the SSR HTML for
that route, costing 3-4 s of post-hydration LCP on
every tab variant.

Two requirements for the fallback:

1. **Ship LCP-eligible text.** A static `<h1>` in the
   initial HTML becomes the LCP candidate at FCP, so
   the route's LCP isn't waiting on hydration.
2. **Match the inner wrapper's outer dimensions
   exactly.** Same `space-y-*`, same `p-*`, same
   `min-h-*` content reserve. Otherwise the
   fallback → real swap shifts layout downward
   when content streams in (CLS regression).

Pattern from `frontend/app/(authenticated)/admin/page.tsx`:

```tsx
// AdminPageInner returns:
//   <div className="space-y-6 p-4 sm:p-6">
//     <TabStrip />
//     <div className="min-h-[600px]">{tab content}</div>
//   </div>

function AdminPageSkeleton() {
  return (
    <div className="space-y-6 p-4 sm:p-6">
      <h1 className="text-2xl font-semibold ...">
        Admin Console
      </h1>
      <div className="min-h-[600px]" aria-hidden />
    </div>
  );
}

export default function AdminPage() {
  return (
    <Suspense fallback={<AdminPageSkeleton />}>
      <AdminPageInner />
    </Suspense>
  );
}
```

Verify SSR is shipping the heading:

```bash
JAR=$(mktemp)
curl -s -c $JAR -X POST http://localhost:3000/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@demo.com","password":"Admin123!"}' >/dev/null
curl -s -b $JAR http://localhost:3000/admin \
  | grep -oE '<h1[^>]*>[^<]+'
# Expected: <h1 ...>Admin Console
```

If the static heading is **smaller in viewport area
than a likely data-bound element on the final page**
(long table cell, multi-line textarea), Lighthouse
picks the bigger element and the LCP win is lost.
Examples from the 2026-04-25 audit:

- `/insights?tab=piotroski` — long stock-name cells
  beat the `text-2xl` h1 → still 5.1 s LCP.
- `/insights?tab=screenql` — 4-row textarea beats
  the h1 → still 3.7 s LCP.

These need RSC pre-fetch (real fix), not bigger
fallbacks.

Reference: `shared/debugging/suspense-fallback-null-ssr-hole`,
`shared/debugging/loading-gate-lcp-anti-pattern`.

### Anti-pattern: gating static text behind a single SWR loading state

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

The displayed text comes from `profile` and
`portfolioTotals`, not from `watchlist`. Gating on
`watchlist.loading` hides the LCP candidate until SWR
resolves, costing 3-5 s LCP. Phase breakdown
signature: `Render Delay = 100% of LCP`, FCP healthy.
Fix: render the structure always; show inline mini-
skeletons or `?? 0` placeholders only on the
data-bound bits.

---

## Preconnect / dns-prefetch

`frontend/app/layout.tsx` emits both for the backend
URL so DNS + TLS happen ahead of the first XHR:

```tsx
<link rel="preconnect" href={BACKEND_URL}
      crossOrigin="anonymous" />
<link rel="dns-prefetch" href={BACKEND_URL} />
```

Both are kept because legacy browsers honour only
`dns-prefetch`. URL bakes in at build time so there's
no runtime cost.

Saves ~100–200 ms on the first authenticated route
load. Don't sprinkle `preconnect` per-page — once at
the root layout is enough.

---

## Partial Prerendering (`cacheComponents`)

`next.config.ts` carries the flag scaffolded but
currently `false`:

```ts
const nextConfig: NextConfig = {
  cacheComponents: false,
};
```

Flipping it to `true` triggers:

- Static shell prerendering — the non-dynamic part
  of every page is generated at build time.
- Dynamic islands (anything reading `cookies()`,
  `headers()`, `searchParams`, or async data)
  suspend.

**Before flipping**, audit every Client Component
for non-deterministic render — `new Date()`,
`Math.random()`, etc. — and wrap with `<Suspense>`
or move to client-only mount-effects. PPR refuses to
prerender pages that might disagree between server
and client renders.

---

## Common pitfalls

### "I added `serverApi` and the response is 401."

Either the access cookie isn't reaching the server,
or `BACKEND_URL` is misconfigured. Check:

```bash
docker compose exec frontend printenv BACKEND_URL
# expect: http://backend:8181
```

If the cookie isn't sent: confirm `proxy.ts` matcher
isn't excluding the route, and check that login set
the cookie (curl `-c cookies.txt` then
`grep access_token cookies.txt`).

### "RSC shows stale data after a write."

`serverApi` defaults to `cache: "no-store"`. If you
overrode that for caching, you'll need `revalidatePath`
or `revalidateTag` after the write. SWR on the client
side still revalidates independently — but the very
first paint shows whatever RSC fetched.

### "I need to call backend from a Server Action."

Same `serverApi` works inside Server Actions —
`cookies()` reads the request cookie just like in a
Server Component. Don't import `apiFetch` (it's
client-only).

### "Hydration mismatch warning."

Almost always: a Client Component called `new Date()`
or read `localStorage` during render. Move to
`useEffect` or wrap in `<Suspense>` with
`{typeof window === "undefined" ? <Skeleton/> : <Real/>}`.

---

## Reference commits (Sprint 8, ASETPLTFRM-334)

| Phase | Commit     | Scope                                   |
|-------|------------|-----------------------------------------|
| E     | `3402f8f`  | preconnect + dns-prefetch               |
| D     | `bf74143`  | parallelize `/dashboard/home` sub-calls |
| B     | `4d11168`  | `<Suspense>` around chart routes        |
| C     | `269ef3f`  | markdown lazy + admin endpoint cache    |
| F     | `bd0aa9c`  | scaffold `cacheComponents` (off)        |
| A.1   | `d97e39c`  | HttpOnly cookie on login                |
| A.2   | `b446b9e`  | `proxy.ts` (Next 16 middleware rename)  |
| A.3   | `2606531`  | `serverApi.ts`                          |
| A.4   | `2170e48`  | dashboard RSC wrapper + initialData     |
