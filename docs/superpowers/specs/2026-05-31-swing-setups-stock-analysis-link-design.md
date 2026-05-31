# Swing Setups → Stock Analysis icon link — design

**Date:** 2026-05-31
**Owner:** Abhay
**Status:** Draft

## Problem

The Advanced Analytics tabbed page renders most tabs via the shared
`AdvancedAnalyticsTable` component, whose ticker column already includes a
small chart icon that links to the Stock Analysis page (`stockAnalysisUrl`,
opens in a new tab). The **Swing Setups** tab renders its own
`<table>` in `SwingSetupsTab.tsx` and therefore lacks that icon — users
can't jump straight from a swing-setup row to its Stock Analysis chart
the way every other AA tab allows.

The link helpers (`stockAnalysisUrl()` and the `ChartIcon` SVG) are
currently **module-local** in `AdvancedAnalyticsTable.tsx` (functions
declared but not exported), so they cannot be reused as-is.

## Goal

Add the same "open Stock Analysis chart" icon to the ticker column of
the Swing Setups table, using the existing convention (icon →
`stockAnalysisUrl(ticker)`, `target="_blank"`, `rel="noopener noreferrer"`).

## Non-goals

- Refactoring Swing Setups to use the shared `AdvancedAnalyticsTable`
  (separate, larger change).
- Visual changes to the icon itself, the URL scheme, or the Stock
  Analysis page.
- Adding the icon to any other tab/component (Insights, Portfolio, etc.).

## Design

### 1. New shared component — `StockAnalysisLink`

**File:** `frontend/components/advanced-analytics/StockAnalysisLink.tsx`

A single-purpose React component that renders the `<a>` + chart icon.
It owns the URL helper and the icon SVG so there is **one source of
truth** for the link convention. Public API:

```tsx
export function stockAnalysisUrl(ticker: string): string;

export function StockAnalysisLink(props: {
  ticker: string;
  testId?: string;
}): JSX.Element;
```

Implementation notes:

- `stockAnalysisUrl(ticker)` — exact behaviour moved verbatim from
  `AdvancedAnalyticsTable.tsx`. Exported so any future caller that
  needs just the URL (e.g. a copy-link affordance) can reuse it.
- `<StockAnalysisLink>` — renders:

  ```tsx
  <a
    href={stockAnalysisUrl(ticker)}
    target="_blank"
    rel="noopener noreferrer"
    title="Open stock analysis chart"
    aria-label={`Open stock analysis for ${ticker}`}
    data-testid={testId ?? `stock-analysis-link-${ticker}`}
    className="text-indigo-500 hover:text-indigo-700
               dark:text-indigo-400 dark:hover:text-indigo-300
               transition-colors"
  >
    <ChartIcon />
  </a>
  ```

  `ChartIcon` (the same SVG as today) is **kept private** to the module —
  no consumer needs it directly.

- The `testId` prop is optional so each consumer can keep its own
  E2E namespace (`aa-chart-link-${ticker}`, `swing-chart-link-${ticker}`,
  etc.). When omitted, it falls back to `stock-analysis-link-${ticker}`.

### 2. Consumer change — `AdvancedAnalyticsTable.tsx`

- Delete the module-local `stockAnalysisUrl` (line ~80) and `ChartIcon`
  (line ~84).
- Replace the inline `<a>…<ChartIcon/></a>` at line ~491 with
  `<StockAnalysisLink ticker={row.ticker} testId={\`aa-chart-link-${row.ticker}\`} />`.
- The existing `aa-chart-link-${ticker}` test ID is preserved, so no
  E2E selector regresses.

### 3. Consumer change — `SwingSetupsTab.tsx`

In the body-cell renderer (currently a single uniform `<td>` mapping in
`SwingSetupsTab.tsx:~190`), special-case the `ticker` column to mirror
AA's inline-flex layout:

```tsx
{c.key === "ticker" ? (
  <span className="inline-flex items-center gap-1.5">
    <StockAnalysisLink
      ticker={row.ticker}
      testId={`swing-chart-link-${row.ticker}`}
    />
    <span className="font-mono">{String(row.ticker)}</span>
  </span>
) : (
  c.fmt ? c.fmt(row[c.key]) : String(row[c.key] ?? "—")
)}
```

The icon sits to the **left** of the ticker text (same visual
convention as the AA table). The new test ID is `swing-chart-link-<ticker>`,
consistent with the existing `swing-row-<ticker>` / `swing-table` naming.

### 4. Accessibility

- `aria-label` set on the `<a>` so screen readers announce
  *"Open stock analysis for INFY"* rather than the bare SVG.
- `title` retained for hover tooltips on pointer devices.
- Existing keyboard-tab order is unchanged (single anchor element).

### 5. Security

- `rel="noopener noreferrer"` retained on the new-tab link
  (matches existing convention; prevents `window.opener` leaks).

## Testing

### Unit (vitest) — `StockAnalysisLink.test.tsx` (new)

- Renders an `<a>` with `href === stockAnalysisUrl(ticker)`.
- Sets `target="_blank"` and `rel="noopener noreferrer"`.
- Sets `aria-label` containing the ticker.
- Uses the default `stock-analysis-link-${ticker}` testid when no
  `testId` prop is passed; uses the provided value when it is.

### E2E (Playwright)

- Existing `aa-chart-link-${ticker}` selector continues to work (no
  regression — `AdvancedAnalyticsTable` keeps that testid via the
  explicit prop).
- Add one assertion to the Swing Setups spec:
  `await expect(page.locator(\`[data-testid="swing-chart-link-${TICKER}"]\`))
   .toHaveAttribute('target', '_blank')`.

## Acceptance criteria

1. New file `StockAnalysisLink.tsx` exists, exports the component +
   URL helper, and renders the documented markup.
2. `AdvancedAnalyticsTable.tsx` no longer declares `stockAnalysisUrl`
   or `ChartIcon` locally; it imports and uses `StockAnalysisLink`.
3. The Swing Setups table renders the same icon, left of the ticker
   text, in the ticker column.
4. Clicking the icon opens the Stock Analysis page for that ticker in
   a new browser tab.
5. Existing `aa-chart-link-${ticker}` E2E selectors still resolve.
6. New unit test for `StockAnalysisLink` passes; existing AA + Swing
   tests still pass.

## Risks

- **None functionally** — the AA path is a behaviour-preserving
  refactor (same markup, same testid via the prop). Only Swing Setups
  gains a new icon.
- Visual: the inline-flex with `gap-1.5` shifts the ticker text
  ~6 px to the right in the Swing column. Acceptable and consistent
  with AA.

## Out of scope (deliberately deferred)

- Migrating Swing Setups to the shared `AdvancedAnalyticsTable`
  (would also give it column selection, CSV export, etc.) — separate
  initiative.
- Extending the icon to other custom tables (Help tab, etc.) — none
  currently render a ticker column outside the shared table.
