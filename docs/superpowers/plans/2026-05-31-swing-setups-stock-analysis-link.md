# Swing Setups → Stock Analysis Link — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a shared `StockAnalysisLink` React component and use it in both `AdvancedAnalyticsTable` (replacing inline helpers) and `SwingSetupsTab` (new — gives the Swing Setups table the same "open in new tab" Stock Analysis icon every other AA tab already has).

**Architecture:** New small component owns the URL helper (`stockAnalysisUrl`), the chart-icon SVG (`ChartIcon`, private), and the link markup with sane a11y/security defaults (`target="_blank"`, `rel="noopener noreferrer"`, `aria-label`). Consumers pass `ticker` and an optional `testId` so each table keeps its own E2E namespace.

**Tech Stack:** Next.js / React (TypeScript), Vitest + @testing-library/react for unit tests, Playwright for E2E (defer new spec — registry entry only).

**Spec:** `docs/superpowers/specs/2026-05-31-swing-setups-stock-analysis-link-design.md`

**Spec amendment baked into this plan:** the spec mentioned "add one assertion to the Swing Setups E2E spec," but no such spec currently exists. The plan adds the selector to the central registry (`e2e/utils/selectors.ts`) so a future swing E2E spec can use it; creating a new spec/POM is deferred (separate ticket).

---

## File structure

- **Create** `frontend/components/advanced-analytics/StockAnalysisLink.tsx` — single shared component (~40 lines) + `stockAnalysisUrl` helper + private `ChartIcon` SVG.
- **Create** `frontend/components/advanced-analytics/StockAnalysisLink.test.tsx` — vitest + RTL, 4 assertions covering href / target+rel / aria-label / testId fallback+override.
- **Modify** `frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx` — remove local `stockAnalysisUrl` (line ~80) + `ChartIcon` (line ~84); import + use `<StockAnalysisLink/>` at the ticker-column anchor (line ~491). The existing `aa-chart-link-${ticker}` testid is preserved via the explicit `testId` prop.
- **Modify** `frontend/components/advanced-analytics/SwingSetupsTab.tsx` — special-case the `ticker` cell renderer (~line 190) to wrap the ticker text in an `inline-flex` span with `<StockAnalysisLink testId={\`swing-chart-link-${ticker}\`}/>` to its left.
- **Modify** `e2e/utils/selectors.ts` — add `swingChartLink: (t: string) => \`swing-chart-link-${t}\`` to the `FE` object near the existing `swing*` entries.

---

## Task 1: Create `StockAnalysisLink` component (TDD)

**Files:**
- Create: `frontend/components/advanced-analytics/StockAnalysisLink.tsx`
- Create: `frontend/components/advanced-analytics/StockAnalysisLink.test.tsx`

- [ ] **Step 1.1: Write the failing test**

Create `frontend/components/advanced-analytics/StockAnalysisLink.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  StockAnalysisLink,
  stockAnalysisUrl,
} from "./StockAnalysisLink";

describe("stockAnalysisUrl", () => {
  it("returns a URL containing the ticker", () => {
    const url = stockAnalysisUrl("INFY.NS");
    expect(url).toContain("INFY.NS");
  });
});

describe("<StockAnalysisLink />", () => {
  it("renders an anchor with the correct href, target, rel and aria-label", () => {
    render(<StockAnalysisLink ticker="INFY.NS" />);
    const a = screen.getByRole("link");
    expect(a).toHaveAttribute("href", stockAnalysisUrl("INFY.NS"));
    expect(a).toHaveAttribute("target", "_blank");
    expect(a).toHaveAttribute("rel", "noopener noreferrer");
    expect(a).toHaveAttribute(
      "aria-label",
      "Open stock analysis for INFY.NS",
    );
  });

  it("uses the default testid when none is supplied", () => {
    render(<StockAnalysisLink ticker="ITC.NS" />);
    expect(
      screen.getByTestId("stock-analysis-link-ITC.NS"),
    ).toBeInTheDocument();
  });

  it("uses the supplied testid when provided", () => {
    render(
      <StockAnalysisLink ticker="ITC.NS" testId="aa-chart-link-ITC.NS" />,
    );
    expect(
      screen.getByTestId("aa-chart-link-ITC.NS"),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 1.2: Run the test — expect FAIL**

```bash
cd frontend && npx vitest run components/advanced-analytics/StockAnalysisLink.test.tsx
```

Expected: FAIL — module `./StockAnalysisLink` does not exist.

- [ ] **Step 1.3: Implement the minimal component**

Create `frontend/components/advanced-analytics/StockAnalysisLink.tsx`:

```tsx
/**
 * Shared "Open Stock Analysis chart" icon link used by AA tabs.
 *
 * One source of truth for the URL scheme, the chart-icon glyph, and
 * the new-tab a11y/security attributes. Consumers pass the ticker
 * and an optional `testId` so each table keeps its own E2E namespace.
 */
