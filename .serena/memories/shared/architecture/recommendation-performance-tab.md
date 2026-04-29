# Recommendation Performance Sub-Tab — cohort-bucketed analytics

A sibling sub-tab to the existing recommendations "History" view, mounted at `/analytics/analysis?tab=recommendations&subtab=performance`. Lets a user see how recommendations issued in the last 14 months have actually performed.

## Architecture summary

```
/analytics/analysis?tab=recommendations
  └─ RecommendationsPanel.tsx (parent)
       ├─ inner sub-tab strip: [History | Performance]
       │  (subtab choice persisted as ?subtab= URL param)
       ├─ History  → existing RecommendationHistoryTab (untouched)
       └─ Performance → new RecommendationPerformanceTab
              ├─ Period strip:  Weekly | Monthly | Quarterly
              ├─ Scope pills:   All | India | US
              ├─ Acted-on:      All | Acted-on only
              ├─ KPI tiles ×4:  total_recs, acted_on, hit_rate_Nd, avg_excess_Nd
              ├─ Pending chip:  amber, when any bucket has recs younger than horizon N
              ├─ Bar chart 1:   Activity (issued vs acted-on per bucket — always renderable)
              ├─ Bar chart 2:   Hit rate at horizon N
              ├─ Bar chart 3:   Avg return vs benchmark at horizon N
              └─ CSV download
```

## Key design decisions (2026-04-29 brainstorm)

