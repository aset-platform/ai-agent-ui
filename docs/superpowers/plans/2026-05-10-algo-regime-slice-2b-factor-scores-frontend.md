# Regime-Aware Multi-Factor System — Slice REGIME-2b: Factor Scores Frontend

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session is about to start.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Extend the Insights tab with a new `Factor Scores` sub-tab displaying per-ticker factor scores using the existing tabular page pattern (CLAUDE.md §5.4). Endpoint-driven column selector, CSV download, multi-column sort, and client-side pagination. Pure UX layer — no compute, no schema changes.

**Architecture:** SWR-driven React panel reading from two new backend endpoints:
- `GET /v1/algo/factors/{ticker}` — single ticker (28 factor columns).
- `GET /v1/algo/factors?tickers=...` — bulk (watchlist / portfolio cohort).

Both endpoints cache-read from `stocks.daily_factors` Iceberg table (populated by REGIME-2a). Frontend renders tabular page per CLAUDE.md §5.4 pattern: `useColumnSelection` hook, `ColumnSelector` popover, `DownloadCsvButton`, locked identity column (`ticker`), sort on any column, client-side pagination (25 rows/page), empty-state messaging.

**Tech Stack:** Next.js 16, React 19, SWR, Tailwind CSS, ECharts (factor sparklines optional phase 2), Playwright E2E, FastAPI (Python 3.12).

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §5.1 REGIME-2b row + §6.1 test coverage + §3.10 frontend FactorScoresTab block.

**Branch:** `feature/algo-regime-slice-2b-factor-scores-frontend` off `feature/regime-multifactor-integration`.

**Depends on:** REGIME-2a merged (`stocks.daily_factors` Iceberg table populated, runtime read confirmed, `FACTOR_KEYS` registered in `strategy/features.py`).

**Estimated SP:** 8

---

## File Structure

**Backend (new):**
- `backend/algo/routes/factors.py` — `GET /v1/algo/factors/{ticker}` (single ticker, latest row), `GET /v1/algo/factors?tickers=ticker1,ticker2,ticker3` (bulk, latest per ticker). Both read from Redis cache key `cache:factors:{ticker}` (TTL_STABLE=300s). Cache miss → DuckDB query on `stocks.daily_factors`, populate Redis, return.

**Backend (modified):**
- `backend/algo/tests/test_feature_registry_sync.py` — extend CI sync test to verify frontend `FE.factorKeyNames` array matches backend `FACTOR_KEYS` registry (28-element parity check).

**Frontend (new):**
- `frontend/components/algo-trading/FactorScoresTab.tsx` — main tabular component. Imports `useFactorScores`, `useColumnSelection`, `ColumnSelector`, `DownloadCsvButton` from shared. Props: scope ∈ `{discovery, watchlist, portfolio}` (scoped tickers via `_scoped_tickers` helper, matching Insights 3-tier pattern §5.9). Renders: loading skeleton, empty state ("No stocks with factor data"), sorted table, pagination controls.
- `frontend/hooks/useFactorScores.ts` — SWR hook returning `{ data: FactorScoresData[], isLoading, error }`. Calls `GET /v1/algo/factors?tickers=...` on scope-computed ticker list. Dedup interval 2min per CLAUDE.md §5.3, `revalidateOnFocus: false`.
- `frontend/components/algo-trading/FactorScoresModal.tsx` (optional) — detail view opened from table row; shows single ticker's factor values + last_update timestamp.
- `e2e/tests/frontend/algo-trading-factor-scores.spec.ts` — Playwright test: login → navigate to Insights Factor Scores tab → verify column selector popover, sort by top 3 factors, CSV download, bulk load completes <5s. Storage state pre-loaded (no /auth/login).

**Frontend (modified):**
- `frontend/components/insights/InsightsPanel.tsx` — add Factor Scores sub-tab to Insights tab strip (alongside Screener, ScreenQL, Sectors, Piotroski, etc.).
- `frontend/utils/selectors.ts` (FE object) — add testid entries: `factor-scores-tab`, `factor-scores-table`, `factor-column-selector`, `factor-download-csv`.