import React from "react";

export function stockAnalysisUrl(ticker: string): string {
  return `/analytics/analysis?ticker=${encodeURIComponent(ticker)}`;
}

function ChartIcon() {
  // 14x14 outline chart glyph — matches the original AA icon.
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 3v18h18" />
      <path d="M7 15l4-4 4 4 5-7" />
    </svg>
  );
}

export interface StockAnalysisLinkProps {
  ticker: string;
  /** Override the default `stock-analysis-link-<ticker>` test ID. */
  testId?: string;
}

export function StockAnalysisLink({
  ticker,
  testId,
}: StockAnalysisLinkProps) {
  return (
    <a
      href={stockAnalysisUrl(ticker)}
      target="_blank"
      rel="noopener noreferrer"
      title="Open stock analysis chart"
      aria-label={`Open stock analysis for ${ticker}`}
      data-testid={testId ?? `stock-analysis-link-${ticker}`}
      className="text-indigo-500 hover:text-indigo-700 dark:text-indigo-400 dark:hover:text-indigo-300 transition-colors"
    >
      <ChartIcon />
    </a>
  );
}
```

> **Engineer note:** verify the URL scheme on line `stockAnalysisUrl` matches what's currently produced by the inline helper in `AdvancedAnalyticsTable.tsx` (look at line ~80). If the path/query differs (e.g. `/analytics/stock/...`, or different param name), copy the existing helper verbatim instead of using the literal above. The unit test above is loose enough (`toContain(ticker)`) that it will pass either way, but Task 2's refactor depends on **bit-for-bit URL equality**.

- [ ] **Step 1.4: Run the test — expect PASS**

```bash
cd frontend && npx vitest run components/advanced-analytics/StockAnalysisLink.test.tsx
```

Expected: PASS — 4 tests across 2 describes.

- [ ] **Step 1.5: Commit**

```bash
git add frontend/components/advanced-analytics/StockAnalysisLink.tsx \
        frontend/components/advanced-analytics/StockAnalysisLink.test.tsx
