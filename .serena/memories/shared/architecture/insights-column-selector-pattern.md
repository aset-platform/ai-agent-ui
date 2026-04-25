# Insights column selector pattern

Introduced by ASETPLTFRM-333. Reusable for any table with many candidate columns where users want to customise their view.

## Components

### `frontend/lib/useColumnSelection.ts` (hook)
localStorage-backed `[selected, setSelected, reset]`. Two-phase hydration (SSR defaults → client localStorage on first effect) avoids hydration mismatches. Tolerates stale keys via `validKeys` filter on load — catalog can evolve without breaking old selections.

Signature:
```ts
useColumnSelection(
  storageKey: string,         // e.g. "insights.columns.screener"
  defaults: string[],         // default visible columns
  validKeys: string[],        // current catalog — filter stale keys
): [selected, setSelected, reset]
```

### `frontend/components/insights/ColumnSelector.tsx` (component)
Reusable popover. Renders a grouped checkbox list with per-category select-all toggle, search box, global select/deselect, reset-to-defaults.

Props:
```tsx
<ColumnSelector
  catalog={specs}             // { key, label, category }[]
  selected={selected}
  onChange={setSelected}
  onReset={reset}
  lockedKeys={["ticker"]}
  buttonLabel="Columns"       // optional trigger label
/>
```

Testids: `column-selector-trigger`, `column-selector-popover`, `col-toggle-<key>`.

## Applied on two surfaces

### Screener tab
- Static catalog `SCREENER_COL_CATALOG` in page.tsx (39 entries, 8 categories).
- Defaults = the pre-selector 13 columns (zero regression).
- `visibleCols = screenerCols.filter(key ∈ selected)` — single source of truth, CSV export mirrors.

### ScreenQL tab
- Catalog built dynamically from `/v1/insights/screen/fields` response (37 entries, 7 categories).
- Selection passed as `display_columns: string[]` on the `/screen` request body.
- `generate_sql(..., display_columns=)` in `screen_parser.py` merges selection with filter-referenced fields (dedup + auto-resolves join tables + silently ignores unknown names).
- Cache key in endpoint includes the sorted selection so toggling re-runs rather than serving stale subset.
- Selection change auto-re-fires the current query via an effect.

## Design principles

1. **Zero regression default** — the pre-feature column set is the on-load default. Users opt in to more.
2. **Locked identity columns** — `ticker` always visible, checkbox disabled with "locked" tag.
3. **Category grouping from catalog** — same 7-category taxonomy across both tabs (Identity / Pricing / Valuation / Profitability / Risk / Technical / Quality / Forecast).
4. **Tolerant of catalog evolution** — old localStorage selections with stale keys are filtered on load, never throw.
5. **CSV mirrors visible columns** — `DownloadCsvButton` uses the filtered column list, not the full master.

## Where this pattern fits next

Good candidates for applying the same selector:
- `/admin?tab=users` — many user profile fields
- `/admin?tab=observability` — per-model LLM usage table
- `/admin?tab=scheduler` — job runs table
- Portfolio transactions modal (if column count grows)

Don't apply to tables with < ~8 potential columns — the UX overhead isn't worth it.

## Deferred enhancements (not blocking)

- Unit tests for `useColumnSelection` — pure logic, easy add
- Column drag-reorder — separate UX polish ticket
- Saved presets (named sets, shareable) — localStorage alone is fine for solo use today

## Live verification (commit fd10a7d on feature/sprint8)

Screener: trigger shows `Columns (14/39)`, 8 category groups with counts, 39 toggles.
ScreenQL: `peg_ratio < 1` + selected `[piotroski_score, beta, eps]` → `columns_used = [peg_ratio, piotroski_score, beta, eps]`, all values populated per row.
