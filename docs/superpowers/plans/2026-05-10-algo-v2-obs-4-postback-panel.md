# Algo Trading v2 — Slice OBS-4: Kite Postback Observability Panel

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `KitePostbackPanel` to the Trading tab Live segment showing the last 50
Kite postback events with a raw-payload toggle — gives glance-able confirmation that
postbacks are flowing and surfaces a troubleshooting hint when nothing is received.

**Architecture:** SWR hook polls `GET /v1/algo/live/postbacks?limit=50` (implemented
in OBS-2) every 30 s. Component mirrors `ReconciliationDriftPanel` UX: header + table
+ per-row JSON expand + amber empty state. Mounted in `PaperTab.tsx` inside the
`viewMode === "live"` branch only; hidden in Paper and Dry-run segments.

**Tech Stack:** Next.js 16 / React 19 / SWR / TailwindCSS / Vitest + Testing Library
/ Playwright.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-v2-observability-postback-design.md`
— §3.6.

**Branch:** `feature/algo-v2-obs-4-postback-panel` off
`feature/algo-trading-v2-integration`.

**Depends on:** OBS-2 (`GET /v1/algo/live/postbacks?limit=50` endpoint must exist).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `e2e/utils/selectors.ts` | Modify | Add 4 new `FE.*` testid constants |
| `frontend/hooks/useKitePostbacks.ts` | Create | SWR hook + `KitePostback` TS type |
| `frontend/hooks/__tests__/useKitePostbacks.test.ts` | Create | Hook unit tests |
| `frontend/components/algo-trading/KitePostbackPanel.tsx` | Create | Table + payload toggle + states |
| `frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx` | Create | Component unit tests |
| `frontend/components/algo-trading/PaperTab.tsx` | Modify | Mount panel in Live segment only |
| `e2e/tests/frontend/algo-trading-postback-panel.spec.ts` | Create | Playwright E2E |

---

## Task 1: Register testids in `selectors.ts`

> Register `data-testid` constants BEFORE writing the component — this is required by
> CLAUDE.md §5.14. The `FE` object in `e2e/utils/selectors.ts` is the single source
> of truth for all Playwright selectors.

**Files:**
- Modify: `e2e/utils/selectors.ts`

- [ ] **Step 1: Write failing test** (Playwright — verify the keys exist in the object)

There is no separate test for this step; the Playwright spec in Task 11 will fail to
compile if these keys are absent. Instead, do a manual grep to confirm they are
missing before adding them:

```bash
grep -n "kite-postback" /Users/abhay/Documents/projects/ai-agent-ui/e2e/utils/selectors.ts
# Expected output: (empty — keys don't exist yet)
```

- [ ] **Step 2: Add the four new keys to the `FE` object**

Open `e2e/utils/selectors.ts`. Locate the `// ── Algo Trading — Reconciliation (V2-3) ─────`
block (currently the last algo section, ending near line 344). Append the new block
**before** the closing `} as const;` on the final line:

```typescript
  // ── Algo Trading — Kite Postback Panel (OBS-4) ──────
  kitePostbackPanel: "kite-postback-panel",
  kitePostbackRow: "kite-postback-row",
  kitePostbackPayloadToggle: "kite-postback-payload-toggle",
  kitePostbackEmptyState: "kite-postback-empty-state",
```

The closing of the file after the edit should look like:

```typescript
  // ── Algo Trading — Reconciliation (V2-3) ─────
  algoReconciliationPanel: "reconciliation-drift-panel",
  algoReconciliationChip: "reconciliation-drift-chip",
  algoReconciliationToggle: "reconciliation-drift-toggle",
  algoReconciliationTable: "reconciliation-drift-table",
  algoDriftThresholdWidget: "drift-threshold-widget",
  algoDriftThresholdInput: "drift-threshold-input",
  algoDriftThresholdSave: "drift-threshold-save",

  // ── Algo Trading — Kite Postback Panel (OBS-4) ──────
  kitePostbackPanel: "kite-postback-panel",
  kitePostbackRow: "kite-postback-row",
  kitePostbackPayloadToggle: "kite-postback-payload-toggle",
  kitePostbackEmptyState: "kite-postback-empty-state",
} as const;
```

- [ ] **Step 3: Verify the keys are present**

```bash
grep -n "kite-postback" /Users/abhay/Documents/projects/ai-agent-ui/e2e/utils/selectors.ts
```

Expected output (4 lines, line numbers will vary):

```
348:  kitePostbackPanel: "kite-postback-panel",
349:  kitePostbackRow: "kite-postback-row",
350:  kitePostbackPayloadToggle: "kite-postback-payload-toggle",
351:  kitePostbackEmptyState: "kite-postback-empty-state",
```

- [ ] **Step 4: TypeScript-compile check**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/e2e
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add e2e/utils/selectors.ts
git commit -m "$(cat <<'EOF'
feat(obs-4): register kite-postback testid constants in selectors.ts

Adds kitePostbackPanel, kitePostbackRow, kitePostbackPayloadToggle,
kitePostbackEmptyState to the FE object per CLAUDE.md §5.14.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: `KitePostback` TypeScript type + `useKitePostbacks` hook

> SWR hook that calls `GET /v1/algo/live/postbacks?limit=50`. Returns
> `{ postbacks, isLoading, error, mutate }`. Mirrors the `useReconciliation` hook
> pattern at `frontend/hooks/useReconciliation.ts`.

**Files:**
- Create: `frontend/hooks/useKitePostbacks.ts`
- Create: `frontend/hooks/__tests__/useKitePostbacks.test.ts`

- [ ] **Step 1: Write the failing hook test**

Create `frontend/hooks/__tests__/useKitePostbacks.test.ts`:

