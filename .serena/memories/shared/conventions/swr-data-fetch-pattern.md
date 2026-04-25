---
name: swr-data-fetch-pattern
description: One-true-way for client-side data fetching — apiFetch + SWR via shared hook, never raw useEffect+fetch
type: convention
---

# SWR data-fetch pattern

EVERY client-side data fetch in the frontend MUST flow through:
`apiFetch` → SWR → a shared hook in `frontend/hooks/`.

Hardened across Sprint 6 (`useDashboardData`) + Sprint 7 (`useAdminData`)
+ Sprint 8 (RSC `initialData` extension).

## Anti-pattern (do NOT do this)

```tsx
// ❌ Raw useEffect + fetch — no auto-refresh, no JWT refresh,
// no dedup, no cross-page cache, no stale-while-revalidate
useEffect(() => {
  fetch("/v1/some/endpoint")
    .then(r => r.json())
    .then(setData);
}, []);
```

## Correct pattern

### 1. Use `apiFetch`, never bare `fetch`

`apiFetch` (in `frontend/lib/`) auto-refreshes the JWT on 401, sets
the Bearer header from cookie, and prepends the backend URL.

```tsx
// ✅
import { apiFetch } from "@/lib/apiFetch";
const r = await apiFetch(`${API_URL}/v1/some/endpoint`);
```

`apiFetch` requires the **full URL** (`${API_URL}/path`), not a
relative `/path` (relative goes to the Next.js host on :3000, not the
backend on :8181).

### 2. Wrap in a SWR hook in `hooks/`

```tsx
// frontend/hooks/useSomething.ts
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";

const fetcher = (url: string) => apiFetch(url).then(r => r.json());

export function useSomething(initialData?: SomethingResponse) {
  return useSWR<SomethingResponse>(
    `${API_URL}/v1/something`,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 120_000,   // 2 minutes
      fallbackData: initialData,   // for RSC pre-fetch
    },
  );
}
```

Defaults:
- `revalidateOnFocus: false` — chat panel + dashboard polling
  already cover live updates.
- `dedupingInterval: 120_000` (2 min) — second nav within 2 min is
  instant.
- `fallbackData: initialData` — accepts RSC pre-fetched payload so
  the first render is real data, not a skeleton.

For auto-refreshing endpoints (e.g. observability):
`refreshInterval: 60_000`.

### 3. Component consumes the hook

```tsx
const { data, error, isLoading, mutate } = useSomething(initialData);
```

Optimistic mutations:

```tsx
await apiFetch(`${API_URL}/v1/something`, { method: "DELETE" });
mutate();   // refetch
```

## Hook locations

- `frontend/hooks/useDashboardData.ts` — all dashboard widgets
  (`useDashboardHome`, `useWatchlist`, `useForecastSummary`,
  `useAnalysisLatest`, `useLLMUsage`, `useRegistry`, `useUserTickers`,
  `useProfile`)
- `frontend/hooks/useAdminData.ts` — admin pages
  (`useAdminUsers`, `useAdminAudit`, `useObservability`,
  `useDataHealth`)

When adding a new endpoint, **extend the existing file** rather than
creating a new one — SWR's request dedup keys off the URL across
hooks, so co-location keeps the dedup map predictable.

## Aggregate endpoints

Prefer ONE endpoint per page that bundles widgets:

- `GET /v1/dashboard/home` returns `{watchlist, forecasts, analysis,
  llm_usage}` in a single round trip. Backend caches the aggregate
  in Redis (60s TTL).
- Page makes 1 request instead of 4. Cross-widget cache invalidation
  via single `mutate()`.

When adding a new widget:
1. Try to add a field to the existing aggregate response model.
2. Only create a new endpoint if the data is page-specific or
   security-scoped differently.

## RSC + SWR fallback

If the route is migrating to React Server Components (see
`shared/architecture/cookie-auth-rsc-pattern`):

```tsx
// page.tsx (RSC)
export default async function Page() {
  const data = await serverApiOrNull<Response>("/something");
  return <Client initialData={data ?? undefined} />;
}

// Client.tsx
const { data } = useSomething(initialData);
```

First render uses `initialData`, no skeleton. SWR still revalidates
in the background after `dedupingInterval`.

## Testing implications

Hooks are pure → unit-testable with `swr/_internal` test helpers.
Components are dumb → snap-test the rendered output given a fixed
hook return.

## Scope-aware endpoints

Some hooks accept a `scope: "self" | "all"` (admin observability +
audit-log). The hook MUST pass the scope into the SWR key so toggling
re-fetches:

```tsx
useSWR(`${API_URL}/v1/admin/audit-log?scope=${scope}`, fetcher);
```

`obsFetcher` skips superuser-only sub-calls (`tier-health`) when
`scope === "self"`.

## Related

- `shared/architecture/dashboard-swr-caching` — original
  implementation write-up
- `shared/architecture/cookie-auth-rsc-pattern` — RSC seeding
- `shared/conventions/tabular-page-pattern` — tables that consume
  these hooks
