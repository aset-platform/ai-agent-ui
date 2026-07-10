---
paths:
  - "frontend/lib/useColumnSelection.ts"
  - "frontend/components/insights/**"
  - "frontend/components/admin/**"
  - "frontend/components/advanced-analytics/**"
  - "frontend/components/common/DownloadCsvButton.tsx"
---

# Tabular pages rules — Insights, Admin (auto-loads when touching table components)

> Lazy-loaded via `paths:` frontmatter. Mirrors what was CLAUDE.md §5.4.
> Deep detail → Serena memory `tabular-page-pattern`.

Every new table/list page (catalog ≥ 8 cols) MUST use:
- `useColumnSelection(storageKey, defaults, validKeys)` (localStorage, SSR-safe)
- `<ColumnSelector lockedKeys={["ticker"]}/>` (popover, category groups, search, reset)
- **Single source of truth**: `visibleCols = allCols.filter(c ∈ selected)` — CSV export uses SAME filter
- `<DownloadCsvButton rows={sortedRows} cols={visibleCols}/>` next to pagination (not header)
- Server-side pagination if `total > 200`; else client-side, default page size 25
- Column-header sort; locked identity column (ticker); skeleton on load, CTA on empty
- Stale-data chip per CLAUDE.md §5.5 when aggregate uses ffill

Reference: ScreenerTab, ScreenQLTab, RecommendationHistoryTab, Admin Users. → `tabular-page-pattern`

**`<ColumnSelector>` popover clips if the trigger sits near the LEFT edge of a scroll container** — the popover always expands via `right-0` by default (safe when the trigger is on the right of its row, the common case). Pass `align="left"` when `<ColumnSelector>` is the FIRST child in a `justify-between` row (paired with a right-aligned download button) — found 2026-07-04 on `TradeLogTable`. → `column-selector-popover-clipping`