```typescript
// frontend/hooks/__tests__/useKitePostbacks.test.ts
/**
 * Unit tests for useKitePostbacks.
 * SWR is mocked so tests never hit the network.
 */
import { describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

// Mock SWR before importing the hook.
vi.mock("swr", () => ({
  default: vi.fn(),
}));

// Mock apiFetch to prevent real HTTP calls.
vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

// Mock config to give a deterministic API_URL.
vi.mock("@/lib/config", () => ({
  API_URL: "http://localhost:8181/v1",
}));

import useSWR from "swr";
import { useKitePostbacks } from "@/hooks/useKitePostbacks";

const mockUseSWR = useSWR as ReturnType<typeof vi.fn>;

const SAMPLE_POSTBACK = {
  event_ts: "2026-05-10T09:30:00Z",
  tradingsymbol: "RELIANCE.NS",
  status: "COMPLETE",
  filled_quantity: 5,
  average_price: 2950.75,
  raw: {
    order_id: "240510000111111",
    guid: "abc-123",
    status: "COMPLETE",
    tradingsymbol: "RELIANCE.NS",
    filled_quantity: 5,
    average_price: 2950.75,
    checksum: "deadbeef",
  },
};

describe("useKitePostbacks", () => {
  it("returns empty array and isLoading=true while fetching", () => {
    mockUseSWR.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
      mutate: vi.fn(),
    });

    const { result } = renderHook(() => useKitePostbacks());

    expect(result.current.postbacks).toEqual([]);
    expect(result.current.isLoading).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("returns populated array when data is available", () => {
    mockUseSWR.mockReturnValue({
      data: [SAMPLE_POSTBACK],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });

    const { result } = renderHook(() => useKitePostbacks());

    expect(result.current.postbacks).toHaveLength(1);
    expect(result.current.postbacks[0].tradingsymbol).toBe(
      "RELIANCE.NS",
    );
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("returns error string when SWR errors", () => {
    mockUseSWR.mockReturnValue({
      data: undefined,
      error: new Error("HTTP 500"),
      isLoading: false,
      mutate: vi.fn(),
    });

    const { result } = renderHook(() => useKitePostbacks());

    expect(result.current.postbacks).toEqual([]);
    expect(result.current.error).toBe("HTTP 500");
  });

  it("uses the correct SWR key", () => {
    mockUseSWR.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });

    renderHook(() => useKitePostbacks());

    const [key] = mockUseSWR.mock.calls[0];
    expect(key).toBe(
      "http://localhost:8181/v1/algo/live/postbacks?limit=50",
    );
  });

  it("passes revalidateOnFocus: false to SWR", () => {
    mockUseSWR.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });

    renderHook(() => useKitePostbacks());

    const [, , opts] = mockUseSWR.mock.calls[0];
    expect(opts.revalidateOnFocus).toBe(false);
    expect(opts.refreshInterval).toBe(30_000);
  });

  it("exposes mutate for manual revalidation", () => {
    const mockMutate = vi.fn();
    mockUseSWR.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
      mutate: mockMutate,
    });

    const { result } = renderHook(() => useKitePostbacks());

    expect(result.current.mutate).toBe(mockMutate);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx vitest run hooks/__tests__/useKitePostbacks.test.ts
```

Expected: FAIL — `Cannot find module '@/hooks/useKitePostbacks'`

- [ ] **Step 3: Implement `useKitePostbacks.ts`**

Create `frontend/hooks/useKitePostbacks.ts`:

```typescript
"use client";
/**
 * SWR hook for Kite postback events (OBS-4).
 *
 * Polls GET /v1/algo/live/postbacks?limit=50 every 30 s.
 * The endpoint is implemented in OBS-2 and filters by
 * the authenticated user automatically.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

/** Subset of the kite_postback_received event payload
 *  returned by GET /v1/algo/live/postbacks. */
export interface KitePostback {
  /** ISO 8601 UTC timestamp of when the postback was received. */
  event_ts: string;
  tradingsymbol: string;
  /** COMPLETE | REJECTED | CANCELLED | UPDATE */
  status: string;
  filled_quantity: number;
  average_price: number;
  /** Full raw Kite postback payload for the JSON expand row. */
  raw: Record<string, unknown>;
}

const KEY = `${API_URL}/algo/live/postbacks?limit=50`;

async function fetcher(url: string): Promise<KitePostback[]> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useKitePostbacks() {
  const { data, error, isLoading, mutate } = useSWR<KitePostback[]>(
    KEY,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 30_000,
      dedupingInterval: 15_000,
    },
  );

  return {
    postbacks: data ?? [],
    isLoading,
    error: error instanceof Error ? error.message : null,
    mutate,
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx vitest run hooks/__tests__/useKitePostbacks.test.ts
```

Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/hooks/useKitePostbacks.ts \
        frontend/hooks/__tests__/useKitePostbacks.test.ts
git commit -m "$(cat <<'EOF'
feat(obs-4): add useKitePostbacks SWR hook + KitePostback type

30 s polling, revalidateOnFocus: false, key /algo/live/postbacks?limit=50.
Unit tests cover loading / populated / error states and SWR config.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: `KitePostbackPanel` component — loading skeleton

> Lighthouse FCP heuristic (CLAUDE.md §5.3): loading shells MUST include text,
> not just CSS divs. The skeleton renders "Loading postbacks…" so FCP fires.

**Files:**
- Create: `frontend/components/algo-trading/KitePostbackPanel.tsx`
  (initially only the loading branch; tasks 4-7 fill in the rest)
- Create: `frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx`

- [ ] **Step 1: Write the failing loading-state test**

Create `frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx`:

```tsx
// frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx
/**
 * Vitest unit tests for KitePostbackPanel (OBS-4).
 *
 * Tests (added task-by-task):
 *  Task 3: loading skeleton renders text for FCP.
 *  Task 4: empty state renders amber troubleshooting card.
 *  Task 5: populated state renders postback rows.
 *  Task 6: status badge colour classes.
 *  Task 7: payload toggle expand / collapse; single-row-at-a-time.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

afterEach(() => cleanup());

vi.mock("@/hooks/useKitePostbacks", () => ({
  useKitePostbacks: vi.fn(),
}));

import { useKitePostbacks } from "@/hooks/useKitePostbacks";
import { KitePostbackPanel } from "../KitePostbackPanel";

const mockHook = useKitePostbacks as ReturnType<typeof vi.fn>;

// ── Task 3 ──────────────────────────────────────────────────
describe("KitePostbackPanel — loading state", () => {
  it("renders text while loading so Lighthouse FCP fires", () => {
    mockHook.mockReturnValue({
      postbacks: [],
      isLoading: true,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    // Must contain a text node — not just a CSS shimmer div.
    expect(screen.getByText(/loading postbacks/i)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx vitest run \
  components/algo-trading/__tests__/KitePostbackPanel.test.tsx \
  --reporter=verbose 2>&1 | head -30
```

