---
paths:
  - "backend/recommendation_*.py"
  - "backend/jobs/recommendation_engine.py"
  - "backend/db/models/recommendation.py"
---

# Recommendation engine rules (auto-loads when touching recommendation code)

> Lazy-loaded via `paths:` frontmatter. Mirrors what was CLAUDE.md §5.8.
> Deep detail → Serena memory `recommendation-engine`.

- Monthly-per-scope quota: 1 run per `(user, scope, IST month)` via `get_or_create_monthly_run`. ALL entry points (widget/chat/scheduler) MUST route through it.
- `run_type ∈ {manual, chat, scheduled, admin, admin_test}`. User reads filter `admin_test` via `exclude_test=True`.
- Acted-on auto-detect: portfolio CRUD fires daemon thread → `update_recommendation_status`.
- Scope-aware: `/stats`, `/history`, `/performance` take `?scope=india|us|all`. `total_acted_on` derives from `acted_on_date`. `expire_old_recommendations` IS scope-aware. → `recommendation-engine`
- **Retention: 14 months hard cap.** Daily `recommendation_cleanup` (03:00 IST) deletes `stocks.recommendation_runs` where `run_date < CURRENT_DATE - INTERVAL '14 months'`. FK CASCADE wipes children.
- **`/performance`** = cohort-bucketed (week/month/quarter IST-truncated) × `recommendation_outcomes`. Granularity → primary horizon (weekly→7d, monthly→30d, quarterly→90d). Hit-rate uses `excess_return_pct > 0`. `pending_count` is horizon-aware — surface via CLAUDE.md §5.5 amber chip.
- **Outcomes job**: 4 horizons {7,30,60,90}, self-healing via `id.notin_(existing)`. Computes return at close on `created_at + N days` (next trading day if weekend, ±6d scan). ⚠ `benchmark_return_pct` hardcoded 0 (TODO); `price_at_rec` not always populated.
