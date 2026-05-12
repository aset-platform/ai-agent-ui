# swing-setups-design

**Created:** 2026-05-12
**Spec:** `docs/superpowers/specs/2026-05-12-swing-setups-design.md`
**Plan:** `docs/superpowers/plans/2026-05-12-swing-setups.md`
**Branch:** `feature/aa-swing-setups`

## Summary

Eighth tab under `/v1/advanced-analytics/` — `swing-setups` —
emits three ranked, user-scoped watchlists per trading day:
Bull / Sideways / Bearish. Each regime has its own filter set
and rank formula encoded in `backend/advanced_analytics_swing.py`
(the **single source of truth** that both the route consumes
AND the on-page methodology panel renders). Tune thresholds in
ONE place; on-page explanation and filter behaviour move in
lockstep. The drift snapshot test
`test_methodology_thresholds_match_filter_constants` fails
loudly if they ever de-sync.

## Where to look

### Backend
- `backend/advanced_analytics_swing.py` — thresholds,
  `BULLISH_CATEGORIES`, `build_methodology(regime)`,
  `passes_{bull,sideways,bearish}(row, …)`, `rank_*`.
- `backend/advanced_analytics_routes.py`
  - `_death_cross_days_ago` (mirror of `_golden_cross_days_ago`)
  - `_rolling_band_20d_prev`, `_rsi_lookback`
  - `_load_indicators_latest` extended with 3 new keys
  - `_build_row` extended with 6 new fields (today_low +
    5 swing computed cols)
  - `_load_latest_recommendations` (async PG join)
  - `_apply_rec_data` (in-place row stamping)
  - `_compute_swing_setup` (orchestrator, after
    `_compute_report`)
  - `get_swing_setups` + `get_swing_methodology` endpoints
    inside `create_advanced_analytics_router()`
- `backend/advanced_analytics_models.py` — AdvancedRow extended
  with 9 fields; `SwingMethodology{Gate,Rank}`,
  `SwingMethodology`, `SwingSetupsResponse` response models.
- `backend/recommendation_routes.py` — invalidates
  `cache:advanced_analytics:swing-setups:bull:{uid}:*` on rec
  run.

### Frontend
- `frontend/lib/types/swingSetups.ts` — `SwingRegime`,
  `SwingMethodology{,Gate,Rank}`, `SwingSetupsResponse`.
- `frontend/lib/types/advancedAnalytics.ts` — AdvancedRow
  extended with 9 fields; `AdvancedTabId` /
  `ADVANCED_TAB_LABELS` / `ADVANCED_TAB_ORDER` include
  `"swing-setups"`.
- `frontend/hooks/useSwingSetups.ts` — `useSwingSetups`
  + `useSwingMethodology`.
- `frontend/components/advanced-analytics/SwingRegimePills.tsx`
- `frontend/components/advanced-analytics/SwingMethodologyPanel.tsx`
- `frontend/components/advanced-analytics/SwingSetupsTab.tsx`
- `frontend/app/(authenticated)/advanced-analytics/AdvancedAnalyticsClient.tsx`
  — renders `<SwingSetupsTab />` for `tab === "swing-setups"`.

### Tests
- `backend/tests/test_advanced_analytics_swing.py` — 78 tests
  covering helpers, regime filters, rank, rec lookup, orchestrator,
  routes, methodology drift.
- `e2e/tests/frontend/aa-swing-setups.spec.ts` — tab loads,
  regime switch swaps methodology copy, panel collapse
  persists.

## Conventions

- **Single source of truth**: tune thresholds in
  `advanced_analytics_swing.py` constants; methodology block
  AND filter behaviour move together (drift snapshot test
  enforces).
- **Rec-engine graceful degrade**: when user has no rec run
  this IST month, `rec_gate_applied: false` is surfaced in the
  response. UI strikes through the "Rec-engine bullish" gate
  row in the methodology panel and shows an amber note.
- **New computed cols on AdvancedRow**: 9 optional fields
  (today_low + 5 swing computed + 3 rec-join). Defaults all
  `None` so the seven existing AA reports remain unchanged.
- **Caching**:
  - Key shape:
    `cache:advanced_analytics:swing-setups:{regime}:{user_id}:{market}:p{page}:ps{page_size}:sk{sort_key}:sd{sort_dir}`.
  - TTL_STABLE = 300s.
  - Iceberg writes auto-invalidate via the
    `cache:advanced_analytics:*` glob in
    `stocks.repository._CACHE_INVALIDATION_MAP`.
  - Rec-engine writes explicitly invalidate
    `cache:advanced_analytics:swing-setups:bull:{uid}:*` in
    `recommendation_routes.py`.

## Pinned bullish category set

Pinned 2026-05-12 from DB inspection (Task 0):
`{offensive, value, growth, hold_accumulate}`.

Rec engine uses a portfolio-action vocabulary — not
stock-rating. Other live categories (`defensive`, `rebalance`,
`risk_alert`, `gap_fill`, `diversification`) are
direction-agnostic or bearish. Severity is surfaced on the row
for analysis but NOT used as a hard gate in Phase A.

When the rec engine introduces new categories, update
`BULLISH_CATEGORIES` in `advanced_analytics_swing.py` AND the
test snapshot
(`test_methodology_bullish_categories_match_constant`).

## Phase C hook

The three regime rule sets are the seed for the Phase C
algo-DSL strategy templates (`project_algo_v3_complete`). Once
a few weeks of hit-rate data are observed, lift each
`passes_*` function into an AST template that runs in
backtest / paper / live. Phase A's three lists then become
"live candidates for the v3 strategy" rather than the
strategy itself.

## Known limitations / Phase A.5 candidates

- Table renders without column selector or CSV export
  (intentionally minimal for Phase A; existing
  AdvancedAnalyticsTable is tightly coupled to the 7-report
  shape — would need refactor to share).
- No client-side sort; rank is server-driven.
- `sort_key` query param accepted but ignored — regime rank
  always applies in Phase A. Add validated allow-list when
  per-column sort lands.
- `as_of` not in cache key — relies on TTL_STABLE (5 min) for
  bhavcopy-day rollover. Consistent with other AA endpoints
  in this file.
