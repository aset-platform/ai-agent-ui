# Frontend SWR Caching Strategy

## Pattern: All data hooks use SWR with 2-minute dedup
```typescript
const { data, error, isLoading, mutate } = useSWR<T>(url, fetcher, {
  revalidateOnFocus: false,
  dedupingInterval: 120_000,  // 2 minutes
});
```

## Hook Location: `frontend/hooks/useDashboardData.ts`
- `useDashboardHome()` — aggregate `/dashboard/home` (1 request for all widgets)
- `useWatchlist()`, `useForecastSummary()`, `useAnalysisLatest()`, `useLLMUsage()` — individual
- `useRegistry()` — shared across analytics + marketplace pages
- `useUserTickers()` — with `mutate` for optimistic link/unlink
- `useProfile<T>()` — generic cached profile

## Hook Location: `frontend/hooks/useAdminData.ts`
- `useAdminUsers()` — SWR + CRUD mutations via `mutate()`
- `useAdminAudit()` — SWR (replaces raw useEffect)
- `useObservability()` — SWR with `refreshInterval: 60_000` (auto-refresh)

## Pages Converted from raw useEffect → SWR
- `/dashboard` (home) — uses `useDashboardHome()` aggregate
- `/analytics` — uses `useWatchlist()` + `useAnalysisLatest()` + `useRegistry()`
- `/analytics/marketplace` — uses `useRegistry()` + `useUserTickers()`
- `/admin` — all 3 tabs now SWR-based

## Aggregate Endpoint
`GET /v1/dashboard/home` returns `DashboardHomeResponse`:
```typescript
{ watchlist, forecasts, analysis, llm_usage }
```
Dashboard page makes 1 request instead of 4. Backend calls sub-functions
and caches the aggregate in Redis (60s TTL).

## Key Benefit
Page revisit within 2 minutes → instant (SWR cache hit, zero network).
Cross-page cache sharing: registry data reused across analytics + marketplace.