git commit -m "feat(advanced-analytics): add shared StockAnalysisLink component"
```

---

## Task 2: Refactor `AdvancedAnalyticsTable` to use the shared component

**Files:**
- Modify: `frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx`

This is a behaviour-preserving refactor — same markup, same `aa-chart-link-${ticker}` test ID via the new `testId` prop. No new tests; correctness verified by type-check and a string-presence grep.

- [ ] **Step 2.1: Confirm the existing URL helper matches `StockAnalysisLink.stockAnalysisUrl`**

```bash
sed -n '78,90p' frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx
```

Compare the body of `function stockAnalysisUrl` to the one created in Task 1. If they differ, **update Task 1's helper to match the existing AA helper character-for-character** (re-run Task 1 tests after editing). Do NOT change the URL during this refactor — Task 2's whole purpose is to be invisible to users and to E2E.

- [ ] **Step 2.2: Remove the local helpers**

In `frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx`, delete the two module-local declarations:

```ts
// DELETE these two functions (current lines ~78-92, exact location may shift):
function stockAnalysisUrl(ticker: string): string { /* ... */ }
function ChartIcon() { /* ... */ }
```

- [ ] **Step 2.3: Add the import**

Near the top of the file, alongside the existing imports, add:

```tsx
import { StockAnalysisLink } from "./StockAnalysisLink";
```

- [ ] **Step 2.4: Replace the inline anchor at the ticker column**

In `AdvancedAnalyticsTable.tsx` the ticker cell currently contains (around line 491–500):

```tsx
{col.key === "ticker" ? (
  <span className="inline-flex items-center gap-1.5">
    <a
      href={stockAnalysisUrl(row.ticker)}
      target="_blank"
      rel="noopener noreferrer"
      title="Open stock analysis chart"
      data-testid={`aa-chart-link-${row.ticker}`}
      className="text-indigo-500 hover:text-indigo-700 dark:text-indigo-400 dark:hover:text-indigo-300 transition-colors"
    >
      <ChartIcon />
    </a>
    <span className="font-mono">{text}</span>
    {/* ...golden-cross pills etc... */}
  </span>
) : ...}
```

Replace the inner `<a>...</a>` (and only the anchor — leave the surrounding `<span>` and the golden-cross siblings intact) with:

```tsx
<StockAnalysisLink
  ticker={row.ticker}
  testId={`aa-chart-link-${row.ticker}`}
/>
```

- [ ] **Step 2.5: Type-check + lint + grep for regressions**

```bash
cd frontend && npx tsc --noEmit && npx eslint components/advanced-analytics/AdvancedAnalyticsTable.tsx
```

Expected: clean (no type errors, no lint errors).

```bash
grep -nE "function stockAnalysisUrl|function ChartIcon" \
  frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx
```

Expected: no matches (helpers removed).

```bash
grep -n "aa-chart-link" frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx
```

Expected: one match on the `testId={...}` line (E2E selector preserved).

- [ ] **Step 2.6: Re-run the unit suite to confirm no collateral damage**

```bash
cd frontend && npx vitest run
```

Expected: all green (including the 4 `StockAnalysisLink` tests from Task 1).

- [ ] **Step 2.7: Commit**

```bash
git add frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx
git commit -m "refactor(advanced-analytics): use shared StockAnalysisLink"
```

---

## Task 3: Add `StockAnalysisLink` to `SwingSetupsTab`

**Files:**
- Modify: `frontend/components/advanced-analytics/SwingSetupsTab.tsx`

- [ ] **Step 3.1: Add the import**

In `frontend/components/advanced-analytics/SwingSetupsTab.tsx`, alongside the existing imports near the top, add:

```tsx
import { StockAnalysisLink } from "./StockAnalysisLink";
```

- [ ] **Step 3.2: Special-case the ticker cell in the body renderer**

The current cell renderer (around line 190) is:

```tsx
<td
  key={c.key as string}
  className="px-3 py-2 text-slate-800 dark:text-slate-100"
>
  {c.fmt
    ? c.fmt(row[c.key])
    : String(row[c.key] ?? "—")}
</td>
```

Replace it with:

```tsx
<td
  key={c.key as string}
  className="px-3 py-2 text-slate-800 dark:text-slate-100"
>
  {c.key === "ticker" ? (
    <span className="inline-flex items-center gap-1.5">
      <StockAnalysisLink
        ticker={row.ticker}
        testId={`swing-chart-link-${row.ticker}`}
      />
      <span className="font-mono">{String(row.ticker)}</span>
    </span>
  ) : (
    c.fmt
      ? c.fmt(row[c.key])
      : String(row[c.key] ?? "—")
  )}
</td>
```

The icon sits to the **left** of the ticker text (same convention as `AdvancedAnalyticsTable`). The default `c.fmt`/`String` path is preserved for every other column.

- [ ] **Step 3.3: Type-check + lint**

```bash
cd frontend && npx tsc --noEmit && npx eslint components/advanced-analytics/SwingSetupsTab.tsx
```

Expected: clean.

- [ ] **Step 3.4: Visual smoke (manual)**

Start the dev stack if not already running:

```bash
./run.sh status   # confirm frontend is up; if not: ./run.sh start
```

Open `http://localhost:3000/advanced-analytics`, switch to the **Swing Setups** tab, pick a regime pill (Bull / Sideways / Bearish), and confirm:

