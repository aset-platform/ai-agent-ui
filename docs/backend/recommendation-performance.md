# Recommendation Performance Analytics

Cohort-bucketed analytics over `recommendation_outcomes`. Lets users see how recommendations issued over the last 14 months actually performed, sliced by week / month / quarter.

Shipped 2026-04-29 as [ASETPLTFRM-339](https://asequitytrading.atlassian.net/browse/ASETPLTFRM-339).

---

## Endpoint

```
GET /v1/dashboard/portfolio/recommendations/performance
    ?granularity=week|month|quarter   (required)
    &months_back=1..14                 (default 14)
    &scope=all|india|us                (default all)
    &acted_on_only=true|false          (default false)
```

Returns `RecommendationPerformanceResponse`:

```json
{
  "granularity": "week",
  "scope": "all",
  "acted_on_only": false,
  "months_back": 14,
  "buckets": [
    {
      "bucket_start": "2026-04-13",
      "bucket_label": "2026-W16",
      "total_recs": 5,
      "acted_on_count": 3,
      "pending_count": 0,
      "hit_rate_7d": 60.0,
      "hit_rate_30d": null,
      "hit_rate_60d": null,
      "hit_rate_90d": null,
      "avg_return_7d": -0.89,
      "avg_excess_7d": -0.89,
      ...
    }
  ],
  "summary": {
    "total_recs": 5,
    "acted_on_count": 3,
    "pending_count": 0,
    "hit_rate_7d": 60.0,
    "avg_return_7d": -0.89,
    "avg_excess_7d": -0.89,
    ...
  }
}
```

Cache key `cache:portfolio:recs:{user_id}:perf:{granularity}:{scope}:{acted_on_only}:{months_back}`, TTL 300 s. Caught by the existing `/refresh` invalidation glob.

---

## Cohort axis

Buckets group recommendations by **when they were issued** (`recommendations.created_at` truncated to week / month / quarter in IST):

```sql
date_trunc(:granularity, r.created_at AT TIME ZONE 'Asia/Kolkata')::date
```

`bucket_label` formats:

| Granularity | Label format | Example |
|---|---|---|
| `week` | ISO year-week | `2026-W16` |
| `month` | abbreviated month + year | `Apr 2026` |
| `quarter` | quarter + year | `Q2 2026` |

---

## Granularity drives horizon

The frontend Performance sub-tab uses the granularity selector to also pick the **primary horizon** emphasised in the chart:

| Granularity | Primary horizon | KPI tile labels |
|---|---|---|
| `week` | 7d | Hit rate 7d, Avg excess 7d |
| `month` | 30d | Hit rate 30d, Avg excess 30d |
| `quarter` | 90d | Hit rate 90d, Avg excess 90d |

The endpoint always returns all four horizons; the UI just emphasises the matching one. This lets a user switch granularity and see immediately-meaningful numbers without thinking about horizon-vs-bucket-size.

---

## Hit-rate convention

A "hit" = `excess_return_pct > 0` at the horizon's outcome check. This matches `/v1/dashboard/portfolio/recommendations/stats` so cross-tab KPIs are directly comparable.

> **Caveat:** `benchmark_return_pct` is currently hardcoded to 0 in the daily outcomes executor (pre-existing TODO — not in scope of ASETPLTFRM-339). So `excess_return_pct ≡ return_pct` until the executor is wired to a real index (Nifty 50 for India recs, S&P 500 for US recs). The Performance tab's "Avg excess" tile carries an amber heads-up callout in its tooltip about this.

---

## `pending_count` (granularity-aware)

A rec is "pending" if it's younger than the **primary horizon** for the chosen granularity:

| Granularity | Pending threshold |
|---|---|
| `week` | rec age < 7 days |
| `month` | rec age < 30 days |
| `quarter` | rec age < 90 days |

The frontend renders an amber chip when `summary.pending_count > 0`: "X recommendations under {N} days, outcomes pending". Same transparency pattern as `PLTrendWidget::StaleTickerChip`.

---

## Helper

`get_recommendation_performance_buckets()` in `backend/db/pg_stocks.py` — single CTE-based raw SQL. Two queries total: bucket aggregates (grouped by `bucket_start, days_elapsed`) plus a separate non-bucketed roll-up for the summary (avoids Simpson's paradox in cross-bucket averages).

Inputs validated:
- `granularity` ∈ `{"week", "month", "quarter"}` else `ValueError`
- `months_back` clamped to `[1, 14]`
- `scope` coerced to `None` when not in `{"india", "us"}`

Optional `acted_on_only=True` restricts the cohort to recs with `acted_on_date IS NOT NULL`.

Asyncpg quirk: `:scope` and `:acted_on_only` parameters are CAST'd to VARCHAR / BOOLEAN at every use site to avoid `AmbiguousParameterError` when NULL. See Serena memory `shared/debugging/asyncpg-null-param-cast` for the full pattern.

---

## Outcomes job dependency

The Performance endpoint only returns metrics for outcomes already persisted by the daily `recommendation_outcomes` job (now 4 horizons: `(7, 30, 60, 90)`). The job was overhauled as part of ASETPLTFRM-339 to:

1. **Self-healing window-match** — picks up any rec ≥ N days old without an outcome at horizon N (was strict `created_at = today − N ± 2d` window which silently dropped outcomes when the daily job was skipped or a new horizon was added retroactively).
2. **Target-date close lookup** — fetches OHLCV close at `created_at + N days` (or first trading day after, ±6d forward scan) instead of "latest close per ticker" (which was only correct because the strict window kept recs ≈ N days old).

See [Scheduler — recommendation_outcomes](scheduler.md) for the full executor surface.

---

## Retention — `recommendation_cleanup`

Daily job at 03:00 IST mon-sun:

```sql
DELETE FROM stocks.recommendation_runs
 WHERE run_date < CURRENT_DATE - INTERVAL '14 months'
```

Foreign-key `CASCADE` wipes child `recommendations` and `recommendation_outcomes`. PG storage reclaimed via autovacuum. Idempotent — running on a clean table reports 0 deletes. Cache invalidates `cache:portfolio:recs:*` glob on non-zero deletes.

The Performance endpoint reads up to 14 months of history; **don't widen the window without widening retention** (the SQL would happily return empty for missing months, but the UI would suggest data was incomplete rather than retired).

---

## Frontend

The Performance sub-tab lives at `/analytics/analysis?tab=recommendations&subtab=performance`. Sibling to the existing History sub-tab. See `frontend/components/insights/RecommendationPerformanceTab.tsx`.

KPI tiles use the new reusable `InfoTooltip` component (`frontend/components/common/InfoTooltip.tsx`) for What / How / Formula explanations.

---

## Sprint 9 follow-ups (not in scope)

1. **Engine-side fix** for `price_at_rec` — populate from OHLCV at issue time (existing 41 NULL recs were backfilled out-of-band on 2026-04-29).
2. **Wire `benchmark_return_pct` to a real index** (Nifty India / S&P US). Removes the "excess ≡ return" caveat above.
3. **Tier-gating** retention (Free 6m / Pro+ 14m). Currently uniform.
4. **Per-recommendation drill-down** chart on Performance tab — clicking a bucket bar opens a per-rec returns curve.
5. **Playwright E2E** for the new sub-tab (existing rig needs auth-fixture wiring).