Expected: FAIL — `Cannot find module '../KitePostbackPanel'`

- [ ] **Step 3: Implement the loading skeleton**

Create `frontend/components/algo-trading/KitePostbackPanel.tsx`:

```tsx
"use client";
/**
 * KitePostbackPanel — OBS-4.
 *
 * Shows the last 50 Kite postback events for the authenticated user.
 * Mounts only in the Live segment of PaperTab (hidden in Paper /
 * Dry-run — postbacks require real Kite orders).
 *
 * UX pattern: mirrors ReconciliationDriftPanel (table + per-row
 * JSON expand + amber empty state with troubleshooting hint).
 */

import { useState } from "react";

import {
  useKitePostbacks,
  type KitePostback,
} from "@/hooks/useKitePostbacks";

// ── Status badge ────────────────────────────────────────────

type PostbackStatus = "COMPLETE" | "REJECTED" | "CANCELLED" | "UPDATE";

function StatusBadge({ status }: { status: string }) {
  const s = status.toUpperCase() as PostbackStatus;
  const cls =
    s === "COMPLETE"
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300"
      : s === "REJECTED"
        ? "bg-rose-100 text-rose-800 dark:bg-rose-950/50 dark:text-rose-300"
        : s === "CANCELLED"
          ? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400"
          : /* UPDATE */
            "bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300";

  return (
    <span
      className={`inline-flex items-center rounded-full px-1.5 py-0.5
        text-[10px] font-medium ${cls}`}
    >
      {s}
    </span>
  );
}

// ── Helpers ─────────────────────────────────────────────────

/** Formats an ISO 8601 UTC string to IST local time (HH:MM:SS). */
function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return iso;
  }
}

/** Full absolute datetime for the title tooltip. */
function fmtAbsolute(iso: string): string {
  try {
    return new Date(iso).toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      dateStyle: "short",
      timeStyle: "medium",
    });
  } catch {
    return iso;
  }
}

// ── Sub-components ───────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div
      className="space-y-1"
      aria-busy="true"
      aria-label="Loading postbacks"
    >
      {/* Text node so Lighthouse FCP fires — per CLAUDE.md §5.3. */}
      <p className="text-xs text-slate-500">Loading postbacks…</p>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-8 animate-pulse rounded bg-slate-100
            dark:bg-slate-800"
        />
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div
      className="rounded-md border border-amber-300 bg-amber-50
        p-3 text-xs text-amber-800 dark:border-amber-700
        dark:bg-amber-950/20 dark:text-amber-300"
      data-testid="kite-postback-empty-state"
    >
      No postbacks received. Either no live orders placed today,
      postbacks not yet enabled (
      <code className="rounded bg-amber-100 px-1 dark:bg-amber-900/40">
        KITE_POSTBACK_ENABLED
      </code>
      ), or ngrok tunnel down — check{" "}
      <a
        href="http://localhost:4040"
        target="_blank"
        rel="noreferrer"
        className="underline"
      >
        http://localhost:4040
      </a>
      .
    </div>
  );
}

interface PostbackRowProps {
  postback: KitePostback;
  expanded: boolean;
  onToggle: () => void;
}

function PostbackRow({ postback, expanded, onToggle }: PostbackRowProps) {
  return (
    <>
      <tr
        className="border-b border-slate-100 hover:bg-slate-50
          dark:border-slate-800 dark:hover:bg-slate-800/50"
        data-testid="kite-postback-row"
      >
        <td
          className="px-3 py-1.5 text-[11px] text-slate-500"
          title={fmtAbsolute(postback.event_ts)}
        >
          {fmtTime(postback.event_ts)}
        </td>
        <td className="px-3 py-1.5 font-mono text-xs font-semibold
          text-slate-900 dark:text-slate-100">
          {postback.tradingsymbol}
        </td>
        <td className="px-3 py-1.5">
          <StatusBadge status={postback.status} />
        </td>
        <td className="px-3 py-1.5 text-right text-xs text-slate-700
          dark:text-slate-300">
          {postback.filled_quantity}
        </td>
        <td className="px-3 py-1.5 text-right text-xs text-slate-700
          dark:text-slate-300">
          {postback.average_price > 0
            ? `₹${postback.average_price.toFixed(2)}`
            : "—"}
        </td>
        <td className="px-3 py-1.5 text-center">
          <button
            type="button"
            onClick={onToggle}
            className="text-[10px] text-slate-400 hover:text-slate-700
              dark:hover:text-slate-200"
            aria-expanded={expanded}
            aria-label={expanded ? "Collapse payload" : "Expand payload"}
            data-testid="kite-postback-payload-toggle"
          >
            {expanded ? "▾" : "▸"}
          </button>
        </td>
      </tr>

      {expanded && (
        <tr className="bg-slate-50 dark:bg-slate-900/60">
          <td
            colSpan={6}
            className="px-3 py-2"
          >
            <pre
              className="overflow-x-auto whitespace-pre-wrap break-all
                rounded border border-slate-200 bg-white p-2
                text-[10px] leading-relaxed text-slate-700
                dark:border-slate-700 dark:bg-slate-900
                dark:text-slate-300"
            >
              {JSON.stringify(postback.raw, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Main component ───────────────────────────────────────────

export function KitePostbackPanel() {
  const { postbacks, isLoading, error } = useKitePostbacks();
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const handleToggle = (idx: number) => {
    setExpandedIdx((prev) => (prev === idx ? null : idx));
  };

  return (
    <div
      className="rounded-md border border-slate-200
        dark:border-slate-700"
      data-testid="kite-postback-panel"
    >
      <div className="border-b border-slate-200 px-3 py-2
        dark:border-slate-700">
        <h4 className="text-xs font-semibold uppercase tracking-wide
          text-slate-500 dark:text-slate-400">
          Kite postbacks
          {postbacks.length > 0 && (
            <span className="ml-1.5 text-slate-400">
              ({postbacks.length})
            </span>
          )}
        </h4>
      </div>

      <div className="p-3">
        {isLoading && postbacks.length === 0 && <LoadingSkeleton />}

        {error && (
          <p className="text-xs text-rose-600 dark:text-rose-400">
            {error}
          </p>
        )}

        {!isLoading && !error && postbacks.length === 0 && (
          <EmptyState />
        )}

        {postbacks.length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead>
                <tr className="border-b border-slate-100
                  dark:border-slate-800">
                  <th className="px-3 py-1.5 text-left text-[10px]
                    font-medium uppercase tracking-wide text-slate-400">
                    Time (IST)
                  </th>
                  <th className="px-3 py-1.5 text-left text-[10px]
                    font-medium uppercase tracking-wide text-slate-400">
                    Symbol
                  </th>
                  <th className="px-3 py-1.5 text-left text-[10px]
                    font-medium uppercase tracking-wide text-slate-400">
                    Status
                  </th>
                  <th className="px-3 py-1.5 text-right text-[10px]
                    font-medium uppercase tracking-wide text-slate-400">
                    Filled qty
                  </th>
                  <th className="px-3 py-1.5 text-right text-[10px]
                    font-medium uppercase tracking-wide text-slate-400">
                    Avg price
                  </th>
                  <th className="px-3 py-1.5 text-center text-[10px]
                    font-medium uppercase tracking-wide text-slate-400">
                    Raw
                  </th>
                </tr>
              </thead>
              <tbody>
                {postbacks.map((pb, idx) => (
                  <PostbackRow
                    key={`${pb.event_ts}-${pb.raw?.["order_id"] ?? idx}`}
                    postback={pb}
                    expanded={expandedIdx === idx}
                    onToggle={() => handleToggle(idx)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify loading state passes**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx vitest run \
  components/algo-trading/__tests__/KitePostbackPanel.test.tsx \
  --reporter=verbose 2>&1 | head -30
```