- A small chart icon appears immediately to the left of each ticker in the table.
- Clicking the icon opens the Stock Analysis page for that ticker **in a new browser tab**.
- The ticker text and every other column are unchanged.

- [ ] **Step 3.5: Re-run the unit suite**

```bash
cd frontend && npx vitest run
```

Expected: all green.

- [ ] **Step 3.6: Commit**

```bash
git add frontend/components/advanced-analytics/SwingSetupsTab.tsx
git commit -m "feat(swing-setups): add stock-analysis icon link to ticker column"
```

---

## Task 4: Register the new E2E selector

**Files:**
- Modify: `e2e/utils/selectors.ts`

Adds the selector to the central registry so a future Swing Setups E2E spec can use it. No new spec is created here (see plan header for rationale).

- [ ] **Step 4.1: Add the selector**

In `e2e/utils/selectors.ts`, near the existing `swing*` block (around line 256–264), add a new entry to the `FE` object:

```ts
swingChartLink: (t: string) => `swing-chart-link-${t}`,
```

Place it next to `swingTable` so the namespace stays grouped.

- [ ] **Step 4.2: Verify the registry compiles**

```bash
cd e2e && npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 4.3: Commit**

```bash
git add e2e/utils/selectors.ts
git commit -m "test(e2e): register swingChartLink selector"
```

---

## Verification (after all tasks complete)

- [ ] **All unit tests green**

```bash
cd frontend && npx vitest run
```

Expected: PASS, including the 4 `StockAnalysisLink` tests added in Task 1.

- [ ] **Type-check + lint clean across the touched files**

```bash
cd frontend && npx tsc --noEmit && \
  npx eslint components/advanced-analytics/StockAnalysisLink.tsx \
              components/advanced-analytics/AdvancedAnalyticsTable.tsx \
              components/advanced-analytics/SwingSetupsTab.tsx
cd ../e2e && npx tsc --noEmit
```

- [ ] **No leftover local helpers in AdvancedAnalyticsTable**

```bash
grep -cE "function (stockAnalysisUrl|ChartIcon)" \
  frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx
```

Expected: `0`.

- [ ] **Existing AA testid preserved**

```bash
grep -c "aa-chart-link-" \
  frontend/components/advanced-analytics/AdvancedAnalyticsTable.tsx
```

Expected: `>= 1`.

- [ ] **Manual UI confirmation**

- Visit `/advanced-analytics` → every tab that previously had the icon still does.
- Visit `/advanced-analytics` → Swing Setups → icons now appear; click → new tab.

---

## Out of scope (explicit)

- Migrating Swing Setups to the shared `AdvancedAnalyticsTable` (would also bring column selection / CSV export — separate ticket).
- Creating a new Playwright spec for the Swing Setups tab (none exists today; the selector is registered for the future ticket).
- Visual changes to the icon, URL scheme, or Stock Analysis page.

---

## Self-review (notes from the plan author)

- **Spec coverage:** the four spec sections (new component, AA refactor, Swing integration, testing) each map to one task. The E2E "add assertion" requirement is partially fulfilled (registry entry only) with a clear scope amendment at the top of this plan.
- **Placeholders:** every code step contains the actual code or command. Step 2.1 includes a *contingency* (engineer must verify URL helper parity character-for-character) rather than a placeholder — this is real verification work, not a TBD.
- **Type/name consistency:** `StockAnalysisLink`, `stockAnalysisUrl`, the `testId` prop, and the test-IDs (`stock-analysis-link-`, `aa-chart-link-`, `swing-chart-link-`) are used identically across all tasks and match the spec.
- **Scope:** four small, ordered, independently committable tasks; total ~70 lines added net, two files modified, one selectors entry. Each task ends with a commit per the "frequent commits" principle.
