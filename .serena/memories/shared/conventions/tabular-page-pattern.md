---
name: tabular-page-pattern
description: One-true-way for building tabular list pages on /insights and /admin — column selector, CSV export, pagination, sort, locked id columns
type: convention
---

# Tabular page pattern (Insights, Admin tables)

Every new table/list page MUST follow this layout. Deviation creates
UX drift. Hardened across Sprint 7 (ASETPLTFRM-322 ScreenQL) +
Sprint 8 (ASETPLTFRM-333 Column Selector + ASETPLTFRM-323 admin-tabs).

## Required components

### 1. Column selector

When the catalog ≥ 8 columns, the page MUST expose a column
selector. Two pieces:

- `frontend/lib/useColumnSelection.ts` — hook
  `[selected, setSelected, reset] = useColumnSelection(
       storageKey,    // e.g. "insights.columns.screener"
       defaults,      // pre-feature visible set (zero-regression)
       validKeys,     // current catalog — filters stale localStorage
  )`
  Two-phase hydration (SSR defaults → client localStorage on first
  effect) avoids hydration mismatches. Tolerant to catalog evolution.

- `frontend/components/insights/ColumnSelector.tsx` — reusable
  popover with grouped checkboxes, per-category select-all, search,
  global select/deselect, reset, locked keys.

  Props:
  ```tsx
  <ColumnSelector
    catalog={specs}        // { key, label, category }[]
    selected={selected}
    onChange={setSelected}
    onReset={reset}
    lockedKeys={["ticker"]}
    buttonLabel="Columns"
  />
  ```

Testids: `column-selector-trigger`, `column-selector-popover`,
`col-toggle-<key>`.

### 2. Single source of truth for visible columns

```ts
const visibleCols = allCols.filter(c => selected.includes(c.key));
```

Both the rendered table AND the CSV export consume `visibleCols`.
Never diverge — CSV showing different columns than the screen is a
trust-breaker.

### 3. CSV download

Use the shared component, NOT a one-off.

`frontend/components/common/DownloadCsvButton.tsx` — icon + "CSV"
text. Place **next to pagination controls**, NOT in the panel header.
`loading` prop for async-collecting tables. Receives sorted (NOT
paginated) rows so the user gets the full filtered set.

### 4. Pagination

- Server-side if `total > 200` (paginate via `?page=` + `?per_page=`).
- Client-side for ≤200 rows (slice the array — avoids the round trip).
- Default page size 25; show `(N of M)` in the footer.

### 5. Sort

- Column-header click toggles asc/desc; arrow icon indicator on
  active column.
- Server-side sort if backend already paginates; otherwise sort the
  client array.
- Default sort = relevance (composite score) where applicable;
  otherwise primary identity column desc.

### 6. Locked identity column

`ticker` is always visible — checkbox disabled with "locked" tag in
the selector. Same for any other primary key (`run_id`, `user_id`).

### 7. Empty state

- During load: skeleton rows (NOT just a spinner) — preserves layout
  shift behaviour.
- After load with zero rows: centred message + primary CTA
  ("Add your first ticker", "Run a screener", etc.).

### 8. Stale-data chip

If any cell value derives from a per-entity aggregate that may use
forward-fill or fallback (portfolio P&L, sentiment label), the
panel-title row gets an amber chip. See
`shared/architecture/portfolio-pl-stale-ticker-chip`.

## Reference implementations

- ScreenerTab (`frontend/app/(authenticated)/analytics/insights/page.tsx`)
- ScreenQLTab (same file, different tab) — dynamic catalog from
  `/insights/screen/fields`
- RecommendationHistoryTab
  (`frontend/components/insights/RecommendationHistoryTab.tsx`)
- Admin Users tab, Admin Audit-log tab

## When NOT to apply

- Catalog has < 8 columns — overhead not worth it.
- Read-only top-of-page widgets (market ticker, KPI cards).
- Charts (use the chart pattern instead).

## Backend pairing

- Endpoint accepts `display_columns: list[str]` when the column set
  influences which Iceberg tables to JOIN (ScreenQL pattern). Cache
  key MUST include the sorted selection or you serve stale subsets.
- For pure-render selectors (Screener), backend returns full row;
  frontend filters.

## Pattern extensions (not yet built)

Good next-application candidates:
- `/admin?tab=observability` — per-model LLM usage table
- `/admin?tab=scheduler` — job runs table
- Portfolio transactions modal if columns grow

## Related

- `shared/architecture/insights-column-selector-pattern` — original
  build write-up with code snippets
- `shared/architecture/portfolio-pl-stale-ticker-chip` — chip pattern
- `shared/conventions/swr-data-fetch-pattern` — how the table hook
  fetches its rows