1. **Bucket axis = cohort** (when issued). April bucket = recs *issued* in April + their attached outcomes. Best answers "how good were the April recommendations?".
2. **Default cohort = all** recommendations (engine-quality view); UI toggle for acted-on-only.
3. **Granularity drives the primary horizon** displayed in the chart: Weekly→7d, Monthly→30d, Quarterly→90d. The chart shows ONE matching horizon, not all three stacked. This required adding 7d to the outcomes job.
4. **Retention = 14 months hard cap.** Nightly cleanup deletes runs older than 14 months; FK CASCADE wipes child recs + outcomes.
5. **Activity chart always renderable** (uses `total_recs`, doesn't depend on outcomes) — so the tab is never empty for cohorts younger than the chosen horizon.

## Backend

### Endpoint

```
GET /v1/dashboard/portfolio/recommendations/performance
  ?granularity=week|month|quarter   (required)
  &months_back=1..14                 (default 14)
  &scope=all|india|us                (default all)
  &acted_on_only=bool                (default false)
```

Cache key: `cache:portfolio:recs:{user_id}:perf:{granularity}:{scope}:{acted_on_only}:{months_back}`, TTL_STABLE (300s). Caught by the existing `/refresh` invalidation glob.

Returns `RecommendationPerformanceResponse` with `buckets: PerfBucket[]` and `summary: PerfSummary`. Each bucket has 7/30/60/90d hit_rate, avg_return, avg_excess plus `total_recs`, `acted_on_count`, `pending_count`.

### Helper

`get_recommendation_performance_buckets()` in `backend/db/pg_stocks.py`. Single CTE-based raw SQL — `date_trunc(:granularity, r.created_at AT TIME ZONE 'Asia/Kolkata')`. Two queries total: bucket aggregates, then a separate non-bucketed query for the summary roll-up (avoids Simpson's paradox in cross-bucket averages).

### Pending count is granularity-aware

`pending_days` parameter passed to the SQL: 7 for weekly, 30 for monthly, 90 for quarterly. A rec is "pending" if it's younger than the primary horizon for the chosen granularity (no outcome computed yet at that horizon).

### Hit-rate convention

`excess_return_pct > 0` — matches the existing `/stats` endpoint. Note: `outcome_label = 'correct'` would be a different (action-aware) hit rate, but consistency with `/stats` was prioritised so KPIs cross-compare cleanly.

### Outcomes pipeline (`recommendation_outcomes` job)

Now 4 horizons: `(7, 30, 60, 90)` — see `shared/debugging/recommendation-outcomes-self-healing` for the window-match overhaul that made the new horizons backfillable.

### Retention cleanup

`recommendation_cleanup` daily job (cron 03:00 IST mon-sun). Single SQL: `DELETE FROM stocks.recommendation_runs WHERE run_date < CURRENT_DATE - INTERVAL '14 months'`. CASCADE wipes child recs + outcomes. Idempotent. Cache invalidates `cache:portfolio:recs:*` glob on non-zero deletes.

## Frontend

### Parent panel pattern

`RecommendationsPanel.tsx` owns the inner sub-tab strip. Reads `?subtab=` URL param on mount via deferred `Promise.resolve().then` (satisfies `react-hooks/set-state-in-effect`). On subtab change, calls `window.history.replaceState` to keep URL in sync without a route change.

This is a reusable pattern for any analytics tab that needs sub-tabs without nesting the route hierarchy further.

### Granularity → horizon mapping

```ts
const HORIZON_FOR: Record<Granularity, Horizon> = {
  week: 7,
  month: 30,
  quarter: 90,
};
```

Type-narrowed accessors `bucketHitRate / Return / Excess` and `summaryHitRate / Excess` on `Horizon` union — TS verifies field reads, no string indexing.

### KPI tiles

Reusable `InfoTooltip` (see `shared/conventions/info-tooltip-pattern`) on every label with What / How / Formula sections. The Avg Excess tile carries an amber "Heads up" callout because `benchmark_return_pct` is currently 0 in the executor (pre-existing TODO).

### Stale chip

When `summary.pending_count > 0`, render an amber chip "X recommendations under {N} days, outcomes pending" where N tracks granularity. Same transparency pattern as `PLTrendWidget::StaleTickerChip`.

### Activity chart

`SimpleBarChart` with two series: Issued (= total_recs) and Acted on (= acted_on_count). Doesn't depend on outcomes — solves the "Performance tab is blank when cohort is younger than horizon" UX problem.

## Tests

- `tests/backend/test_recommendation_performance.py` — 11 cases: `_bucket_label` formatting (week/month/quarter), input validation (granularity guard, months_back clamp, scope coercion), empty-result handling.
- `frontend/tests/RecommendationsPanel.test.tsx` — 5 cases: sub-tab dispatch, URL param hydration, switching back to History clears `?subtab`.
- Full SQL aggregation paths verified manually against a synthetic 60d-old fixture during development.

## Files

- `backend/db/pg_stocks.py` — `get_recommendation_performance_buckets()` + `_bucket_label` + `_PERF_BUCKET_SQL`. `get_recommendation_history` gained a `scope` param.
- `backend/recommendation_routes.py` — `/performance` route after `/stats`.
- `backend/recommendation_models.py` — `PerfBucket`, `PerfSummary`, `RecommendationPerformanceResponse`.
- `backend/jobs/executor.py` — `recommendation_cleanup` executor.
- `frontend/components/insights/RecommendationsPanel.tsx` (new)
- `frontend/components/insights/RecommendationPerformanceTab.tsx` (new)
- `frontend/components/common/InfoTooltip.tsx` (new)
- `frontend/hooks/useInsightsData.ts` — `useRecommendationPerformance({granularity, scope, actedOnOnly, monthsBack})`. `useRecommendationHistory` gained scope arg.
- `frontend/lib/types.ts` — `PerfBucket`, `PerfSummary`, `RecommendationPerformanceResponse`.
- `frontend/app/(authenticated)/analytics/analysis/page.tsx` — replaces `<RecommendationHistoryTab />` mount with `<RecommendationsPanel />`.

## CLAUDE.md cross-ref

§5.8 was updated with retention + performance + outcomes-job lines. Pattern Index has "Add a recommendation entry point | 5.8 | recommendation-engine" — when extending the perf endpoint, also reference this memory.