Expected: 1 test PASS (`renders text while loading so Lighthouse FCP fires`).

- [ ] **Step 5: Commit**

```bash
git add frontend/components/algo-trading/KitePostbackPanel.tsx \
        frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx
git commit -m "$(cat <<'EOF'
feat(obs-4): KitePostbackPanel skeleton — loading state with FCP text

Adds component structure, StatusBadge, PostbackRow, EmptyState,
LoadingSkeleton with text node per CLAUDE.md §5.3 FCP heuristic.
Only the loading branch is exercised by tests at this point.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Empty state test

> Verifies the amber troubleshooting card renders verbatim per spec §3.6.

**Files:**
- Modify: `frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx`

- [ ] **Step 1: Add empty-state tests to the test file**

Append the following `describe` block inside the test file, after the Task 3
`describe("KitePostbackPanel — loading state", ...)` block:

```tsx
// ── Task 4 ──────────────────────────────────────────────────
describe("KitePostbackPanel — empty state", () => {
  it("renders amber troubleshooting card when postbacks list is empty", () => {
    mockHook.mockReturnValue({
      postbacks: [],
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    const card = screen.getByTestId("kite-postback-empty-state");
    expect(card).toBeTruthy();
    // Verbatim text per spec §3.6.
    expect(card.textContent).toContain("No postbacks received");
    expect(card.textContent).toContain("KITE_POSTBACK_ENABLED");
    expect(card.textContent).toContain("http://localhost:4040");
  });

  it("empty state card has amber border class", () => {
    mockHook.mockReturnValue({
      postbacks: [],
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    const card = screen.getByTestId("kite-postback-empty-state");
    expect(card.className).toContain("border-amber-300");
  });

  it("does NOT render empty state while still loading", () => {
    mockHook.mockReturnValue({
      postbacks: [],
      isLoading: true,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    expect(
      screen.queryByTestId("kite-postback-empty-state"),
    ).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify the new ones pass**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx vitest run \
  components/algo-trading/__tests__/KitePostbackPanel.test.tsx \
  --reporter=verbose
```

Expected: 4 tests PASS (1 from Task 3 + 3 from this task).

- [ ] **Step 3: Commit**

```bash
git add frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx
git commit -m "$(cat <<'EOF'
test(obs-4): empty state — amber card + verbatim troubleshooting text

Covers spec §3.6 requirement: amber border, KITE_POSTBACK_ENABLED ref,
localhost:4040 link, and guard that empty state hides while loading.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: Populated state — rows render correctly

> Verifies each row renders the symbol, status badge, filled qty, and avg price.

**Files:**
- Modify: `frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx`

- [ ] **Step 1: Add populated-state tests**

Append after the Task 4 `describe` block:

```tsx
// ── Task 5 ──────────────────────────────────────────────────
const TWO_POSTBACKS = [
  {
    event_ts: "2026-05-10T09:30:00Z",
    tradingsymbol: "RELIANCE.NS",
    status: "COMPLETE",
    filled_quantity: 5,
    average_price: 2950.75,
    raw: { order_id: "111", guid: "a" },
  },
  {
    event_ts: "2026-05-10T09:25:00Z",
    tradingsymbol: "INFY.NS",
    status: "REJECTED",
    filled_quantity: 0,
    average_price: 0,
    raw: { order_id: "222", guid: "b" },
  },
];

describe("KitePostbackPanel — populated state", () => {
  it("renders one row per postback", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    const rows = screen.getAllByTestId("kite-postback-row");
    expect(rows).toHaveLength(2);
  });

  it("renders the symbol in the first row", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    expect(screen.getByText("RELIANCE.NS")).toBeTruthy();
    expect(screen.getByText("INFY.NS")).toBeTruthy();
  });

  it("renders avg price formatted with ₹ symbol", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    expect(screen.getByText("₹2950.75")).toBeTruthy();
  });

  it("shows — for avg price when average_price is 0", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    expect(screen.getByText("—")).toBeTruthy();
  });

  it("does not render empty state when postbacks present", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    expect(
      screen.queryByTestId("kite-postback-empty-state"),
    ).toBeNull();
  });

  it("shows postback count in the panel header", () => {
    mockHook.mockReturnValue({
      postbacks: TWO_POSTBACKS,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    // Header should contain "(2)" count.
    const panel = screen.getByTestId("kite-postback-panel");
    expect(panel.textContent).toContain("(2)");
  });
});
```

- [ ] **Step 2: Run tests**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx vitest run \
  components/algo-trading/__tests__/KitePostbackPanel.test.tsx \
  --reporter=verbose
```

Expected: 10 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx
git commit -m "$(cat <<'EOF'
test(obs-4): populated state — rows, symbol, price, count rendering

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: Status badge colour tests

> Verifies the four status codes produce the right Tailwind colour classes.

**Files:**
- Modify: `frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx`

- [ ] **Step 1: Add status-badge colour tests**

Append after the Task 5 `describe` block:

```tsx
// ── Task 6 ──────────────────────────────────────────────────
describe("KitePostbackPanel — status badge colours", () => {
  const statusCases: Array<{
    status: string;
    cls: string;
    label: string;
  }> = [
    { status: "COMPLETE", cls: "bg-emerald-100", label: "green for COMPLETE" },
    { status: "REJECTED", cls: "bg-rose-100", label: "red for REJECTED" },
    { status: "CANCELLED", cls: "bg-slate-100", label: "gray for CANCELLED" },
    { status: "UPDATE", cls: "bg-blue-100", label: "blue for UPDATE" },
  ];

  statusCases.forEach(({ status, cls, label }) => {
    it(`applies ${label}`, () => {
      mockHook.mockReturnValue({
        postbacks: [
          {
            event_ts: "2026-05-10T09:30:00Z",
            tradingsymbol: "TEST.NS",
            status,
            filled_quantity: 1,
            average_price: 100,
            raw: { order_id: "x" },
          },
        ],
        isLoading: false,
        error: null,
        mutate: vi.fn(),
      });

      render(<KitePostbackPanel />);

      const badge = screen.getByText(status);
      expect(badge.className).toContain(cls);
    });
  });
});
```

- [ ] **Step 2: Run tests**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx vitest run \
  components/algo-trading/__tests__/KitePostbackPanel.test.tsx \
  --reporter=verbose
```

Expected: 14 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx
git commit -m "$(cat <<'EOF'
test(obs-4): status badge colour classes for all four postback statuses

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Payload toggle — expand / collapse / single-row-at-a-time

> The `▸` button expands inline JSON. Clicking a second row's toggle collapses the
> first (only one row expanded at a time).

**Files:**
- Modify: `frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx`

- [ ] **Step 1: Add payload-toggle tests**

Append after the Task 6 `describe` block:

```tsx
// ── Task 7 ──────────────────────────────────────────────────
describe("KitePostbackPanel — payload toggle", () => {
  const TWO = [
    {
      event_ts: "2026-05-10T09:30:00Z",
      tradingsymbol: "RELIANCE.NS",
      status: "COMPLETE",
      filled_quantity: 5,
      average_price: 2950.75,
      raw: { order_id: "111", guid: "aaa" },
    },
    {
      event_ts: "2026-05-10T09:25:00Z",
      tradingsymbol: "INFY.NS",
      status: "COMPLETE",
      filled_quantity: 3,
      average_price: 1500.5,
      raw: { order_id: "222", guid: "bbb" },
    },
  ];

  it("payload is hidden before any toggle click", () => {
    mockHook.mockReturnValue({
      postbacks: TWO,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    // "111" is only in the raw JSON — should not be visible initially.
    expect(screen.queryByText(/"111"/)).toBeNull();
  });

  it("clicking ▸ expands the raw JSON payload", () => {
    mockHook.mockReturnValue({
      postbacks: TWO,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    const toggles = screen.getAllByTestId(
      "kite-postback-payload-toggle",
    );
    fireEvent.click(toggles[0]);

    // The raw JSON for row 0 should now be visible.
    expect(screen.getByText(/"111"/)).toBeTruthy();
  });

  it("clicking ▸ twice collapses the payload", () => {
    mockHook.mockReturnValue({
      postbacks: TWO,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    const toggles = screen.getAllByTestId(
      "kite-postback-payload-toggle",
    );
    fireEvent.click(toggles[0]);
    expect(screen.getByText(/"111"/)).toBeTruthy();

    fireEvent.click(toggles[0]);
    expect(screen.queryByText(/"111"/)).toBeNull();
  });

  it("expanding row 1 collapses row 0 (single-row-at-a-time)", () => {
    mockHook.mockReturnValue({
      postbacks: TWO,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });

    render(<KitePostbackPanel />);

    const toggles = screen.getAllByTestId(
      "kite-postback-payload-toggle",
    );

    // Expand row 0.
    fireEvent.click(toggles[0]);
    expect(screen.getByText(/"111"/)).toBeTruthy();
    expect(screen.queryByText(/"222"/)).toBeNull();

    // Expand row 1 — row 0 should collapse.
    fireEvent.click(toggles[1]);
    expect(screen.queryByText(/"111"/)).toBeNull();
    expect(screen.getByText(/"222"/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run tests**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx vitest run \
  components/algo-trading/__tests__/KitePostbackPanel.test.tsx \
  --reporter=verbose
```

Expected: 18 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/algo-trading/__tests__/KitePostbackPanel.test.tsx
git commit -m "$(cat <<'EOF'
test(obs-4): payload toggle expand/collapse + single-row-at-a-time

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: Mount `KitePostbackPanel` in `PaperTab` — Live segment only

> Per spec §3.6 and the skeleton plan: mount below `<LiveLandedOrdersList />` inside
> the `liveEnabled` block. The panel is hidden in Paper and Dry-run segments.
>
> Looking at `PaperTab.tsx`: the `<LiveSection>` component renders the in-flight
> orders list inside `{liveEnabled && (...)}`. We add the postback panel BELOW that
> conditional, still inside `<LiveSection>`, always visible in the Live segment
> (regardless of `liveEnabled` — postbacks exist even when live order toggle is off,
> e.g. during the initial Dry-run smoke test). The panel goes directly inside the
> `<div data-testid="live-section">` in `LiveSection`.

**Files:**
- Modify: `frontend/components/algo-trading/PaperTab.tsx`

- [ ] **Step 1: Verify `KitePostbackPanel` is currently absent from `PaperTab`**

```bash
grep -n "KitePostbackPanel" \
  /Users/abhay/Documents/projects/ai-agent-ui/frontend/components/algo-trading/PaperTab.tsx
# Expected: (empty output)
```

- [ ] **Step 2: Add import and mount the panel**

In `PaperTab.tsx`, locate the existing imports block at the top of the file and add:

```tsx
import { KitePostbackPanel } from "./KitePostbackPanel";
```

Place it after the existing `import { ReconciliationDriftPanel } ...` line.

Then, in the `LiveSection` component body, locate the closing `</div>` of the
`space-y-3` wrapper (after the `{liveEnabled && (...)}` in-flight orders block).
Insert the postback panel immediately before that closing tag:

```tsx
      {/* Kite postback events — OBS-4. Always visible in Live
          segment; shows empty-state troubleshooting when nothing
          arrived (KITE_POSTBACK_ENABLED off, ngrok down, etc.). */}
      <div
        className="rounded-md border border-slate-200
          dark:border-slate-700"
        data-testid="live-postback-section"
      >
        <KitePostbackPanel />
      </div>
```

The full updated `LiveSection` function should look like:

```tsx
function LiveSection({ strategyId, strategyName }: {
  strategyId: string;
  strategyName: string;
}) {
  const { caps } = useLiveCaps(strategyId);
  const { gates } = useLiveStatus(strategyId);
  const liveEnabled = caps?.live_orders_enabled ?? false;

  return (
    <div className="space-y-3" data-testid="live-section">
      {/* Dry-run mode amber banner — shown at top of live section */}
      <LiveDryRunBanner gates={gates} />

      {/* Kill-switch banner — only visible when live + kill armed */}
      <LiveCancelInFlightBanner liveEnabled={liveEnabled} />

      {/* 4-gate toggle */}
      <LiveModeToggle
        strategyId={strategyId}
        strategyName={strategyName}
      />

      {/* Safety belts caps form */}
      <div
        className="rounded-md border border-slate-200
          dark:border-slate-700 p-3"
        data-testid="live-safety-belts-panel"
      >
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide
          text-slate-500 dark:text-slate-400">
          Safety belts (caps)
        </h4>
        <LiveSafetyBeltsForm strategyId={strategyId} />
      </div>

      {/* In-flight orders (only shown when live is enabled) */}
      {liveEnabled && (
        <div
          className="rounded-md border border-slate-200
            dark:border-slate-700 p-3"
          data-testid="live-in-flight-panel"
        >
          <h4 className="mb-2 text-xs font-semibold uppercase
            tracking-wide text-slate-500 dark:text-slate-400">
            In-flight orders
          </h4>
          <LiveLandedOrdersList strategyId={strategyId} />
        </div>
      )}

      {/* Kite postback events — OBS-4. Always visible in Live
          segment; shows empty-state troubleshooting when nothing
          arrived (KITE_POSTBACK_ENABLED off, ngrok down, etc.). */}
      <div
        className="rounded-md border border-slate-200
          dark:border-slate-700"
        data-testid="live-postback-section"
      >
        <KitePostbackPanel />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify the panel is mounted only in the Live segment**

The `<LiveSection>` component is only rendered inside `(viewMode === "live" || viewMode === "dryrun")` in PaperTab's return. But `KitePostbackPanel` should appear only for `viewMode === "live"`, NOT for `viewMode === "dryrun"`. Inspect the current condition in `PaperTab`:

```tsx
{(viewMode === "live" || viewMode === "dryrun") && (
  <div className="space-y-4" data-testid="trading-live-view">
    ...
    {liveStrategyId && selectedStrategy && (
      <div className="mt-3">
        <LiveSection ... />
      </div>
    )}
```

`LiveSection` (and therefore `KitePostbackPanel`) renders in both `live` AND `dryrun` modes when a strategy is selected. Per the skeleton: "Hide entirely outside Live mode (Paper / Dry-run segments don't need this panel)." To honour this, wrap the `<div data-testid="live-postback-section">` mount with a `viewMode` check passed as prop:

Change `LiveSection` to accept a `showPostbacks` prop:

```tsx
function LiveSection({ strategyId, strategyName, showPostbacks }: {
  strategyId: string;
  strategyName: string;
  showPostbacks: boolean;
}) {
```

And gate the panel:

```tsx
      {showPostbacks && (
        <div
          className="rounded-md border border-slate-200
            dark:border-slate-700"
          data-testid="live-postback-section"
        >
          <KitePostbackPanel />
        </div>
      )}
```

Then at the call site in `PaperTab` pass the prop:

```tsx
<LiveSection
  strategyId={liveStrategyId}
  strategyName={selectedStrategy.name}
  showPostbacks={viewMode === "live"}
/>
```

- [ ] **Step 4: TypeScript compile check**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 5: Visual smoke check** (manual — no vitest for this)

```bash
# With Docker services up:
./run.sh logs frontend -f &
# Navigate to /algo-trading?tab=paper
# Toggle to Live mode → pick any strategy
# Verify: KitePostbackPanel renders below the in-flight orders section
# Toggle to Paper mode → panel should be absent
# Toggle to Dry run mode → panel should be absent
```

- [ ] **Step 6: Commit**

```bash
git add frontend/components/algo-trading/PaperTab.tsx
git commit -m "$(cat <<'EOF'
feat(obs-4): mount KitePostbackPanel in PaperTab Live segment only

Adds showPostbacks prop to LiveSection; panel renders only when
viewMode === "live", hidden in Paper and Dry-run per spec §3.6.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 9: Run the full vitest suite — no regressions

> Confirm nothing in the existing 18 frontend tests regressed.

**Files:** no changes.

- [ ] **Step 1: Run all vitest tests**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx vitest run --reporter=verbose 2>&1 | tail -20
```

Expected: all tests PASS. The new `KitePostbackPanel` suite should show 18 tests; the
existing `ReconciliationDriftPanel` suite should still show 7 tests (unchanged).

- [ ] **Step 2: Run ESLint**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend
npx eslint ./components/algo-trading/KitePostbackPanel.tsx \
           ./hooks/useKitePostbacks.ts \
           --fix
```

Expected: 0 errors.

- [ ] **Step 3: Commit** (lint-clean confirmation commit — only if ESLint auto-fixed anything)

```bash
git add -p  # stage only ESLint autofixes if any
git commit -m "$(cat <<'EOF'
style(obs-4): ESLint autofix on KitePostbackPanel + useKitePostbacks

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)" || echo "Nothing to fix — clean"
```

---

## Task 10: Playwright E2E spec

> One spec file with three scenarios:
> 1. Panel is visible in Live mode.
> 2. Empty state shows troubleshooting text when the API returns an empty array.
> 3. Payload toggle expands JSON (using mocked API response via `page.route`).
>
> Note: we do NOT seed a real Iceberg postback row for E2E — that would require a
> running backend with OBS-2 shipped. Instead we intercept the API call with
> `page.route` and serve a fixture. This is consistent with how other algo E2E tests
> work (they also mock slow or infra-dependent endpoints).

**Files:**
- Create: `e2e/tests/frontend/algo-trading-postback-panel.spec.ts`

- [ ] **Step 1: Write the spec**

Create `e2e/tests/frontend/algo-trading-postback-panel.spec.ts`:

```typescript
/**
 * E2E — Kite Postback Panel (OBS-4).
 *
 * Three scenarios tested against the Live segment of the Trading tab.
 * The backend GET /algo/live/postbacks endpoint is mocked via
 * page.route so these tests don't require OBS-2 to be deployed.
 *
 * Auth: uses the superuser storageState (.auth/superuser.json) because
 * the endpoint requires pro_or_superuser and the Playwright auth setup
 * project already populates this file.
 */
import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

const POSTBACKS_URL = "**/v1/algo/live/postbacks*";

const FIXTURE_POSTBACK = {
  event_ts: "2026-05-10T09:30:00.000Z",
  tradingsymbol: "RELIANCE.NS",
  status: "COMPLETE",
  filled_quantity: 5,
  average_price: 2950.75,
  raw: {
    order_id: "240510000111111",
    guid: "e2e-test-guid",
    status: "COMPLETE",
    tradingsymbol: "RELIANCE.NS",
    filled_quantity: 5,
    average_price: 2950.75,
    checksum: "00deadbeef",
  },
};

test.describe("Algo Trading — Kite Postback Panel", () => {
  test.use({ storageState: ".auth/superuser.json" });

  test("panel is present in Live mode", async ({ page }) => {
    // Intercept the API so the test doesn't need OBS-2 deployed.
    await page.route(POSTBACKS_URL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
    );

    await page.goto("/algo-trading?tab=paper");

    // Switch to Live mode using the trading-mode toggle.
    await page.getByTestId("trading-mode-live").click();

    // The panel should be mounted (even with empty postbacks it renders
    // the panel container).
    await expect(
      page.getByTestId(FE.kitePostbackPanel),
    ).toBeVisible();
  });

  test("empty state shows troubleshooting card when postbacks is []", async ({
    page,
  }) => {
    await page.route(POSTBACKS_URL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
    );

    await page.goto("/algo-trading?tab=paper");
    await page.getByTestId("trading-mode-live").click();

    const emptyCard = page.getByTestId(FE.kitePostbackEmptyState);
    await expect(emptyCard).toBeVisible();
    await expect(emptyCard).toContainText("No postbacks received");
    await expect(emptyCard).toContainText("KITE_POSTBACK_ENABLED");
    await expect(emptyCard).toContainText("http://localhost:4040");
  });

  test("payload toggle expands raw JSON for a seeded postback row", async ({
    page,
  }) => {
    await page.route(POSTBACKS_URL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([FIXTURE_POSTBACK]),
      }),
    );

    await page.goto("/algo-trading?tab=paper");
    await page.getByTestId("trading-mode-live").click();

    // Wait for the postback row to appear.
    const row = page.getByTestId(FE.kitePostbackRow).first();
    await expect(row).toBeVisible();

    // Raw JSON is hidden before toggle.
    await expect(page.getByText("e2e-test-guid")).not.toBeVisible();

    // Click the ▸ toggle.
    await page.getByTestId(FE.kitePostbackPayloadToggle).first().click();

    // Raw JSON should now be visible.
    await expect(page.getByText(/e2e-test-guid/)).toBeVisible();
  });

  test("panel is absent in Paper mode", async ({ page }) => {
    // No route intercept needed — panel shouldn't mount at all.
    await page.goto("/algo-trading?tab=paper");

    // Default mode is Live (per PaperTab), so first switch to Paper.
    await page.getByTestId("trading-mode-paper").click();

    await expect(
      page.getByTestId(FE.kitePostbackPanel),
    ).not.toBeVisible();
  });

  test("panel is absent in Dry-run mode", async ({ page }) => {
    await page.route(POSTBACKS_URL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
    );

    await page.goto("/algo-trading?tab=paper");
    await page.getByTestId("trading-mode-dryrun").click();

    await expect(
      page.getByTestId(FE.kitePostbackPanel),
    ).not.toBeVisible();
  });
});
```

- [ ] **Step 2: Run the spec (services must be up)**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/e2e
npx playwright test \
  tests/frontend/algo-trading-postback-panel.spec.ts \
  --project=frontend-chromium \
  --reporter=list
```

Expected: 5 tests PASS. If `KitePostbackPanel` is mounted before a strategy is
selected (it's inside `LiveSection` which only renders when `liveStrategyId` is set),
some tests may need a strategy selection step — if so, add:

```typescript
// After clicking trading-mode-live and before asserting the panel:
const stratSelect = page.getByTestId("live-strategy-select");
if (await stratSelect.isVisible()) {
  await stratSelect.selectOption({ index: 1 }); // pick first strategy
}
```

Insert that pattern in any test that times out waiting for `kite-postback-panel`.

- [ ] **Step 3: Commit**

```bash
git add e2e/tests/frontend/algo-trading-postback-panel.spec.ts
git commit -m "$(cat <<'EOF'
test(obs-4): Playwright E2E for KitePostbackPanel — 5 scenarios

Covers: panel present in Live mode, empty state text, payload toggle,
and panel absent in Paper + Dry-run modes. API mocked via page.route.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 11: Documentation — add "Verifying postbacks in the UI" section

> `docs/algo-trading/postbacks.md` is created in OBS-3. This task adds the UI
> verification section to it. If OBS-3 has not yet run, create the file with only
> this section and note that the operator runbook section is added by OBS-3.

**Files:**
- Modify (or Create): `docs/algo-trading/postbacks.md`

- [ ] **Step 1: Check if the file exists (OBS-3 prerequisite)**

```bash
ls /Users/abhay/Documents/projects/ai-agent-ui/docs/algo-trading/postbacks.md 2>/dev/null \
  && echo "EXISTS" || echo "MISSING"
```

If `MISSING`: create a stub:

```bash
mkdir -p /Users/abhay/Documents/projects/ai-agent-ui/docs/algo-trading
cat > /Users/abhay/Documents/projects/ai-agent-ui/docs/algo-trading/postbacks.md <<'STUB'
# Kite Postbacks — Operator Runbook

> ⚠️ The operator setup section (ngrok + Kite Developer Console) is
> documented in OBS-3. This file covers the UI verification section (OBS-4).

STUB
```

- [ ] **Step 2: Append the "Verifying postbacks in the UI" section**

Append to `docs/algo-trading/postbacks.md`:

```markdown

---

## Verifying postbacks in the UI

Once the ngrok tunnel is up and the Kite Developer Console postback URL is
configured (see OBS-3 setup above), place a small test order in Dry-run mode
and watch the **Kite postbacks** panel in the Trading tab:

### Panel location

1. Open **Algo Trading → Trading tab**.
2. Ensure the **Live** mode button is selected (top-right toggle).
3. Select any strategy from the "Strategy" dropdown.
4. The **Kite postbacks** panel appears below the "In-flight orders" panel.

### What you should see

| State | Panel appearance |
|---|---|
| No postbacks received today | Amber card: "No postbacks received…" with `KITE_POSTBACK_ENABLED` and `http://localhost:4040` references |
| Postbacks flowing | Table rows: `Time (IST) · Symbol · Status · Filled qty · Avg price · ▸` |
| Status COMPLETE | Green badge |
| Status REJECTED | Red badge |
| Status CANCELLED | Grey badge |
| Status UPDATE | Blue badge |

### Expanding the raw payload

Click the **▸** arrow at the end of any row to expand the raw Kite postback
JSON. Only one row is expanded at a time — clicking another row collapses the
previous one. This is useful for diagnosing checksum failures or unexpected
field values.

### Troubleshooting the empty state

If the amber "No postbacks received" card appears after placing a live or
dry-run order:

1. **`KITE_POSTBACK_ENABLED` is `false`** — set it to `true` in `.env` and
   restart the backend: `./run.sh restart backend`.
2. **ngrok tunnel is down** — check `http://localhost:4040`. The ngrok
   service should show your live tunnel. If not:
   ```bash
   docker compose --profile live up -d ngrok
   ```
3. **Postback URL not set in Kite Developer Console** — navigate to
   `https://kite.trade/` → Developer Console → your app → Postback URL.
   It should be `https://<NGROK_DOMAIN>/v1/webhooks/kite/postback`.
4. **Checksum mismatch** — tail backend logs for
   `"kite postback checksum failed"`. Usually caused by a stale
   `KITE_API_SECRET` in the macOS Keychain; update with:
   ```bash
   security add-generic-password -a kite_api_secret \
     -s ai-agent-ui -w "<new-secret>" -U
   ```

### SWR refresh cadence

The panel refreshes every **30 seconds**. To force an immediate refresh,
reload the page (`Cmd+R`). A postback typically appears in the panel within
≤ 30 s of being received (network round-trip + SWR next tick).
```

- [ ] **Step 3: Verify the markdown renders (optional — requires mkdocs up)**

```bash
# Only run if Docker docs service is running:
curl -s http://localhost:8000/algo-trading/postbacks/ | grep -i "Verifying postbacks"
# Expected: match found
```

- [ ] **Step 4: Commit**

```bash
git add docs/algo-trading/postbacks.md
git commit -m "$(cat <<'EOF'
docs(obs-4): add "Verifying postbacks in the UI" section to runbook

Covers panel location, status badge colours, payload expand UX,
and troubleshooting steps for the amber empty-state card.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Acceptance Checklist

Run through these manually after all tasks complete:

- [ ] Panel mounted in Live segment only (switch to Paper → gone; Dry-run → gone).
- [ ] Table renders up to 50 rows, newest first (order comes from the backend
      `ORDER BY event_ts DESC LIMIT 50` — the frontend does not re-sort).
- [ ] Payload toggle expands/collapses correctly; clicking another row collapses
      the previous.
- [ ] Empty state shows amber troubleshooting card verbatim per spec §3.6.
- [ ] Loading state has "Loading postbacks…" text (Lighthouse FCP heuristic).
- [ ] No regression on `LiveLandedOrdersList` (sibling component — existing 18
      vitest tests still pass).
- [ ] All 4 new `data-testid` attributes registered in `e2e/utils/selectors.ts`.
- [ ] Playwright spec: 5 tests pass including empty-state, payload toggle, and
      "panel absent in Paper/Dry-run" assertions.
- [ ] `npx tsc --noEmit` clean in both `frontend/` and `e2e/`.
- [ ] ESLint clean on all new/modified files.

---

## Out of scope for OBS-4

- WS health dot (OBS-1).
- Postback backend handler (OBS-2).
- ngrok service (OBS-3).
- Postback-driven UI auto-refresh on push (polled @ 30s in this slice).
- Postback retry / replay UI (postbacks aren't retryable from our side).
- Column selector (only 5 columns — `§5.4`: column selector NOT needed below 8
  columns).
- `cache:algo:postbacks:{user_id}` TTL_VOLATILE=60 invalidation is handled
  backend-side in OBS-2; the frontend hook just polls.