**Shared utility (new or modified):**
- `frontend/lib/factorCatalog.ts` — export `FACTOR_CATALOG: FactorDef[]` (28 items) with metadata per factor: name, key, unit, description, category (`momentum`, `quality`, `lowvol`, `trend`, `volume`, `relative_strength`, `breadth`). Used by column selector for category-grouped UI + sorting logic.

---

## High-level task list (expand at session start)

1. **Backend endpoints** — implement `GET /v1/algo/factors/{ticker}` and `GET /v1/algo/factors?tickers=...` with Redis cache, DuckDB fallback, 403 for unauthenticated.
2. **SWR hook** — `useFactorScores` with error handling, loading state, dedup (2min).
3. **Column selector** — wire `useColumnSelection('factors', defaults, validKeys)` per CLAUDE.md §5.4 (localStorage-backed, SSR-safe, tolerates schema evolution).
4. **Tabular component** — `FactorScoresTab.tsx` with column header click→sort, pagination (25/page client-side), locked `ticker` column, visible cols filter.
5. **CSV download** — `DownloadCsvButton` using visible cols + sorted rows (CLAUDE.md §5.4 single source of truth).
6. **Empty state & skeleton** — messaging when no factors available or loading.
7. **Backend CI sync test** — verify `FACTOR_KEYS` ↔ frontend `FE.factorKeyNames` parity (28 fields).
8. **E2E test** — Playwright spec with column selector interaction, sort, CSV download, data rendering.
9. **Docs** — add "Factor Scores" section to `docs/algo-trading/insights.md` (optional; may defer to session finalization).

---

## Acceptance

- [ ] `GET /v1/algo/factors/INFY.NS` returns JSON with 28 factor keys (mom_12_1, roic, realized_vol_60d, etc.) + last_update timestamp. 403 if unauthenticated.
- [ ] `GET /v1/algo/factors?tickers=INFY.NS,TCS.NS,RELIANCE.NS` returns array of 3 rows (or fewer if some tickers absent from factor store), sorted by ticker asc.
- [ ] Cache invalidation test: trigger `stocks.daily_factors` write → Redis `cache:factors:*` pattern invalidated → next fetch recomputes from DuckDB.
- [ ] Frontend FactorScoresTab loads within 2s on watchlist (20 tickers) after data in Iceberg. SWR dedup honored (no duplicate backend calls for 2min interval).
- [ ] Column selector: catalog shows 28 factors grouped by category (momentum / quality / lowvol / trend / volume / relative_strength / breadth). Toggle 3 columns off → table reflows, CSV download respects selection.
- [ ] Sort: click `mom_12_1` header twice → first ascending, second descending, third clears (default order = ticker asc).
- [ ] CSV export: headers match visible columns (excluding locked ticker if user deselected; NO — ticker is locked per spec), 25 rows max per page export. User can export full dataset via paginating + re-exporting.
- [ ] E2E test passes: tab navigates, column selector opens, 3 columns toggled, data rendered, CSV clicked. No console errors.
- [ ] CI sync test: `test_feature_registry_sync_extended` asserts `len(FACTOR_KEYS) == len(FE.factorKeyNames) == 28` + key-by-key name parity.
- [ ] No regression on other Insights tabs (Screener, ScreenQL, etc.).

---

## Out of scope for REGIME-2b

- Factor sparklines or mini-charts in table cells (phase 2, post-acceptance, if performance permits).
- Exporting per-ticker factor history (daily OHLCV time-series). Factor Scores tab shows latest close only.
- Sorting by multiple columns (single-column sort sufficient; Shift+Click combos deferred).
- Server-side pagination (client-side 25/page adequate for watchlist — full universe paginated in phase 2 if needed).
- Factor correlation heatmap (separate future widget; not tabular page pattern).
- User-defined factor thresholds or alerting (v4 feature).
