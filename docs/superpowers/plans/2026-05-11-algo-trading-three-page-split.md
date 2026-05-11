# Algo Trading — Three-Page Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `/algo-trading` into three sidebar-grouped pages — `Zerodha Connect`, `Strategies` (Instruments / Strategies / Backtest / Paper / Dry run / Performance / Replay / Settings), and `Live Trading` (Live dashboard / Positions / Holdings / Settings) — replacing the single eight-tab page where Live, Paper, and Dry-run share a risky in-page toggle.

**Architecture:** Frontend route + sidebar restructure on top of v3. Three new Next.js route folders under `(authenticated)/algo-trading/`, three thin `*Client.tsx` shells, a new dense Live dashboard (sticky KPI strip + 4-zone grid), and three new backend endpoints under the existing `create_live_router()` (dashboard-summary, positions, holdings) that join Kite REST truth to `paper_events_v3` strategy attribution. Each of the three modes (Paper / Dry-run / Live) lives at a distinct URL with a distinct accent color and runtime; switching mode means switching tab (and therefore URL), eliminating the in-page toggle and the per-user Redis dry-run flag bleed.

**Tech Stack:** Next.js 16 App Router, React 19, SWR, Tailwind. Python 3.12, FastAPI, Pydantic v2, kiteconnect (existing). Tests: pytest, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-05-11-algo-trading-three-page-split-design.md` — §3 (IA), §4 (Live dashboard), §5 (backend), §6 (frontend file map), §7 (slicing).

**Branch:** `feature/algo-trading-three-page-split` off `dev` (already created at commit `da9b213`).

**Estimated effort:** ~5 days across 6 slices.

---

## Pre-flight (MUST DO before writing any code)

Per the `feedback_subagent_grep_preflight` memory: every imported symbol, called method, and constant referenced in this plan MUST be grep-verified in the current branch before the corresponding task is written. The v2 epic accumulated 8 wrong-name bugs (`_iceberg_table_path`, `kite_api_secret` slug, `await cache.get`, etc.) all from skipping this step.

- [ ] **PF-1:** Verify `create_live_router` factory exists and where new endpoints will live:

  ```bash
  grep -n "^def create_live_router\|^    @router\." \
    backend/algo/routes/live.py | head -30
  ```
  Expected: function at module level + multiple `@router.*` decorators inside; new endpoints append before the final `return router`.

- [ ] **PF-2:** Verify the Kite client method names for positions, holdings, margins:

  ```bash
  grep -n "kite._kc\.\(positions\|holdings\|margins\)" \
    backend/algo/routes/kill_switch.py
  ```
  Expected: at least one match for each of `positions`, `holdings`. `kill_switch.py:225-236` shows the call signatures. `kite._kc.margins()` is referenced in spec § 4.2 — grep `_kc.margins` across `backend/algo/` to confirm it exists; if not, use `kite._kc.margins("equity")` per kiteconnect API and adjust.

- [ ] **PF-3:** Verify Redis cache helper signature is `cache.set(key, value, ttl=...)` and **not** `ex=...`:

  ```bash
  grep -n "def set\|def get\|def invalidate" backend/db/cache.py
  ```
  Expected: synchronous `def set(self, key, value, ttl=...)`. CLAUDE.md § 5.13 calls this out as a frequent footgun.

- [ ] **PF-4:** Verify `ActiveRunsPanel` accepts the three trading-mode literals and the routes that the Dry-run tab depends on:

  ```bash
  grep -n 'tradingMode\?:' \
    frontend/components/algo-trading/ActiveRunsPanel.tsx
  ```
  Expected: `tradingMode?: "paper" | "dryrun" | "live"`. Plan assumes the existing prop works for both Paper-only and Dry-run-only callers.

- [ ] **PF-5:** Confirm sidebar `NavItem` type supports nested children (the Dashboard group pattern):

  ```bash
  grep -n "children\?\:\|interface NavItem\|type NavItem" \
    frontend/lib/constants.tsx frontend/components/Sidebar.tsx
  ```
  Expected: `children?: NavItem[]` on `NavItem`. Sidebar.tsx:370+ already handles `renderNavGroup`.

- [ ] **PF-6:** Verify `paper_events_v3` Iceberg table schema (for the positions/holdings join):

  ```bash
  grep -n "paper_events_v3\|tradingsymbol\|strategy_id" \
    stocks/create_tables.py | head -20
  ```
  Expected: `tradingsymbol` and `strategy_id` columns exist. If column names differ, adjust § 5.2 of the spec and the JOIN logic in Task 4.3 accordingly.

- [ ] **PF-7:** Confirm `/algo/kill-switch/panic-close` POST exists (used by `PanicCloseButton` in Task 4.4):

  ```bash
  grep -n "panic-close\|panic_close\|@router.post" \
    backend/algo/routes/kill_switch.py | head -20
  ```
  Expected: at least one POST route handling the panic-close
  command. If it lives at a different path (e.g.
  `/algo/kill-switch/panic`), update the `panicClose()` URL in
  `LiveDashboard.tsx` Task 4.6 accordingly.

- [ ] **PF-8:** Confirm we are on `feature/algo-trading-three-page-split` branch with the spec already committed:

  ```bash
  git branch --show-current && \
    git log --oneline -1 -- docs/superpowers/specs/2026-05-11-algo-trading-three-page-split-design.md
  ```
  Expected: branch name matches, log shows `da9b213 docs(algo): spec three-page split ...`.

---

## File Structure

### New files

```
backend/algo/routes/live.py
  + GET /algo/live/dashboard-summary
  + GET /algo/live/positions
  + GET /algo/live/holdings
backend/algo/tests/test_live_dashboard_summary.py
backend/algo/tests/test_live_positions.py
backend/algo/tests/test_live_holdings.py

frontend/app/(authenticated)/algo-trading/broker/page.tsx
frontend/app/(authenticated)/algo-trading/broker/BrokerClient.tsx
frontend/app/(authenticated)/algo-trading/strategies/page.tsx
frontend/app/(authenticated)/algo-trading/strategies/StrategiesClient.tsx
frontend/app/(authenticated)/algo-trading/live/page.tsx
frontend/app/(authenticated)/algo-trading/live/LiveClient.tsx

frontend/components/algo-trading/live/LiveHeaderStrip.tsx
frontend/components/algo-trading/live/LiveDashboard.tsx
frontend/components/algo-trading/live/OpenPositionsWidget.tsx
frontend/components/algo-trading/live/RecentFillsTape.tsx
frontend/components/algo-trading/live/PositionsTab.tsx
frontend/components/algo-trading/live/HoldingsTab.tsx
frontend/components/algo-trading/live/LiveSettingsTab.tsx
frontend/components/algo-trading/live/PanicCloseButton.tsx
frontend/components/algo-trading/live/LiveModeChip.tsx
frontend/components/algo-trading/dryrun/DryRunTab.tsx
frontend/components/algo-trading/dryrun/DryRunArmBanner.tsx
frontend/components/algo-trading/StrategiesSettingsTab.tsx   (Fee Preview only)

frontend/hooks/useLiveDashboardSummary.ts
frontend/hooks/useLivePositions.ts
frontend/hooks/useLiveHoldings.ts

frontend/components/algo-trading/__tests__/LiveHeaderStrip.test.tsx
frontend/components/algo-trading/__tests__/LiveModeChip.test.tsx
frontend/components/algo-trading/__tests__/PanicCloseButton.test.tsx
frontend/components/algo-trading/__tests__/PositionsTab.test.tsx
frontend/components/algo-trading/__tests__/DryRunArmBanner.test.tsx
frontend/components/algo-trading/__tests__/AlgoTradingRedirect.test.tsx

e2e/specs/algo-sidebar-group.spec.ts
e2e/specs/algo-broker-page.spec.ts
e2e/specs/algo-strategies-tabs.spec.ts
e2e/specs/algo-live-dashboard.spec.ts
e2e/specs/algo-live-positions.spec.ts

docs/algo-trading/page-structure.md
```

### Modified files

```
frontend/app/(authenticated)/algo-trading/page.tsx
  — now server-redirects to /algo-trading/strategies, mapping
    legacy ?tab=* to the new homes (table in § 6.2 of spec).
frontend/components/algo-trading/PaperTab.tsx
  — strip Live + Dry-run branches; Paper-only; ~150 LOC after split
frontend/components/algo-trading/SettingsTab.tsx
  — DELETED (replaced by StrategiesSettingsTab + LiveSettingsTab)
frontend/lib/constants.tsx
  — flat algo-trading entry replaced with collapsible group of 3
frontend/lib/types/algoTrading.ts
  — AlgoTabId union split into StrategiesTabId + LiveTabId

e2e/utils/selectors.ts
  — testid constants for the new components

PROGRESS.md
```

### Deleted files (in Slice 5, after Strategies + Live clients work)

```
frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx
```

---

## Slice 1 — Sidebar group + routes scaffold (½ day, 1 PR)

Adds the three new sidebar entries and bare page shells. No business
logic moves yet — clicking each entry lands on a placeholder. This
is the lowest-risk slice and ships first so the navigation is in
place when later slices fill in content.

### Task 1.1 — Sidebar nav constants: add Algo Trading group

**Files:**
- Modify: `frontend/lib/constants.tsx:131-140`

- [ ] **Step 1: Locate the existing flat entry**

  ```bash
  grep -n '"algo-trading"\|view: "algo-trading"' \
    frontend/lib/constants.tsx
  ```
  Expected: line 131 region — current shape:

  ```tsx
  {
    view: "algo-trading",
    href: "/algo-trading",
    label: "Algo Trading",
    requiresAlgoTrading: true,
    icon: (...),
  },
  ```

- [ ] **Step 2: Replace with collapsible group**

  Replace the block above with:

  ```tsx
  {
    view: "algo-trading",
    href: "/algo-trading",
    label: "Algo Trading",
    requiresAlgoTrading: true,
    icon: (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        className="w-4 h-4 shrink-0"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M2 12h2l3-9 4 18 3-9 2 5h6" />
      </svg>
    ),
    children: [
      {
        view: "algo-broker",
        href: "/algo-trading/broker",
        label: "Zerodha Connect",
        requiresAlgoTrading: true,
        icon: (
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="w-4 h-4 shrink-0"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          </svg>
        ),
      },
      {
        view: "algo-strategies",
        href: "/algo-trading/strategies",
        label: "Strategies",
        requiresAlgoTrading: true,
        icon: (
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="w-4 h-4 shrink-0"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" />
          </svg>
        ),
      },
      {
        view: "algo-live",
        href: "/algo-trading/live",
        label: "Live Trading",
        requiresAlgoTrading: true,
        icon: (
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="w-4 h-4 shrink-0"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="10" />
            <circle cx="12" cy="12" r="4" />
          </svg>
        ),
      },
    ],
  },
  ```

- [ ] **Step 3: Update view-id union if it exists**

  ```bash
  grep -n '"algo-trading"' frontend/lib/constants.tsx | head -5
  ```
  If the `view` field has a string-literal union type (look for `type View = "..." | "algo-trading" | ...`), add `"algo-broker" | "algo-strategies" | "algo-live"` to it. If `view` is `string`, skip.

- [ ] **Step 4: Verify the sidebar renders the group**

  ```bash
  cd frontend && npx vitest run --reporter=basic
  ```
  Expected: existing tests pass; no test references the deleted flat entry (none should — the entry is data, not type).

- [ ] **Step 5: Commit**

  ```bash
  git add frontend/lib/constants.tsx
  git commit -m "feat(algo-fe): sidebar Algo Trading group with 3 children"
  ```

### Task 1.2 — Broker page scaffold

**Files:**
- Create: `frontend/app/(authenticated)/algo-trading/broker/page.tsx`
- Create: `frontend/app/(authenticated)/algo-trading/broker/BrokerClient.tsx`

- [ ] **Step 1: Server page**

  Write `frontend/app/(authenticated)/algo-trading/broker/page.tsx`:

  ```tsx
  import BrokerClient from "./BrokerClient";

  export const metadata = { title: "Zerodha Connect — Algo Trading" };

  export default function BrokerPage() {
    return <BrokerClient />;
  }
  ```

- [ ] **Step 2: Client shell**

  Write `frontend/app/(authenticated)/algo-trading/broker/BrokerClient.tsx`:

  ```tsx
  "use client";

  import { ConnectBrokerTab } from "@/components/algo-trading/ConnectBrokerTab";

  export default function BrokerClient() {
    return (
      <div className="space-y-4 p-6" data-testid="algo-broker-page">
        <h1 className="text-xl font-semibold">Zerodha Connect</h1>
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
          <ConnectBrokerTab />
        </div>
      </div>
    );
  }
  ```

- [ ] **Step 3: Smoke test**

  ```bash
  cd frontend && npx tsc --noEmit
  ```
  Expected: no type errors.

- [ ] **Step 4: Commit**

  ```bash
  git add frontend/app/\(authenticated\)/algo-trading/broker/
  git commit -m "feat(algo-fe): scaffold /algo-trading/broker page"
  ```

### Task 1.3 — Strategies + Live page scaffolds (placeholders)

**Files:**
- Create: `frontend/app/(authenticated)/algo-trading/strategies/page.tsx`
- Create: `frontend/app/(authenticated)/algo-trading/strategies/StrategiesClient.tsx`
- Create: `frontend/app/(authenticated)/algo-trading/live/page.tsx`
- Create: `frontend/app/(authenticated)/algo-trading/live/LiveClient.tsx`

- [ ] **Step 1: Strategies server page**

  ```tsx
  // strategies/page.tsx
  import StrategiesClient from "./StrategiesClient";
  export const metadata = { title: "Strategies — Algo Trading" };
  export default function StrategiesPage() {
    return <StrategiesClient />;
  }
  ```

- [ ] **Step 2: Strategies client placeholder**

  ```tsx
  // strategies/StrategiesClient.tsx
  "use client";

  export default function StrategiesClient() {
    return (
      <div className="space-y-4 p-6" data-testid="algo-strategies-page">
        <h1 className="text-xl font-semibold">Strategies</h1>
        <p className="text-sm text-gray-500">
          Tabs will be wired up in Slice 2.
        </p>
      </div>
    );
  }
  ```

- [ ] **Step 3: Live server page + client placeholder (same pattern)**

  ```tsx
  // live/page.tsx
  import LiveClient from "./LiveClient";
  export const metadata = { title: "Live Trading — Algo Trading" };
  export default function LivePage() {
    return <LiveClient />;
  }
  ```

  ```tsx
  // live/LiveClient.tsx
  "use client";

  export default function LiveClient() {
    return (
      <div className="space-y-4 p-6" data-testid="algo-live-page">
        <h1 className="text-xl font-semibold">Live Trading</h1>
        <p className="text-sm text-gray-500">
          Dashboard will be wired up in Slice 4.
        </p>
      </div>
    );
  }
  ```

- [ ] **Step 4: Type-check + smoke**

  ```bash
  cd frontend && npx tsc --noEmit && npx eslint app/\(authenticated\)/algo-trading/
  ```
  Expected: no errors.

- [ ] **Step 5: Commit**

  ```bash
  git add frontend/app/\(authenticated\)/algo-trading/strategies/ \
          frontend/app/\(authenticated\)/algo-trading/live/
  git commit -m "feat(algo-fe): scaffold /algo-trading/strategies and /live pages"
  ```

### Task 1.4 — Server-side redirect on /algo-trading

**Files:**
- Modify: `frontend/app/(authenticated)/algo-trading/page.tsx`

The current `page.tsx` mounts `AlgoTradingClient`. It now becomes a
server-side redirect that maps legacy `?tab=*` to the new homes per
spec § 6.2.

- [ ] **Step 1: Write the failing redirect test (vitest)**

  Create `frontend/components/algo-trading/__tests__/AlgoTradingRedirect.test.tsx`:

  ```tsx
  /**
   * NOTE: this is an integration-level smoke. Next 16 server
   * redirects are tested via Playwright in Slice 6. The vitest
   * test below exercises the redirect-map pure function so logic
   * stays unit-testable.
   */
  import { describe, expect, it } from "vitest";
  import { mapLegacyTab } from "@/app/(authenticated)/algo-trading/redirectMap";

  describe("mapLegacyTab", () => {
    it("connect -> /broker", () => {
      expect(mapLegacyTab("connect")).toBe("/algo-trading/broker");
    });
    it("instruments -> /strategies?tab=instruments", () => {
      expect(mapLegacyTab("instruments")).toBe(
        "/algo-trading/strategies?tab=instruments",
      );
    });
    it.each(["strategies", "backtest", "performance", "replay"])(
      "%s -> /strategies?tab=%s",
      (id) => {
        expect(mapLegacyTab(id)).toBe(
          `/algo-trading/strategies?tab=${id}`,
        );
      },
    );
    it("paper -> /strategies?tab=paper", () => {
      expect(mapLegacyTab("paper")).toBe(
        "/algo-trading/strategies?tab=paper",
      );
    });
    it("settings -> /strategies?tab=settings", () => {
      expect(mapLegacyTab("settings")).toBe(
        "/algo-trading/strategies?tab=settings",
      );
    });
    it("null -> /strategies (default)", () => {
      expect(mapLegacyTab(null)).toBe("/algo-trading/strategies");
    });
    it("unknown -> /strategies (safe default)", () => {
      expect(mapLegacyTab("ghost")).toBe("/algo-trading/strategies");
    });
  });
  ```

- [ ] **Step 2: Run the test, confirm it fails**

  ```bash
  cd frontend && npx vitest run AlgoTradingRedirect
  ```
  Expected: FAIL with module-not-found for `redirectMap`.

- [ ] **Step 3: Implement the pure mapper**

  Create `frontend/app/(authenticated)/algo-trading/redirectMap.ts`:

  ```ts
  /** Map legacy ?tab= IDs (pre-2026-05-11) to their new homes. */
  export function mapLegacyTab(tab: string | null): string {
    if (tab === "connect") return "/algo-trading/broker";
    const strategiesTabs = [
      "instruments",
      "strategies",
      "backtest",
      "paper",
      "performance",
      "replay",
      "settings",
    ];
    if (tab && strategiesTabs.includes(tab)) {
      return `/algo-trading/strategies?tab=${tab}`;
    }
    return "/algo-trading/strategies";
  }
  ```

- [ ] **Step 4: Run the test, confirm it passes**

  ```bash
  cd frontend && npx vitest run AlgoTradingRedirect
  ```
  Expected: PASS, 9 assertions.

- [ ] **Step 5: Replace page.tsx with the redirect**

  Overwrite `frontend/app/(authenticated)/algo-trading/page.tsx`:

  ```tsx
  import { redirect } from "next/navigation";
  import { mapLegacyTab } from "./redirectMap";

  export default async function AlgoTradingIndex({
    searchParams,
  }: {
    searchParams: Promise<{ tab?: string }>;
  }) {
    const params = await searchParams;
    redirect(mapLegacyTab(params.tab ?? null));
  }
  ```

- [ ] **Step 6: Verify nothing imports the now-orphaned AlgoTradingClient**

  ```bash
  grep -rn "AlgoTradingClient" frontend/app/ frontend/components/ \
    --include="*.tsx" --include="*.ts" | grep -v __tests__
  ```
  Expected: empty (the only consumer was `page.tsx`, which we just rewrote). Keep the file on disk for now; Task 5.3 deletes it.

- [ ] **Step 7: Commit**

  ```bash
  git add frontend/app/\(authenticated\)/algo-trading/page.tsx \
          frontend/app/\(authenticated\)/algo-trading/redirectMap.ts \
          frontend/components/algo-trading/__tests__/AlgoTradingRedirect.test.tsx
  git commit -m "feat(algo-fe): legacy ?tab= redirects to new page homes"
  ```

### Task 1.5 — Manual smoke

- [ ] **Step 1: Start the stack**

  ```bash
  ./run.sh start
  ```

- [ ] **Step 2: Sidebar smoke**

  Browse to `http://localhost:3000`, log in, click sidebar → confirm
  "Algo Trading" expands to show "Zerodha Connect", "Strategies",
  "Live Trading" entries. Click each → land on the placeholder page.

- [ ] **Step 3: Redirect smoke**

  Browse to `http://localhost:3000/algo-trading?tab=settings`,
  confirm URL becomes `/algo-trading/strategies?tab=settings`.
  Repeat for `?tab=connect` → `/algo-trading/broker`.

- [ ] **Step 4: PR**

  Create the slice-1 PR titled `feat(algo-fe): sidebar group + page
  scaffolds (slice 1/6)`.

---

## Slice 2 — Move Strategies tabs; split Paper / Dry-run (1 day, 1 PR)

Migrates the existing six tabs (Instruments, Strategies, Backtest,
Performance, Replay, Settings) from `AlgoTradingClient.tsx` into the
new `StrategiesClient.tsx`. Adds the new `Paper` (replay-only) and
`Dry run` (live-runtime synthetic) tabs as siblings. The Live branch
of today's `PaperTab.tsx` is deleted here — its destination is Slice
4. This is the slice where the user starts seeing "complete
separation."

### Task 2.1 — Type union split

**Files:**
- Modify: `frontend/lib/types/algoTrading.ts`

- [ ] **Step 1: Replace the file**

  ```ts
  /** Strategies-page tab IDs. URL-synced via ?tab=. */
  export type StrategiesTabId =
    | "instruments"
    | "strategies"
    | "backtest"
    | "paper"
    | "dryrun"
    | "performance"
    | "replay"
    | "settings";

  export const STRATEGIES_TAB_LABELS: Record<StrategiesTabId, string> = {
    instruments: "Instruments",
    strategies: "Strategies",
    backtest: "Backtest",
    paper: "Paper",
    dryrun: "Dry run",
    performance: "Performance",
    replay: "Replay",
    settings: "Settings",
  };

  export const STRATEGIES_TAB_ORDER: StrategiesTabId[] = [
    "instruments",
    "strategies",
    "backtest",
    "paper",
    "dryrun",
    "performance",
    "replay",
    "settings",
  ];

  /** Live-page tab IDs. URL-synced via ?tab=. */
  export type LiveTabId = "live" | "positions" | "holdings" | "settings";

  export const LIVE_TAB_LABELS: Record<LiveTabId, string> = {
    live: "Live",
    positions: "Positions",
    holdings: "Holdings",
    settings: "Settings",
  };

  export const LIVE_TAB_ORDER: LiveTabId[] = [
    "live",
    "positions",
    "holdings",
    "settings",
  ];
  ```

- [ ] **Step 2: Find every consumer of the old `AlgoTabId`**

  ```bash
  grep -rn "AlgoTabId\|ALGO_TAB_LABELS\|ALGO_TAB_ORDER" \
    frontend/ --include="*.tsx" --include="*.ts"
  ```
  Expected: only `AlgoTradingClient.tsx`. That file is orphaned
  after Slice 1 and will be deleted in Task 5.3 — no consumers to
  update.

- [ ] **Step 3: Commit**

  ```bash
  git add frontend/lib/types/algoTrading.ts
  git commit -m "refactor(algo-fe): split AlgoTabId into StrategiesTabId + LiveTabId"
  ```

### Task 2.2 — Strategies client with 8 tabs

**Files:**
- Modify: `frontend/app/(authenticated)/algo-trading/strategies/StrategiesClient.tsx`

- [ ] **Step 1: Replace the placeholder body**

  ```tsx
  "use client";

  import { useCallback, useMemo } from "react";
  import { useRouter, useSearchParams } from "next/navigation";

  import {
    STRATEGIES_TAB_LABELS,
    STRATEGIES_TAB_ORDER,
    type StrategiesTabId,
  } from "@/lib/types/algoTrading";

  import { BacktestTab } from "@/components/algo-trading/BacktestTab";
  import { InstrumentsTab } from "@/components/algo-trading/InstrumentsTab";
  import { PaperTab } from "@/components/algo-trading/PaperTab";
  import { DryRunTab } from "@/components/algo-trading/dryrun/DryRunTab";
  import { PerformanceTab } from "@/components/algo-trading/PerformanceTab";
  import { ReplayTab } from "@/components/algo-trading/ReplayTab";
  import { StrategiesSettingsTab } from "@/components/algo-trading/StrategiesSettingsTab";
  import { StrategiesTab } from "@/components/algo-trading/StrategiesTab";

  const DEFAULT_TAB: StrategiesTabId = "instruments";

  function isValidTab(v: string | null): v is StrategiesTabId {
    return (
      v !== null &&
      (STRATEGIES_TAB_ORDER as readonly string[]).includes(v)
    );
  }

  export default function StrategiesClient() {
    const router = useRouter();
    const sp = useSearchParams();
    const raw = sp.get("tab");
    const active: StrategiesTabId = isValidTab(raw) ? raw : DEFAULT_TAB;

    const handleSwitch = useCallback(
      (next: StrategiesTabId) => {
        const params = new URLSearchParams(sp.toString());
        params.set("tab", next);
        router.replace(
          `/algo-trading/strategies?${params.toString()}`,
          { scroll: false },
        );
      },
      [router, sp],
    );

    const tabPanel = useMemo(() => {
      switch (active) {
        case "instruments":
          return <InstrumentsTab />;
        case "strategies":
          return <StrategiesTab />;
        case "backtest":
          return <BacktestTab />;
        case "paper":
          return <PaperTab />;
        case "dryrun":
          return <DryRunTab />;
        case "performance":
          return <PerformanceTab />;
        case "replay":
          return <ReplayTab />;
        case "settings":
          return <StrategiesSettingsTab />;
      }
    }, [active]);

    return (
      <div className="space-y-4 p-6">
        <h1
          className="text-xl font-semibold"
          data-testid="algo-strategies-heading"
        >
          Strategies
        </h1>

        <div
          role="tablist"
          data-testid="algo-strategies-tabs"
          className="flex flex-wrap items-center gap-1 border-b
            border-gray-200 dark:border-gray-700"
        >
          {STRATEGIES_TAB_ORDER.map((id) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={id === active}
              data-testid={`algo-strategies-tab-${id}`}
              onClick={() => handleSwitch(id)}
              className={`px-3 py-2 text-sm transition-colors ${
                id === active
                  ? "border-b-2 border-indigo-500 text-indigo-600 dark:text-indigo-400 font-medium"
                  : "text-gray-600 dark:text-gray-300 hover:text-indigo-600 dark:hover:text-indigo-400"
              }`}
            >
              {STRATEGIES_TAB_LABELS[id]}
            </button>
          ))}
        </div>

        <div
          role="tabpanel"
          data-testid={`algo-strategies-panel-${active}`}
          className="rounded-lg border border-gray-200
            dark:border-gray-700 bg-white dark:bg-gray-900 p-4"
        >
          {tabPanel}
        </div>
      </div>
    );
  }
  ```

  Tasks 2.3 and 2.4 create the new component imports (`DryRunTab`,
  `StrategiesSettingsTab`). The file will not type-check until
  those tasks land — that is expected for this slice. Commit at the
  end of Task 2.5.

### Task 2.3 — Strategies-only Settings tab (Fee Preview)

**Files:**
- Create: `frontend/components/algo-trading/StrategiesSettingsTab.tsx`

- [ ] **Step 1: Write the file**

  ```tsx
  "use client";

  import { FeePreviewWidget } from "./FeePreviewWidget";

  /**
   * Strategies → Settings tab. Hosts ONLY the configuration
   * used by Backtest + Paper runs (fee preview, slippage). Live
   * risk knobs (kill switch, drift threshold, safety belts) live
   * on Live Trading → Settings instead.
   */
  export function StrategiesSettingsTab() {
    return (
      <div className="space-y-4" data-testid="strategies-settings-tab">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Settings
        </h2>
        <FeePreviewWidget />
      </div>
    );
  }
  ```

- [ ] **Step 2: Confirm FeePreviewWidget exists**

  ```bash
  grep -n "export function FeePreviewWidget\|export const FeePreviewWidget" \
    frontend/components/algo-trading/FeePreviewWidget.tsx
  ```
  Expected: one match.

### Task 2.4 — Paper tab reduced to Paper-only

**Files:**
- Modify: `frontend/components/algo-trading/PaperTab.tsx`

The current file (387 LOC) contains Paper + Dry-run + Live branches.
Replace with the Paper-only version below. The Dry-run logic moves
to `DryRunTab.tsx` (Task 2.5). The Live branch is **deleted** — its
destination is Slice 4.

- [ ] **Step 1: Replace the file body wholesale**

  ```tsx
  "use client";

  import { useState } from "react";

  import { useKillSwitch } from "@/hooks/useKillSwitch";
  import {
    usePaperEvents,
  } from "@/hooks/usePaperEvents";

  import { ActiveRunsPanel } from "./ActiveRunsPanel";
  import {
    PaperEventsTimeline,
    type EventsPageSize,
  } from "./PaperEventsTimeline";
  import { PaperSessionSummary } from "./PaperSessionSummary";

  const DEFAULT_EVENTS_PAGE_SIZE: EventsPageSize = 100;

  export function PaperTab() {
    const [eventsPage, setEventsPage] = useState(0);
    const [eventsPageSize, setEventsPageSize] =
      useState<EventsPageSize>(DEFAULT_EVENTS_PAGE_SIZE);

    const { events, loading, error, total } = usePaperEvents(
      eventsPageSize,
      eventsPage * eventsPageSize,
      "paper",        // mode filter
      null,           // dryRun filter — irrelevant for paper mode
    );

    const { state: killState } = useKillSwitch();

    return (
      <div className="space-y-4" data-testid="paper-tab">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
              Paper Trading
            </h2>
            <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
              Replay-fixture runs against a synthetic broker. Use this
              tab to validate strategy logic without touching the live
              Kite runtime.
            </p>
          </div>
          {killState?.active && (
            <span
              className="rounded-full bg-rose-100 px-3 py-1 text-xs
                font-medium text-rose-800 dark:bg-rose-950/50
                dark:text-rose-200"
              data-testid="paper-tab-kill-armed-chip"
            >
              Kill switch ARMED
            </span>
          )}
        </div>

        <ActiveRunsPanel tradingMode="paper" />
        <PaperSessionSummary />

        {error && (
          <div
            className="rounded-md border border-rose-200 bg-rose-50 p-3
              text-sm text-rose-700"
            data-testid="paper-events-error"
          >
            {error}
          </div>
        )}

        <PaperEventsTimeline
          events={events}
          loading={loading}
          page={eventsPage}
          pageSize={eventsPageSize}
          total={total}
          onPageChange={setEventsPage}
          onPageSizeChange={setEventsPageSize}
          emptyMessage="No paper events yet. Start a paper run (replay fixture) to see signals + fills here."
        />
      </div>
    );
  }
  ```

- [ ] **Step 2: Confirm no dead imports remain**

  ```bash
  cd frontend && npx eslint components/algo-trading/PaperTab.tsx
  ```
  Expected: no unused-import warnings.

### Task 2.5 — Dry run tab (new)

**Files:**
- Create: `frontend/components/algo-trading/dryrun/DryRunTab.tsx`
- Create: `frontend/components/algo-trading/dryrun/DryRunArmBanner.tsx`

- [ ] **Step 1: Write the arm banner (TDD — test first)**

  Create `frontend/components/algo-trading/__tests__/DryRunArmBanner.test.tsx`:

  ```tsx
  import { describe, expect, it, vi } from "vitest";
  import { fireEvent, render, screen } from "@testing-library/react";
  import { DryRunArmBanner } from "@/components/algo-trading/dryrun/DryRunArmBanner";

  describe("DryRunArmBanner", () => {
    it("renders disarmed state with Arm button", () => {
      render(
        <DryRunArmBanner armed={false} onToggle={() => {}} />,
      );
      expect(screen.getByText(/dry-run is OFF/i)).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /arm dry-run/i }),
      ).toBeInTheDocument();
    });

    it("renders armed state with Disarm button", () => {
      render(
        <DryRunArmBanner armed={true} onToggle={() => {}} />,
      );
      expect(screen.getByText(/dry-run is ON/i)).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /disarm dry-run/i }),
      ).toBeInTheDocument();
    });

    it("fires onToggle with the opposite state", () => {
      const onToggle = vi.fn();
      render(<DryRunArmBanner armed={false} onToggle={onToggle} />);
      fireEvent.click(
        screen.getByRole("button", { name: /arm dry-run/i }),
      );
      expect(onToggle).toHaveBeenCalledWith(true);
    });
  });
  ```

- [ ] **Step 2: Run the test, confirm it fails**

  ```bash
  cd frontend && npx vitest run DryRunArmBanner
  ```
  Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement the banner**

  Create `frontend/components/algo-trading/dryrun/DryRunArmBanner.tsx`:

  ```tsx
  "use client";

  interface Props {
    armed: boolean;
    onToggle: (next: boolean) => void;
  }

  export function DryRunArmBanner({ armed, onToggle }: Props) {
    return (
      <div
        className="rounded-md border border-amber-200 bg-amber-50
          dark:bg-amber-950/30 dark:border-amber-900/50 px-3 py-2
          flex items-center justify-between"
        data-testid="dryrun-arm-banner"
      >
        <div className="text-xs text-amber-800 dark:text-amber-200">
          {armed
            ? "Dry-run is ON — the live runtime accepts orders but Kite responses are synthesised."
            : "Dry-run is OFF — arm it before starting a live-runtime rehearsal."}
        </div>
        <button
          type="button"
          onClick={() => onToggle(!armed)}
          className={`rounded-md px-3 py-1 text-xs font-medium ${
            armed
              ? "bg-amber-600 text-white hover:bg-amber-700"
              : "bg-amber-100 text-amber-900 hover:bg-amber-200"
          }`}
          data-testid="dryrun-arm-button"
        >
          {armed ? "Disarm dry-run" : "Arm dry-run"}
        </button>
      </div>
    );
  }
  ```

- [ ] **Step 4: Re-run the test, confirm it passes**

  ```bash
  cd frontend && npx vitest run DryRunArmBanner
  ```
  Expected: PASS (3 assertions).

- [ ] **Step 5: Write the DryRunTab container**

  Create `frontend/components/algo-trading/dryrun/DryRunTab.tsx`:

  ```tsx
  "use client";

  import { useCallback, useState } from "react";
  import useSWR from "swr";

  import { apiFetch } from "@/lib/apiFetch";
  import { API_URL } from "@/lib/config";
  import { usePaperEvents } from "@/hooks/usePaperEvents";
  import { useStrategies } from "@/hooks/useStrategies";

  import { ActiveRunsPanel } from "../ActiveRunsPanel";
  import { AttributionPanel } from "../AttributionPanel";
  import { KitePostbackPanel } from "../KitePostbackPanel";
  import { LiveWsHealthDot } from "../LiveWsHealthDot";
  import {
    PaperEventsTimeline,
    type EventsPageSize,
  } from "../PaperEventsTimeline";
  import { PaperSessionSummary } from "../PaperSessionSummary";
  import { ReconciliationDriftPanel } from "../ReconciliationDriftPanel";
  import { RegimeHistoryChart } from "../RegimeHistoryChart";
  import { RegimeWidget } from "../RegimeWidget";

  import { DryRunArmBanner } from "./DryRunArmBanner";

  const DEFAULT_PAGE_SIZE: EventsPageSize = 100;

  const DRY_RUN_KEY = `${API_URL}/algo/live/dry-run`;

  async function fetchDryRunState(url: string): Promise<{ armed: boolean }> {
    const r = await apiFetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function setDryRun(armed: boolean): Promise<void> {
    const path = armed
      ? `${API_URL}/algo/live/dry-run/arm`
      : `${API_URL}/algo/live/dry-run/disarm`;
    await apiFetch(path, { method: "POST" });
  }

  export function DryRunTab() {
    const { data, mutate } = useSWR(DRY_RUN_KEY, fetchDryRunState, {
      revalidateOnFocus: false,
    });
    const armed = data?.armed ?? false;

    const onToggleArm = useCallback(
      async (next: boolean) => {
        await mutate({ armed: next }, false);
        try {
          await setDryRun(next);
        } catch {
          // revalidate so the UI reflects the actual backend state
          await mutate();
        }
      },
      [mutate],
    );

    const [page, setPage] = useState(0);
    const [pageSize, setPageSize] = useState<EventsPageSize>(
      DEFAULT_PAGE_SIZE,
    );
    const { events, loading, error, total } = usePaperEvents(
      pageSize,
      page * pageSize,
      "live",
      true, // dry_run filter
    );

    const { strategies } = useStrategies();

    return (
      <div className="space-y-4" data-testid="dryrun-tab">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
                Dry Run
              </h2>
              <RegimeWidget />
            </div>
            <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
              Rehearses the live runtime with synthetic Kite responses.
              Use this to validate gating and order plumbing before
              going to Live Trading.
            </p>
          </div>
          <LiveWsHealthDot />
        </div>

        <DryRunArmBanner armed={armed} onToggle={onToggleArm} />

        <ReconciliationDriftPanel />

        <ActiveRunsPanel tradingMode="dryrun" />

        <KitePostbackPanel />

        <AttributionPanel strategyId={strategies[0]?.id ?? null} />

        <RegimeHistoryChart />

        <PaperSessionSummary mode="live" dryRun={true} />

        {error && (
          <div
            className="rounded-md border border-rose-200 bg-rose-50 p-3
              text-sm text-rose-700"
            data-testid="dryrun-events-error"
          >
            {error}
          </div>
        )}

        <PaperEventsTimeline
          events={events}
          loading={loading}
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={setPage}
          onPageSizeChange={setPageSize}
          emptyMessage="No dry-run events yet. Arm dry-run, then start a run from the panel above."
        />
      </div>
    );
  }
  ```

- [ ] **Step 6: Type-check + smoke**

  ```bash
  cd frontend && npx tsc --noEmit
  ```
  Expected: no errors (all imports resolve now that Tasks 2.3 + 2.5
  have landed `StrategiesSettingsTab` and `DryRunTab`).

- [ ] **Step 7: Run the full unit suite**

  ```bash
  cd frontend && npx vitest run
  ```
  Expected: PASS — no existing tests reference deleted Paper-tab
  internals (the `ViewMode` enum, `setDryRunRedis()`, etc.).

- [ ] **Step 8: Manual smoke**

  ```bash
  ./run.sh restart frontend
  ```
  Browse to `/algo-trading/strategies`, click each of the 8 tabs.
  Paper renders without Live/Dry-run sub-toggle; Dry-run renders
  with the amber banner, WS dot, postbacks, regime, attribution.

- [ ] **Step 9: Commit**

  ```bash
  git add frontend/app/\(authenticated\)/algo-trading/strategies/StrategiesClient.tsx \
          frontend/components/algo-trading/StrategiesSettingsTab.tsx \
          frontend/components/algo-trading/PaperTab.tsx \
          frontend/components/algo-trading/dryrun/ \
          frontend/components/algo-trading/__tests__/DryRunArmBanner.test.tsx
  git commit -m "feat(algo-fe): wire Strategies page tabs + split Paper/Dry-run"
  ```

- [ ] **Step 10: PR**

  Slice 2 PR titled `feat(algo-fe): Strategies page + Paper/Dry-run
  split (slice 2/6)`.

---

## Slice 3 — Backend: dashboard summary + positions + holdings (1 day, 1 PR)

Three new GET endpoints inside `create_live_router()`. Each reads
from Kite REST and (for positions/holdings) joins
`paper_events_v3` for strategy attribution. 15-second Redis cache
per CLAUDE.md § 5.13.

### Task 3.1 — Pydantic response models

**Files:**
- Modify: `backend/algo/routes/live.py` (top — model declarations)

- [ ] **Step 1: Add the three models**

  Append the following Pydantic models near the existing
  `UpsertCapsRequest` block in `live.py`:

  ```python
  class DashboardSummary(BaseModel):
      today_pnl_inr: Decimal = Field(default=Decimal("0"))
      open_pnl_inr: Decimal = Field(default=Decimal("0"))
      realised_pnl_inr: Decimal = Field(default=Decimal("0"))
      cash_inr: Decimal = Field(default=Decimal("0"))
      open_position_count: int = 0
      mode: str  # "live" | "dry_run"
      ws_age_seconds: int | None = None
      kill_switch_active: bool = False


  class PositionRow(BaseModel):
      tradingsymbol: str
      exchange: str
      quantity: int
      average_price: Decimal
      last_price: Decimal
      pnl_inr: Decimal
      pnl_pct: Decimal
      product: str
      strategy_id: str | None = None
      strategy_name: str | None = None
      entry_ts_utc: datetime | None = None
      entry_reason: str | None = None


  class HoldingRow(BaseModel):
      tradingsymbol: str
      exchange: str
      quantity: int
      average_price: Decimal
      last_price: Decimal
      pnl_inr: Decimal
      pnl_pct: Decimal
      days_held: int | None = None
      strategy_id: str | None = None
      strategy_name: str | None = None


  class PositionsResponse(BaseModel):
      rows: list[PositionRow]
      ledger_drift: bool = False


  class HoldingsResponse(BaseModel):
      rows: list[HoldingRow]
      ledger_drift: bool = False
  ```

### Task 3.2 — `GET /algo/live/dashboard-summary`

**Files:**
- Modify: `backend/algo/routes/live.py` (new endpoint inside
  `create_live_router()`)
- Create: `backend/algo/tests/test_live_dashboard_summary.py`

- [ ] **Step 1: Write the failing test**

  Create `backend/algo/tests/test_live_dashboard_summary.py`:

  ```python
  """Unit tests for GET /algo/live/dashboard-summary."""

  from __future__ import annotations

  from decimal import Decimal
  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest
  from fastapi.testclient import TestClient

  from backend.main import app


  @pytest.fixture
  def client():
      return TestClient(app)


  def _kite_mock(open_pnl: float, day_pnl: float, cash: float):
      kc = MagicMock()
      kc.positions.return_value = {
          "net": [
              {"tradingsymbol": "ITC", "quantity": 8, "pnl": open_pnl},
          ],
          "day": [
              {"tradingsymbol": "ITC", "pnl": day_pnl},
          ],
      }
      kc.margins.return_value = {
          "available": {"live_balance": cash},
      }
      return kc


  @patch("backend.algo.routes.live._get_kite")
  def test_dashboard_summary_happy_path(get_kite, client, auth_token):
      kc = _kite_mock(open_pnl=820.30, day_pnl=1240.50, cash=98432.10)
      get_kite.return_value = MagicMock(_kc=kc)
      r = client.get(
          "/v1/algo/live/dashboard-summary",
          headers={"Authorization": f"Bearer {auth_token}"},
      )
      assert r.status_code == 200
      body = r.json()
      assert Decimal(body["today_pnl_inr"]) == Decimal("1240.50")
      assert Decimal(body["open_pnl_inr"]) == Decimal("820.30")
      assert Decimal(body["cash_inr"]) == Decimal("98432.10")
      assert body["open_position_count"] == 1
      assert body["mode"] in {"live", "dry_run"}
  ```

  The fixture `auth_token` is in the existing
  `backend/algo/tests/conftest.py`. The helper `_get_kite` is the
  symbol Task 3.2 Step 2 will introduce — grep first:

  ```bash
  grep -n "def _get_kite\|kite._kc\|get_kite_client" \
    backend/algo/routes/live.py | head -10
  ```
  If `_get_kite` doesn't exist, name the helper based on what's
  actually in the file (e.g., `get_kite_client`) and update the
  patch target to match.

- [ ] **Step 2: Run the test, confirm it fails**

  ```bash
  python -m pytest \
    backend/algo/tests/test_live_dashboard_summary.py -v
  ```
  Expected: FAIL with 404 (endpoint not registered).

- [ ] **Step 3: Implement the endpoint**

  Inside `create_live_router()` in `backend/algo/routes/live.py`,
  add the following endpoint (placement: just after the existing
  `/dry-run` endpoint block):

  ```python
  @router.get(
      "/dashboard-summary",
      response_model=DashboardSummary,
  )
  async def dashboard_summary(
      user: UserContext = Depends(pro_or_superuser),
  ) -> DashboardSummary:
      """Aggregated KPIs for the Live Trading header strip.

      Reads Kite REST (positions, margins) + paper_events_v3
      realised P&L. Cached 15s under cache:algo:dash:{user_id}.
      """
      from backend.db.cache import cache  # local import — module-level
                                          # would create a circular
                                          # import in test harnesses

      cache_key = f"cache:algo:dash:{user.id}"
      cached = cache.get(cache_key)
      if cached:
          return DashboardSummary.model_validate_json(cached)

      kite = _get_kite(user)        # existing helper — verify name
      kc = kite._kc

      positions = await asyncio.to_thread(kc.positions)
      margins = await asyncio.to_thread(kc.margins, "equity")

      net_rows = (
          positions.get("net", []) if isinstance(positions, dict) else []
      )
      day_rows = (
          positions.get("day", []) if isinstance(positions, dict) else []
      )
      open_pnl = sum(
          Decimal(str(r.get("pnl", 0)))
          for r in net_rows
          if r.get("quantity", 0) != 0
      )
      today_pnl = sum(
          Decimal(str(r.get("pnl", 0))) for r in day_rows
      )
      cash = Decimal(
          str(margins.get("available", {}).get("live_balance", 0))
      )

      open_count = sum(
          1 for r in net_rows if r.get("quantity", 0) != 0
      )

      realised = await _realised_pnl_today(user.id)
      dry_run = await _dry_run_armed(user.id)
      ws_age = await _ws_age_seconds(user.id)
      ks = await _kill_switch_active(user.id)

      out = DashboardSummary(
          today_pnl_inr=today_pnl,
          open_pnl_inr=open_pnl,
          realised_pnl_inr=realised,
          cash_inr=cash,
          open_position_count=open_count,
          mode="dry_run" if dry_run else "live",
          ws_age_seconds=ws_age,
          kill_switch_active=ks,
      )
      cache.set(cache_key, out.model_dump_json(), ttl=15)
      return out
  ```

  The helpers `_realised_pnl_today`, `_dry_run_armed`,
  `_ws_age_seconds`, `_kill_switch_active` are NEW. Implement them
  at module level — each is a few lines:

  ```python
  async def _realised_pnl_today(user_id: str) -> Decimal:
      """Sum realised P&L from paper_events_v3 mode=live, dry_run=false,
      today IST. Returns 0 on Iceberg read error."""
      # IST midnight UTC bound
      ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
      ist_mid = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
      since = ist_mid.astimezone(UTC).replace(tzinfo=None)
      try:
          from backend.db.duckdb_engine import query_iceberg_table
          rows = await asyncio.to_thread(
              query_iceberg_table,
              "stocks.paper_events_v3",
              filter_expr=(
                  f"user_id = '{user_id}' "
                  f"AND mode = 'live' "
                  f"AND dry_run = false "
                  f"AND event_type = 'pnl_realised' "
                  f"AND ts >= '{since.isoformat()}'"
              ),
              columns=["payload"],
          )
      except Exception:
          _logger.warning("realised P&L read failed", exc_info=True)
          return Decimal("0")
      total = Decimal("0")
      for r in rows:
          payload = r.get("payload") or {}
          v = payload.get("realised_inr")
          if v is not None:
              total += Decimal(str(v))
      return total


  async def _dry_run_armed(user_id: str) -> bool:
      """Read per-user dry-run flag from Redis."""
      from backend.db.cache import cache
      raw = cache.get(f"algo:live:dry_run:{user_id}")
      return bool(raw) and raw not in {"0", "false", "False"}


  async def _ws_age_seconds(user_id: str) -> int | None:
      """Seconds since last Kite WS tick. None if never connected."""
      from backend.db.cache import cache
      raw = cache.get(f"algo:live:ws:last_tick:{user_id}")
      if not raw:
          return None
      try:
          last = datetime.fromisoformat(raw)
      except Exception:
          return None
      return max(0, int((datetime.now(UTC) - last).total_seconds()))


  async def _kill_switch_active(user_id: str) -> bool:
      from backend.db.cache import cache
      return bool(cache.get(f"algo:kill_switch:{user_id}"))
  ```

  Verify the Redis key names match what the existing code uses:

  ```bash
  grep -rn "algo:live:dry_run\|algo:live:ws\|algo:kill_switch" \
    backend/algo/ | head -20
  ```
  If keys differ, adjust to match the existing producers — do not
  invent new key names.

- [ ] **Step 4: Run the test, confirm it passes**

  ```bash
  python -m pytest \
    backend/algo/tests/test_live_dashboard_summary.py -v
  ```
  Expected: PASS.

- [ ] **Step 5: Restart backend (new endpoint per CLAUDE.md § 6.2)**

  ```bash
  docker compose restart backend
  sleep 5
  ```

- [ ] **Step 6: Smoke**

  ```bash
  TOK="<your-jwt>"   # from `./run.sh token` or your shell
  curl -s -H "Authorization: Bearer $TOK" \
    http://localhost:8181/v1/algo/live/dashboard-summary | jq .
  ```
  Expected: JSON with the 8 fields. Numeric fields may be `"0"` if
  Kite is not connected — that is correct.

- [ ] **Step 7: Commit**

  ```bash
  git add backend/algo/routes/live.py \
          backend/algo/tests/test_live_dashboard_summary.py
  git commit -m "feat(algo-be): GET /algo/live/dashboard-summary"
  ```

### Task 3.3 — `GET /algo/live/positions`

**Files:**
- Modify: `backend/algo/routes/live.py`
- Create: `backend/algo/tests/test_live_positions.py`

- [ ] **Step 1: Write the failing test**

  ```python
  """Unit tests for GET /algo/live/positions."""

  from unittest.mock import MagicMock, patch
  import pytest
  from fastapi.testclient import TestClient

  from backend.main import app


  @pytest.fixture
  def client():
      return TestClient(app)


  @patch("backend.algo.routes.live._fetch_strategy_attribution")
  @patch("backend.algo.routes.live._get_kite")
  def test_positions_joined_with_strategy(
      get_kite, attr, client, auth_token,
  ):
      kc = MagicMock()
      kc.positions.return_value = {
          "net": [
              {
                  "tradingsymbol": "ITC", "exchange": "NSE",
                  "quantity": 8, "average_price": 307.33,
                  "last_price": 311.20, "pnl": 30.96,
                  "product": "MIS",
              },
              {
                  "tradingsymbol": "EXIT", "exchange": "NSE",
                  "quantity": 0, "average_price": 0,
                  "last_price": 0, "pnl": 0, "product": "MIS",
              },
          ],
      }
      get_kite.return_value = MagicMock(_kc=kc)
      attr.return_value = {
          ("ITC", "MIS"): {
              "strategy_id": "v3", "strategy_name": "V3 Multi",
              "entry_ts_utc": "2026-05-11T04:19:54+00:00",
              "entry_reason": "BULL · momentum_z=1.4",
          },
      }
      r = client.get(
          "/v1/algo/live/positions",
          headers={"Authorization": f"Bearer {auth_token}"},
      )
      assert r.status_code == 200
      rows = r.json()["rows"]
      assert len(rows) == 1               # quantity=0 row filtered
      assert rows[0]["tradingsymbol"] == "ITC"
      assert rows[0]["strategy_id"] == "v3"
      assert rows[0]["entry_reason"].startswith("BULL")


  @patch("backend.algo.routes.live._fetch_strategy_attribution")
  @patch("backend.algo.routes.live._get_kite")
  def test_positions_without_attribution(
      get_kite, attr, client, auth_token,
  ):
      kc = MagicMock()
      kc.positions.return_value = {"net": [
          {"tradingsymbol": "MANUAL", "exchange": "NSE",
           "quantity": 1, "average_price": 100, "last_price": 101,
           "pnl": 1, "product": "MIS"},
      ]}
      get_kite.return_value = MagicMock(_kc=kc)
      attr.return_value = {}
      r = client.get(
          "/v1/algo/live/positions",
          headers={"Authorization": f"Bearer {auth_token}"},
      )
      rows = r.json()["rows"]
      assert rows[0]["strategy_id"] is None
      assert rows[0]["entry_reason"] is None
  ```

- [ ] **Step 2: Run, confirm fail**

  ```bash
  python -m pytest backend/algo/tests/test_live_positions.py -v
  ```
  Expected: FAIL — endpoint missing.

- [ ] **Step 3: Implement the endpoint + attribution helper**

  Inside `create_live_router()`:

  ```python
  @router.get("/positions", response_model=PositionsResponse)
  async def positions(
      user: UserContext = Depends(pro_or_superuser),
  ) -> PositionsResponse:
      kite = _get_kite(user)
      kc = kite._kc
      raw = await asyncio.to_thread(kc.positions)
      net = raw.get("net", []) if isinstance(raw, dict) else []
      open_rows = [r for r in net if r.get("quantity", 0) != 0]

      attr = await _fetch_strategy_attribution(
          user.id,
          [(r["tradingsymbol"], r["product"]) for r in open_rows],
      )

      out_rows: list[PositionRow] = []
      for r in open_rows:
          key = (r["tradingsymbol"], r["product"])
          ctx = attr.get(key, {})
          qty = int(r.get("quantity", 0))
          avg = Decimal(str(r.get("average_price", 0)))
          ltp = Decimal(str(r.get("last_price", 0)))
          pnl_inr = Decimal(str(r.get("pnl", 0)))
          pnl_pct = (
              ((ltp - avg) / avg) * Decimal("100")
              if avg > 0 else Decimal("0")
          )
          out_rows.append(PositionRow(
              tradingsymbol=r["tradingsymbol"],
              exchange=r.get("exchange", "NSE"),
              quantity=qty,
              average_price=avg,
              last_price=ltp,
              pnl_inr=pnl_inr,
              pnl_pct=pnl_pct,
              product=r["product"],
              strategy_id=ctx.get("strategy_id"),
              strategy_name=ctx.get("strategy_name"),
              entry_ts_utc=(
                  datetime.fromisoformat(ctx["entry_ts_utc"])
                  if ctx.get("entry_ts_utc") else None
              ),
              entry_reason=ctx.get("entry_reason"),
          ))

      drift = await _ledger_kite_drift(user.id, open_rows)
      return PositionsResponse(rows=out_rows, ledger_drift=drift)
  ```

  And the helper at module level:

  ```python
  async def _fetch_strategy_attribution(
      user_id: str,
      keys: list[tuple[str, str]],
  ) -> dict[tuple[str, str], dict[str, Any]]:
      """For each (tradingsymbol, product), find today's first BUY
      fill in paper_events_v3 and return strategy_id, name, entry ts,
      and entry reason. Empty dict on Iceberg read failure.
      """
      if not keys:
          return {}
      ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
      ist_mid = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
      since = ist_mid.astimezone(UTC).replace(tzinfo=None)
      symbols = "','".join({s for s, _ in keys})
      try:
          from backend.db.duckdb_engine import query_iceberg_table
          rows = await asyncio.to_thread(
              query_iceberg_table,
              "stocks.paper_events_v3",
              filter_expr=(
                  f"user_id = '{user_id}' "
                  f"AND mode = 'live' "
                  f"AND dry_run = false "
                  f"AND event_type = 'order_filled' "
                  f"AND ts >= '{since.isoformat()}' "
                  f"AND tradingsymbol IN ('{symbols}')"
              ),
              columns=[
                  "tradingsymbol", "product", "ts", "payload",
                  "strategy_id", "strategy_name",
              ],
              order_by="ts ASC",
          )
      except Exception:
          _logger.warning("attribution read failed", exc_info=True)
          return {}
      out: dict[tuple[str, str], dict[str, Any]] = {}
      for row in rows:
          key = (row["tradingsymbol"], row.get("product", "MIS"))
          if key in out:
              continue
          payload = row.get("payload") or {}
          if payload.get("side") not in {"BUY", "buy"}:
              continue
          out[key] = {
              "strategy_id": row.get("strategy_id"),
              "strategy_name": row.get("strategy_name"),
              "entry_ts_utc": (
                  row["ts"].isoformat() if hasattr(row["ts"], "isoformat")
                  else str(row["ts"])
              ),
              "entry_reason": payload.get("reason"),
          }
      return out


  async def _ledger_kite_drift(
      user_id: str,
      kite_rows: list[dict[str, Any]],
  ) -> bool:
      """True if our ledger has open positions Kite doesn't (or
      vice-versa). Cheap signal — caller surfaces drift chip."""
      # Implementation: compare set of open tradingsymbols from
      # paper_events_v3 (today, mode=live, dry_run=false, BUY > SELL)
      # against the Kite symbols. Quiet on read failure.
      try:
          kite_syms = {r["tradingsymbol"] for r in kite_rows}
          # Reuse existing drift helper if one exists; else compute
          # a quick approximation.
          from backend.algo.drift_compute import open_tickers_today
          ledger_syms = await asyncio.to_thread(
              open_tickers_today, user_id,
          )
      except Exception:
          return False
      return kite_syms != ledger_syms
  ```

  Grep first to confirm `open_tickers_today` exists; if not, omit
  the drift detection (return False) — `ReconciliationDriftPanel`
  on the frontend already has its own data source for that flag.

  ```bash
  grep -rn "open_tickers_today\|drift_compute" backend/algo/ | head
  ```

- [ ] **Step 4: Run tests, confirm pass**

  ```bash
  python -m pytest backend/algo/tests/test_live_positions.py -v
  ```
  Expected: PASS (2 tests).

- [ ] **Step 5: Restart + smoke**

  ```bash
  docker compose restart backend && sleep 5
  curl -s -H "Authorization: Bearer $TOK" \
    http://localhost:8181/v1/algo/live/positions | jq .
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add backend/algo/routes/live.py \
          backend/algo/tests/test_live_positions.py
  git commit -m "feat(algo-be): GET /algo/live/positions with attribution join"
  ```

### Task 3.4 — `GET /algo/live/holdings`

**Files:**
- Modify: `backend/algo/routes/live.py`
- Create: `backend/algo/tests/test_live_holdings.py`

Pattern identical to Task 3.3 — same structure, swap the Kite call
to `kc.holdings()` and add `days_held` from the ledger's first-fill
date. Test covers: (a) a holding with strategy_id resolved, (b) a
holding with no ledger match (`strategy_id = None`,
`days_held = None`).

- [ ] **Step 1: Write the failing test**

  Create `backend/algo/tests/test_live_holdings.py`:

  ```python
  from unittest.mock import MagicMock, patch
  import pytest
  from fastapi.testclient import TestClient
  from backend.main import app


  @pytest.fixture
  def client():
      return TestClient(app)


  @patch("backend.algo.routes.live._fetch_holding_attribution")
  @patch("backend.algo.routes.live._get_kite")
  def test_holdings_with_days_held(get_kite, attr, client, auth_token):
      kc = MagicMock()
      kc.holdings.return_value = [
          {
              "tradingsymbol": "ITC", "exchange": "NSE",
              "quantity": 8, "average_price": 305.0,
              "last_price": 311.2, "pnl": 49.6,
          },
      ]
      get_kite.return_value = MagicMock(_kc=kc)
      attr.return_value = {
          "ITC": {"strategy_id": "v3", "strategy_name": "V3",
                  "days_held": 3},
      }
      r = client.get(
          "/v1/algo/live/holdings",
          headers={"Authorization": f"Bearer {auth_token}"},
      )
      rows = r.json()["rows"]
      assert rows[0]["days_held"] == 3
      assert rows[0]["strategy_id"] == "v3"


  @patch("backend.algo.routes.live._fetch_holding_attribution")
  @patch("backend.algo.routes.live._get_kite")
  def test_holdings_without_ledger_match(get_kite, attr, client, auth_token):
      kc = MagicMock()
      kc.holdings.return_value = [
          {"tradingsymbol": "EXTERN", "exchange": "NSE",
           "quantity": 5, "average_price": 100, "last_price": 110,
           "pnl": 50},
      ]
      get_kite.return_value = MagicMock(_kc=kc)
      attr.return_value = {}
      r = client.get(
          "/v1/algo/live/holdings",
          headers={"Authorization": f"Bearer {auth_token}"},
      )
      rows = r.json()["rows"]
      assert rows[0]["strategy_id"] is None
      assert rows[0]["days_held"] is None
  ```

- [ ] **Step 2: Run, confirm fail**

  ```bash
  python -m pytest backend/algo/tests/test_live_holdings.py -v
  ```
  Expected: FAIL — endpoint missing.

- [ ] **Step 3: Implement the endpoint + helper**

  Inside `create_live_router()`:

  ```python
  @router.get("/holdings", response_model=HoldingsResponse)
  async def holdings(
      user: UserContext = Depends(pro_or_superuser),
  ) -> HoldingsResponse:
      kite = _get_kite(user)
      kc = kite._kc
      raw = await asyncio.to_thread(kc.holdings)
      rows = raw if isinstance(raw, list) else []
      open_rows = [r for r in rows if r.get("quantity", 0) > 0]

      attr = await _fetch_holding_attribution(
          user.id, [r["tradingsymbol"] for r in open_rows],
      )
      out: list[HoldingRow] = []
      for r in open_rows:
          ctx = attr.get(r["tradingsymbol"], {})
          qty = int(r.get("quantity", 0))
          avg = Decimal(str(r.get("average_price", 0)))
          ltp = Decimal(str(r.get("last_price", 0)))
          pnl_inr = Decimal(str(r.get("pnl", 0)))
          pnl_pct = (
              ((ltp - avg) / avg) * Decimal("100")
              if avg > 0 else Decimal("0")
          )
          out.append(HoldingRow(
              tradingsymbol=r["tradingsymbol"],
              exchange=r.get("exchange", "NSE"),
              quantity=qty, average_price=avg, last_price=ltp,
              pnl_inr=pnl_inr, pnl_pct=pnl_pct,
              days_held=ctx.get("days_held"),
              strategy_id=ctx.get("strategy_id"),
              strategy_name=ctx.get("strategy_name"),
          ))
      return HoldingsResponse(rows=out, ledger_drift=False)
  ```

  Module-level helper:

  ```python
  async def _fetch_holding_attribution(
      user_id: str, symbols: list[str],
  ) -> dict[str, dict[str, Any]]:
      """For each holding symbol, find the earliest live BUY fill in
      paper_events_v3 (no date floor) and return strategy + days_held.
      """
      if not symbols:
          return {}
      sym_csv = "','".join(symbols)
      try:
          from backend.db.duckdb_engine import query_iceberg_table
          rows = await asyncio.to_thread(
              query_iceberg_table,
              "stocks.paper_events_v3",
              filter_expr=(
                  f"user_id = '{user_id}' "
                  f"AND mode = 'live' "
                  f"AND dry_run = false "
                  f"AND event_type = 'order_filled' "
                  f"AND tradingsymbol IN ('{sym_csv}')"
              ),
              columns=["tradingsymbol", "ts", "payload",
                       "strategy_id", "strategy_name"],
              order_by="ts ASC",
          )
      except Exception:
          _logger.warning("holding attribution read failed", exc_info=True)
          return {}
      out: dict[str, dict[str, Any]] = {}
      today_ist = datetime.now(
          timezone(timedelta(hours=5, minutes=30))
      ).date()
      for row in rows:
          sym = row["tradingsymbol"]
          if sym in out:
              continue
          payload = row.get("payload") or {}
          if payload.get("side") not in {"BUY", "buy"}:
              continue
          ts = row["ts"]
          entry_ist = (
              ts.astimezone(timezone(timedelta(hours=5, minutes=30)))
              if hasattr(ts, "astimezone") else ts
          ).date() if hasattr(ts, "date") else None
          days_held = (
              (today_ist - entry_ist).days
              if entry_ist else None
          )
          out[sym] = {
              "strategy_id": row.get("strategy_id"),
              "strategy_name": row.get("strategy_name"),
              "days_held": days_held,
          }
      return out
  ```

- [ ] **Step 4: Pass + restart + smoke + commit (same pattern as 3.3)**

  ```bash
  python -m pytest backend/algo/tests/test_live_holdings.py -v
  docker compose restart backend && sleep 5
  curl -s -H "Authorization: Bearer $TOK" \
    http://localhost:8181/v1/algo/live/holdings | jq .
  git add backend/algo/routes/live.py \
          backend/algo/tests/test_live_holdings.py
  git commit -m "feat(algo-be): GET /algo/live/holdings with days-held"
  ```

- [ ] **Step 5: PR**

  Slice 3 PR titled `feat(algo-be): live dashboard summary +
  positions + holdings (slice 3/6)`.

---

## Slice 4 — Live dashboard + Positions + Holdings tabs (2 days, 1 PR)

The largest slice. Builds the rose-accent Live page with the sticky
KPI strip, 4-zone grid, and three additional tabs. Uses the
endpoints from Slice 3.

### Task 4.1 — Data hooks

**Files:**
- Create: `frontend/hooks/useLiveDashboardSummary.ts`
- Create: `frontend/hooks/useLivePositions.ts`
- Create: `frontend/hooks/useLiveHoldings.ts`

- [ ] **Step 1: Write the three hooks (matching the SWR pattern in
      `frontend/hooks/useLiveCaps.ts`)**

  `useLiveDashboardSummary.ts`:

  ```ts
  "use client";

  import useSWR from "swr";
  import { apiFetch } from "@/lib/apiFetch";
  import { API_URL } from "@/lib/config";

  export interface LiveDashboardSummary {
    today_pnl_inr: string;
    open_pnl_inr: string;
    realised_pnl_inr: string;
    cash_inr: string;
    open_position_count: number;
    mode: "live" | "dry_run";
    ws_age_seconds: number | null;
    kill_switch_active: boolean;
  }

  async function fetchSummary(
    url: string,
  ): Promise<LiveDashboardSummary> {
    const r = await apiFetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  export function useLiveDashboardSummary() {
    const { data, error, isLoading, mutate } = useSWR(
      `${API_URL}/algo/live/dashboard-summary`,
      fetchSummary,
      {
        revalidateOnFocus: false,
        refreshInterval: 15_000,
        dedupingInterval: 5_000,
      },
    );
    return { summary: data, error, loading: isLoading, refresh: mutate };
  }
  ```

  `useLivePositions.ts`:

  ```ts
  "use client";

  import useSWR from "swr";
  import { apiFetch } from "@/lib/apiFetch";
  import { API_URL } from "@/lib/config";

  export interface PositionRow {
    tradingsymbol: string;
    exchange: string;
    quantity: number;
    average_price: string;
    last_price: string;
    pnl_inr: string;
    pnl_pct: string;
    product: string;
    strategy_id: string | null;
    strategy_name: string | null;
    entry_ts_utc: string | null;
    entry_reason: string | null;
  }

  interface Response {
    rows: PositionRow[];
    ledger_drift: boolean;
  }

  async function fetcher(url: string): Promise<Response> {
    const r = await apiFetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  export function useLivePositions() {
    const { data, error, isLoading, mutate } = useSWR(
      `${API_URL}/algo/live/positions`,
      fetcher,
      {
        revalidateOnFocus: false,
        refreshInterval: 10_000,
        dedupingInterval: 3_000,
      },
    );
    return {
      rows: data?.rows,
      ledger_drift: data?.ledger_drift ?? false,
      error,
      loading: isLoading,
      refresh: mutate,
    };
  }
  ```

  `useLiveHoldings.ts` — same shape, swap URL and row type:

  ```ts
  "use client";

  import useSWR from "swr";
  import { apiFetch } from "@/lib/apiFetch";
  import { API_URL } from "@/lib/config";

  export interface HoldingRow {
    tradingsymbol: string;
    exchange: string;
    quantity: number;
    average_price: string;
    last_price: string;
    pnl_inr: string;
    pnl_pct: string;
    days_held: number | null;
    strategy_id: string | null;
    strategy_name: string | null;
  }

  interface Response {
    rows: HoldingRow[];
    ledger_drift: boolean;
  }

  async function fetcher(url: string): Promise<Response> {
    const r = await apiFetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  export function useLiveHoldings() {
    const { data, error, isLoading, mutate } = useSWR(
      `${API_URL}/algo/live/holdings`,
      fetcher,
      {
        revalidateOnFocus: false,
        refreshInterval: 30_000,
        dedupingInterval: 10_000,
      },
    );
    return {
      rows: data?.rows,
      ledger_drift: data?.ledger_drift ?? false,
      error,
      loading: isLoading,
      refresh: mutate,
    };
  }
  ```

- [ ] **Step 2: Type-check**

  ```bash
  cd frontend && npx tsc --noEmit
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add frontend/hooks/useLiveDashboardSummary.ts \
          frontend/hooks/useLivePositions.ts \
          frontend/hooks/useLiveHoldings.ts
  git commit -m "feat(algo-fe): SWR hooks for live dashboard summary/positions/holdings"
  ```

### Task 4.2 — LiveModeChip + LiveHeaderStrip (TDD)

**Files:**
- Create: `frontend/components/algo-trading/live/LiveModeChip.tsx`
- Create: `frontend/components/algo-trading/live/LiveHeaderStrip.tsx`
- Create: `frontend/components/algo-trading/__tests__/LiveModeChip.test.tsx`
- Create: `frontend/components/algo-trading/__tests__/LiveHeaderStrip.test.tsx`

- [ ] **Step 1: LiveModeChip test (write first)**

  ```tsx
  import { describe, expect, it } from "vitest";
  import { render, screen } from "@testing-library/react";
  import { LiveModeChip } from "@/components/algo-trading/live/LiveModeChip";

  describe("LiveModeChip", () => {
    it("LIVE ARMED — rose-600", () => {
      render(<LiveModeChip mode="live" armed={true} />);
      const chip = screen.getByTestId("live-mode-chip");
      expect(chip.textContent).toMatch(/live armed/i);
      expect(chip.className).toContain("bg-rose-600");
    });
    it("LIVE DISARMED — slate-400", () => {
      render(<LiveModeChip mode="live" armed={false} />);
      const chip = screen.getByTestId("live-mode-chip");
      expect(chip.textContent).toMatch(/disarmed/i);
      expect(chip.className).toContain("bg-slate-400");
    });
    it("DRY-RUN — amber-500 (defensive — should never happen on Live page)", () => {
      render(<LiveModeChip mode="dry_run" armed={false} />);
      const chip = screen.getByTestId("live-mode-chip");
      expect(chip.textContent).toMatch(/dry/i);
      expect(chip.className).toContain("bg-amber-500");
    });
  });
  ```

- [ ] **Step 2: Run, confirm fail; implement**

  ```bash
  cd frontend && npx vitest run LiveModeChip
  ```
  Then create the component:

  ```tsx
  "use client";

  interface Props {
    mode: "live" | "dry_run";
    armed: boolean;
  }

  export function LiveModeChip({ mode, armed }: Props) {
    let label: string;
    let bg: string;
    if (mode === "dry_run") {
      label = "DRY-RUN";
      bg = "bg-amber-500";
    } else if (armed) {
      label = "LIVE ARMED";
      bg = "bg-rose-600";
    } else {
      label = "LIVE DISARMED";
      bg = "bg-slate-400";
    }
    return (
      <span
        data-testid="live-mode-chip"
        className={`inline-flex items-center rounded-full ${bg} px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-white`}
      >
        {label}
      </span>
    );
  }
  ```

- [ ] **Step 3: Run, confirm pass**

  ```bash
  cd frontend && npx vitest run LiveModeChip
  ```

- [ ] **Step 4: LiveHeaderStrip — test renders 8 KPIs**

  ```tsx
  import { describe, expect, it, vi } from "vitest";
  import { render, screen } from "@testing-library/react";
  import { LiveHeaderStrip } from "@/components/algo-trading/live/LiveHeaderStrip";

  vi.mock("@/hooks/useLiveDashboardSummary", () => ({
    useLiveDashboardSummary: () => ({
      summary: {
        today_pnl_inr: "1240.50",
        open_pnl_inr: "820.30",
        realised_pnl_inr: "420.20",
        cash_inr: "98432.10",
        open_position_count: 3,
        mode: "live",
        ws_age_seconds: 2,
        kill_switch_active: false,
      },
      loading: false,
      error: null,
      refresh: () => {},
    }),
  }));
  vi.mock("@/hooks/useKillSwitch", () => ({
    useKillSwitch: () => ({ state: { active: false } }),
  }));

  describe("LiveHeaderStrip", () => {
    it("renders all 8 KPIs", () => {
      render(<LiveHeaderStrip />);
      expect(screen.getByText(/1,240/)).toBeInTheDocument();
      expect(screen.getByText(/820/)).toBeInTheDocument();
      expect(screen.getByText(/420/)).toBeInTheDocument();
      expect(screen.getByText(/98,432/)).toBeInTheDocument();
      expect(screen.getByTestId("live-mode-chip")).toBeInTheDocument();
      expect(screen.getByTestId("live-ws-age")).toHaveTextContent("2s");
    });
  });
  ```

- [ ] **Step 5: Implement LiveHeaderStrip**

  ```tsx
  "use client";

  import { useLiveDashboardSummary } from "@/hooks/useLiveDashboardSummary";
  import { LiveModeChip } from "./LiveModeChip";

  function inr(value: string | undefined): string {
    if (!value) return "₹0";
    const n = Number(value);
    if (!Number.isFinite(n)) return "₹0";
    return `₹${n.toLocaleString("en-IN", {
      maximumFractionDigits: 0,
    })}`;
  }

  function signed(value: string | undefined): string {
    const n = Number(value ?? 0);
    const prefix = n >= 0 ? "+" : "";
    return `${prefix}${inr(value)}`;
  }

  export function LiveHeaderStrip() {
    const { summary } = useLiveDashboardSummary();
    const armed = (summary?.open_position_count ?? 0) > 0
      || (summary?.kill_switch_active === false
          && Number(summary?.today_pnl_inr ?? 0) !== 0);
    // armed = "the runtime has done something today"; the chip
    // still reads gates.live_orders_enabled elsewhere — this is a
    // header-strip approximation. Replace with useLiveCaps in
    // Task 4.6 once a strategy is picked.

    return (
      <div
        className="sticky top-0 z-10 flex flex-wrap items-center
          gap-3 bg-white/95 dark:bg-slate-900/95 backdrop-blur
          border-b border-slate-200 dark:border-slate-700 px-4 py-3"
        data-testid="live-header-strip"
      >
        <LiveModeChip
          mode={summary?.mode ?? "live"}
          armed={armed}
        />
        <Kpi label="Today P&L" value={signed(summary?.today_pnl_inr)} />
        <Kpi label="Open P&L"  value={signed(summary?.open_pnl_inr)} />
        <Kpi label="Realised"  value={signed(summary?.realised_pnl_inr)} />
        <Kpi label="Cash"      value={inr(summary?.cash_inr)} />
        <Kpi
          label="Open"
          value={String(summary?.open_position_count ?? 0)}
        />
        <div
          className="flex items-center gap-1 text-xs text-slate-500"
          data-testid="live-ws-age"
        >
          WS
          <span className={`h-2 w-2 rounded-full ${
            (summary?.ws_age_seconds ?? 999) < 10
              ? "bg-emerald-500" : "bg-rose-500"
          }`} />
          {summary?.ws_age_seconds != null
            ? `${summary.ws_age_seconds}s`
            : "—"}
        </div>
      </div>
    );
  }

  function Kpi({ label, value }: { label: string; value: string }) {
    return (
      <div className="flex flex-col">
        <span className="text-[10px] uppercase tracking-wide text-slate-500">
          {label}
        </span>
        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          {value}
        </span>
      </div>
    );
  }
  ```

- [ ] **Step 6: Run tests, confirm pass; commit**

  ```bash
  cd frontend && npx vitest run LiveModeChip LiveHeaderStrip
  git add frontend/components/algo-trading/live/LiveModeChip.tsx \
          frontend/components/algo-trading/live/LiveHeaderStrip.tsx \
          frontend/components/algo-trading/__tests__/LiveModeChip.test.tsx \
          frontend/components/algo-trading/__tests__/LiveHeaderStrip.test.tsx
  git commit -m "feat(algo-fe): LiveHeaderStrip + LiveModeChip with KPI strip"
  ```

### Task 4.3 — PositionsTab + HoldingsTab

**Files:**
- Create: `frontend/components/algo-trading/live/PositionsTab.tsx`
- Create: `frontend/components/algo-trading/live/HoldingsTab.tsx`
- Create: `frontend/components/algo-trading/__tests__/PositionsTab.test.tsx`

- [ ] **Step 1: PositionsTab test (TDD)**

  ```tsx
  import { describe, expect, it, vi } from "vitest";
  import { render, screen } from "@testing-library/react";
  import { PositionsTab } from "@/components/algo-trading/live/PositionsTab";

  vi.mock("@/hooks/useLivePositions", () => ({
    useLivePositions: () => ({
      rows: [
        { tradingsymbol: "ITC", exchange: "NSE", quantity: 8,
          average_price: "307.33", last_price: "311.20",
          pnl_inr: "30.96", pnl_pct: "1.26", product: "MIS",
          strategy_id: "v3", strategy_name: "V3 Multi",
          entry_ts_utc: "2026-05-11T04:19:54Z",
          entry_reason: "BULL · momentum_z=1.4" },
        { tradingsymbol: "MANUAL", exchange: "NSE", quantity: 1,
          average_price: "100", last_price: "100", pnl_inr: "0",
          pnl_pct: "0", product: "MIS",
          strategy_id: null, strategy_name: null,
          entry_ts_utc: null, entry_reason: null },
      ],
      ledger_drift: false, loading: false, error: null,
    }),
  }));

  describe("PositionsTab", () => {
    it("renders both rows with strategy + dash fallback", () => {
      render(<PositionsTab />);
      expect(screen.getByText("ITC")).toBeInTheDocument();
      expect(screen.getByText("V3 Multi")).toBeInTheDocument();
      expect(screen.getByText("MANUAL")).toBeInTheDocument();
      // dash placeholders for unattributed manual row
      expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    });
  });
  ```

- [ ] **Step 2: Implement PositionsTab**

  ```tsx
  "use client";

  import { useLivePositions } from "@/hooks/useLivePositions";

  function fmt(v: string | null | undefined, kind: "inr" | "pct" | "qty"): string {
    if (v == null) return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    if (kind === "inr")
      return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
    if (kind === "pct")
      return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
    return String(n);
  }

  export function PositionsTab() {
    const { rows, loading, error } = useLivePositions();
    if (loading) return <p className="text-sm text-slate-500">Loading…</p>;
    if (error)
      return (
        <p className="text-sm text-rose-700" data-testid="positions-error">
          Could not load positions: {String(error)}
        </p>
      );
    if (!rows || rows.length === 0)
      return (
        <p className="text-sm text-slate-500" data-testid="positions-empty">
          No open positions.
        </p>
      );
    return (
      <table
        className="w-full text-sm"
        data-testid="positions-table"
      >
        <thead className="text-xs uppercase text-slate-500 border-b border-slate-200 dark:border-slate-700">
          <tr>
            <th className="px-2 py-2 text-left">Ticker</th>
            <th className="px-2 py-2 text-right">Qty</th>
            <th className="px-2 py-2 text-right">Avg</th>
            <th className="px-2 py-2 text-right">LTP</th>
            <th className="px-2 py-2 text-right">P&L</th>
            <th className="px-2 py-2 text-right">P&L%</th>
            <th className="px-2 py-2 text-left">Strategy</th>
            <th className="px-2 py-2 text-left">Entry</th>
            <th className="px-2 py-2 text-left">Reason</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={`${r.tradingsymbol}-${r.product}`}
              className="border-b border-slate-100 dark:border-slate-800"
            >
              <td className="px-2 py-2 font-medium">{r.tradingsymbol}</td>
              <td className="px-2 py-2 text-right">{r.quantity}</td>
              <td className="px-2 py-2 text-right">{fmt(r.average_price, "inr")}</td>
              <td className="px-2 py-2 text-right">{fmt(r.last_price, "inr")}</td>
              <td className="px-2 py-2 text-right">{fmt(r.pnl_inr, "inr")}</td>
              <td className="px-2 py-2 text-right">{fmt(r.pnl_pct, "pct")}</td>
              <td className="px-2 py-2">{r.strategy_name ?? "—"}</td>
              <td className="px-2 py-2 text-xs text-slate-500">
                {r.entry_ts_utc
                  ? new Date(r.entry_ts_utc).toLocaleTimeString("en-IN")
                  : "—"}
              </td>
              <td className="px-2 py-2 text-xs">{r.entry_reason ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  ```

- [ ] **Step 3: HoldingsTab**

  Create `frontend/components/algo-trading/live/HoldingsTab.tsx`:

  ```tsx
  "use client";

  import { useLiveHoldings } from "@/hooks/useLiveHoldings";

  function fmtInr(v: string | null | undefined): string {
    if (v == null) return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
  }

  function fmtPct(v: string | null | undefined): string {
    if (v == null) return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
  }

  export function HoldingsTab() {
    const { rows, loading, error } = useLiveHoldings();
    if (loading)
      return <p className="text-sm text-slate-500">Loading…</p>;
    if (error)
      return (
        <p
          className="text-sm text-rose-700"
          data-testid="holdings-error"
        >
          Could not load holdings: {String(error)}
        </p>
      );
    if (!rows || rows.length === 0)
      return (
        <p
          className="text-sm text-slate-500"
          data-testid="holdings-empty"
        >
          No holdings.
        </p>
      );
    return (
      <table className="w-full text-sm" data-testid="holdings-table">
        <thead className="text-xs uppercase text-slate-500 border-b border-slate-200 dark:border-slate-700">
          <tr>
            <th className="px-2 py-2 text-left">Ticker</th>
            <th className="px-2 py-2 text-right">Qty</th>
            <th className="px-2 py-2 text-right">Avg</th>
            <th className="px-2 py-2 text-right">LTP</th>
            <th className="px-2 py-2 text-right">P&amp;L</th>
            <th className="px-2 py-2 text-right">P&amp;L%</th>
            <th className="px-2 py-2 text-right">Days</th>
            <th className="px-2 py-2 text-left">Strategy</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.tradingsymbol}
              className="border-b border-slate-100 dark:border-slate-800"
            >
              <td className="px-2 py-2 font-medium">
                {r.tradingsymbol}
              </td>
              <td className="px-2 py-2 text-right">{r.quantity}</td>
              <td className="px-2 py-2 text-right">{fmtInr(r.average_price)}</td>
              <td className="px-2 py-2 text-right">{fmtInr(r.last_price)}</td>
              <td className="px-2 py-2 text-right">{fmtInr(r.pnl_inr)}</td>
              <td className="px-2 py-2 text-right">{fmtPct(r.pnl_pct)}</td>
              <td className="px-2 py-2 text-right">
                {r.days_held != null ? `${r.days_held}d` : "—"}
              </td>
              <td className="px-2 py-2">{r.strategy_name ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  ```

- [ ] **Step 4: Run, confirm tests pass**

  ```bash
  cd frontend && npx vitest run PositionsTab
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add frontend/components/algo-trading/live/PositionsTab.tsx \
          frontend/components/algo-trading/live/HoldingsTab.tsx \
          frontend/components/algo-trading/__tests__/PositionsTab.test.tsx
  git commit -m "feat(algo-fe): PositionsTab + HoldingsTab with attribution join"
  ```

### Task 4.4 — PanicCloseButton (TDD)

**Files:**
- Create: `frontend/components/algo-trading/live/PanicCloseButton.tsx`
- Create: `frontend/components/algo-trading/__tests__/PanicCloseButton.test.tsx`

- [ ] **Step 1: Test the confirm-modal gating**

  ```tsx
  import { describe, expect, it, vi } from "vitest";
  import { fireEvent, render, screen } from "@testing-library/react";
  import { PanicCloseButton } from "@/components/algo-trading/live/PanicCloseButton";

  describe("PanicCloseButton", () => {
    it("opens confirm modal on click", () => {
      render(<PanicCloseButton onConfirm={() => {}} />);
      fireEvent.click(screen.getByTestId("panic-close-button"));
      expect(
        screen.getByText(/close all open positions/i),
      ).toBeInTheDocument();
    });

    it("fires onConfirm only after typing PANIC", () => {
      const onConfirm = vi.fn();
      render(<PanicCloseButton onConfirm={onConfirm} />);
      fireEvent.click(screen.getByTestId("panic-close-button"));
      const confirm = screen.getByTestId("panic-close-confirm");
      expect(confirm).toBeDisabled();
      fireEvent.change(screen.getByTestId("panic-close-input"), {
        target: { value: "PANIC" },
      });
      expect(confirm).toBeEnabled();
      fireEvent.click(confirm);
      expect(onConfirm).toHaveBeenCalledOnce();
    });
  });
  ```

- [ ] **Step 2: Implement**

  ```tsx
  "use client";

  import { useState } from "react";

  interface Props {
    onConfirm: () => Promise<void> | void;
  }

  export function PanicCloseButton({ onConfirm }: Props) {
    const [open, setOpen] = useState(false);
    const [text, setText] = useState("");
    const [busy, setBusy] = useState(false);

    const confirmable = text.trim() === "PANIC";

    async function handle() {
      setBusy(true);
      try {
        await onConfirm();
        setOpen(false);
        setText("");
      } finally {
        setBusy(false);
      }
    }

    return (
      <>
        <button
          type="button"
          data-testid="panic-close-button"
          onClick={() => setOpen(true)}
          className="rounded-md bg-rose-600 px-3 py-1.5 text-xs
            font-semibold text-white hover:bg-rose-700"
        >
          PANIC CLOSE
        </button>
        {open && (
          <div
            className="fixed inset-0 z-[70] flex items-center
              justify-center bg-black/40"
            onClick={() => !busy && setOpen(false)}
          >
            <div
              className="w-[440px] rounded-lg bg-white dark:bg-slate-900
                p-5 shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              <h3 className="text-base font-semibold text-rose-700">
                Close all open positions?
              </h3>
              <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">
                This will submit market-close orders for every
                algo-opened position via Kite. Type{" "}
                <code className="font-mono">PANIC</code> to confirm.
              </p>
              <input
                data-testid="panic-close-input"
                value={text}
                onChange={(e) => setText(e.target.value)}
                className="mt-3 w-full rounded border border-slate-300
                  dark:border-slate-600 bg-white dark:bg-slate-800
                  px-2 py-1 text-sm"
                placeholder="Type PANIC"
                autoFocus
              />
              <div className="mt-4 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  disabled={busy}
                  className="rounded-md px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  data-testid="panic-close-confirm"
                  onClick={handle}
                  disabled={!confirmable || busy}
                  className="rounded-md bg-rose-600 px-3 py-1.5 text-xs
                    font-semibold text-white hover:bg-rose-700
                    disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {busy ? "Closing…" : "Close all"}
                </button>
              </div>
            </div>
          </div>
        )}
      </>
    );
  }
  ```

- [ ] **Step 3: Run, pass, commit**

  ```bash
  cd frontend && npx vitest run PanicCloseButton
  git add frontend/components/algo-trading/live/PanicCloseButton.tsx \
          frontend/components/algo-trading/__tests__/PanicCloseButton.test.tsx
  git commit -m "feat(algo-fe): PanicCloseButton with PANIC confirm gate"
  ```

### Task 4.5 — OpenPositionsWidget + RecentFillsTape

Both are thin wrappers over existing components:

**Files:**
- Create: `frontend/components/algo-trading/live/OpenPositionsWidget.tsx`
- Create: `frontend/components/algo-trading/live/RecentFillsTape.tsx`

- [ ] **Step 1: OpenPositionsWidget**

  ```tsx
  "use client";

  import { useLivePositions } from "@/hooks/useLivePositions";

  export function OpenPositionsWidget() {
    const { rows, loading } = useLivePositions();
    const visible = (rows ?? []).slice(0, 5);
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-3"
        data-testid="open-positions-widget"
      >
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Open Positions
          </h3>
          <span className="text-xs text-slate-400">
            {rows?.length ?? 0}
          </span>
        </div>
        {loading && (
          <p className="mt-2 text-xs text-slate-400">Loading…</p>
        )}
        {!loading && visible.length === 0 && (
          <p className="mt-2 text-xs text-slate-400">No open positions.</p>
        )}
        <ul className="mt-2 space-y-1 text-sm">
          {visible.map((r) => (
            <li
              key={`${r.tradingsymbol}-${r.product}`}
              className="flex justify-between"
            >
              <span className="font-medium">{r.tradingsymbol}</span>
              <span className="tabular-nums text-slate-600 dark:text-slate-400">
                {r.quantity} · ₹{Number(r.last_price).toFixed(2)} ·
                <span className={
                  Number(r.pnl_pct) >= 0
                    ? "text-emerald-600 ml-1"
                    : "text-rose-600 ml-1"
                }>
                  {Number(r.pnl_pct) >= 0 ? "+" : ""}
                  {Number(r.pnl_pct).toFixed(2)}%
                </span>
              </span>
            </li>
          ))}
        </ul>
        {(rows?.length ?? 0) > 5 && (
          <p className="mt-2 text-xs text-slate-400">
            +{(rows!.length - 5)} more — see Positions tab.
          </p>
        )}
      </div>
    );
  }
  ```

- [ ] **Step 2: RecentFillsTape**

  ```tsx
  "use client";

  import { usePaperEvents } from "@/hooks/usePaperEvents";

  /**
   * Footer-zone tape: latest 20 live fills (real money, not dry-run).
   */
  export function RecentFillsTape() {
    const { events } = usePaperEvents(20, 0, "live", false);
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-3"
        data-testid="recent-fills-tape"
      >
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Recent Fills
        </h3>
        {events.length === 0 && (
          <p className="mt-2 text-xs text-slate-400">No fills yet today.</p>
        )}
        <ul className="mt-2 max-h-48 overflow-y-auto space-y-1 text-xs font-mono">
          {events
            .filter((e) => e.event_type === "order_filled")
            .map((e) => (
              <li key={e.id} className="text-slate-700 dark:text-slate-300">
                {new Date(e.ts).toLocaleTimeString("en-IN")} ·{" "}
                {e.payload?.side} {e.payload?.quantity}{" "}
                {e.payload?.tradingsymbol} @ ₹{e.payload?.price}
              </li>
            ))}
        </ul>
      </div>
    );
  }
  ```

  Verify `usePaperEvents` signature matches `(limit, offset, mode,
  dryRun)` by grepping its definition:

  ```bash
  grep -n "export function usePaperEvents\|EventsMode" \
    frontend/hooks/usePaperEvents.ts | head
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add frontend/components/algo-trading/live/OpenPositionsWidget.tsx \
          frontend/components/algo-trading/live/RecentFillsTape.tsx
  git commit -m "feat(algo-fe): OpenPositionsWidget + RecentFillsTape"
  ```

### Task 4.6 — LiveDashboard composition

**Files:**
- Create: `frontend/components/algo-trading/live/LiveDashboard.tsx`

The Live tab body. Sticky header (already produced by
`LiveHeaderStrip`) + 4-zone 2×2 grid (lg+) + 1-column stack (< lg).

- [ ] **Step 1: Write the file**

  ```tsx
  "use client";

  import { useState } from "react";
  import useSWR from "swr";

  import { apiFetch } from "@/lib/apiFetch";
  import { API_URL } from "@/lib/config";

  import { AttributionPanel } from "../AttributionPanel";
  import { LiveSafetyBeltsForm } from "../LiveSafetyBeltsForm";
  import { RegimeHistoryChart } from "../RegimeHistoryChart";
  import { RegimeWidget } from "../RegimeWidget";
  import { useStrategies } from "@/hooks/useStrategies";

  import { OpenPositionsWidget } from "./OpenPositionsWidget";
  import { PanicCloseButton } from "./PanicCloseButton";
  import { RecentFillsTape } from "./RecentFillsTape";

  async function panicClose(): Promise<void> {
    await apiFetch(`${API_URL}/algo/kill-switch/panic-close`, {
      method: "POST",
    });
  }

  export function LiveDashboard() {
    const { strategies } = useStrategies();
    const [strategyId, setStrategyId] = useState<string>(
      () => strategies[0]?.id ?? "",
    );

    // Defensive dry-run banner — should never show on this page, but
    // if gates.dry_run is true the user has misconfigured Settings.
    const { data: dryRunData } = useSWR<{ armed: boolean }>(
      `${API_URL}/algo/live/dry-run`,
      async (url) => {
        const r = await apiFetch(url);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      },
      { refreshInterval: 30_000, revalidateOnFocus: false },
    );

    return (
      <div className="space-y-3" data-testid="live-dashboard">
        {dryRunData?.armed && (
          <div
            className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800"
            data-testid="live-dryrun-warning"
          >
            Dry-run is armed. You are on the Live page; this banner
            means the runtime is in rehearsal mode. Disarm dry-run
            in Live → Settings to send real orders.
          </div>
        )}

        <div className="flex items-center justify-between gap-3">
          <select
            className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm w-64"
            value={strategyId}
            onChange={(e) => setStrategyId(e.target.value)}
            data-testid="live-strategy-select"
          >
            <option value="">Select strategy…</option>
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          <div className="flex items-center gap-2">
            <RegimeWidget />
            <PanicCloseButton onConfirm={panicClose} />
          </div>
        </div>

        {/* 4-zone grid */}
        <div className="grid gap-3 lg:grid-cols-2">
          {/* Zone A — Open positions */}
          <OpenPositionsWidget />

          {/* Zone B — Regime + stress mini chart */}
          <div
            className="rounded-md border border-slate-200 dark:border-slate-700 p-3"
            data-testid="live-zone-b-regime"
          >
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Regime &amp; Stress
            </h3>
            <RegimeHistoryChart />
          </div>

          {/* Zone C — Active strategy + safety belts compact */}
          <div
            className="rounded-md border border-slate-200 dark:border-slate-700 p-3"
            data-testid="live-zone-c-strategy"
          >
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Active Strategy
            </h3>
            {strategyId ? (
              <LiveSafetyBeltsForm strategyId={strategyId} />
            ) : (
              <p className="mt-2 text-xs text-slate-400">
                Pick a strategy above to see safety belts.
              </p>
            )}
          </div>

          {/* Zone D — Recent fills */}
          <RecentFillsTape />
        </div>

        {/* Footer — collapsed by default */}
        <details
          className="rounded-md border border-slate-200 dark:border-slate-700"
          data-testid="live-attribution-details"
        >
          <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Attribution
          </summary>
          <div className="border-t border-slate-200 dark:border-slate-700 p-3">
            <AttributionPanel strategyId={strategyId || null} />
          </div>
        </details>
      </div>
    );
  }
  ```

- [ ] **Step 2: Type-check, commit**

  ```bash
  cd frontend && npx tsc --noEmit
  git add frontend/components/algo-trading/live/LiveDashboard.tsx
  git commit -m "feat(algo-fe): LiveDashboard 4-zone grid composition"
  ```

### Task 4.7 — Wire LiveClient with tabs

**Files:**
- Modify: `frontend/app/(authenticated)/algo-trading/live/LiveClient.tsx`

- [ ] **Step 1: Replace the placeholder body**

  ```tsx
  "use client";

  import { useCallback, useMemo } from "react";
  import { useRouter, useSearchParams } from "next/navigation";

  import {
    LIVE_TAB_LABELS,
    LIVE_TAB_ORDER,
    type LiveTabId,
  } from "@/lib/types/algoTrading";

  import { HoldingsTab } from "@/components/algo-trading/live/HoldingsTab";
  import { LiveDashboard } from "@/components/algo-trading/live/LiveDashboard";
  import { LiveHeaderStrip } from "@/components/algo-trading/live/LiveHeaderStrip";
  import { LiveSettingsTab } from "@/components/algo-trading/live/LiveSettingsTab";
  import { PositionsTab } from "@/components/algo-trading/live/PositionsTab";

  const DEFAULT_TAB: LiveTabId = "live";

  function isValid(v: string | null): v is LiveTabId {
    return v !== null && (LIVE_TAB_ORDER as readonly string[]).includes(v);
  }

  export default function LiveClient() {
    const router = useRouter();
    const sp = useSearchParams();
    const raw = sp.get("tab");
    const active: LiveTabId = isValid(raw) ? raw : DEFAULT_TAB;

    const switchTo = useCallback(
      (next: LiveTabId) => {
        const params = new URLSearchParams(sp.toString());
        params.set("tab", next);
        router.replace(`/algo-trading/live?${params.toString()}`, {
          scroll: false,
        });
      },
      [router, sp],
    );

    const panel = useMemo(() => {
      switch (active) {
        case "live":      return <LiveDashboard />;
        case "positions": return <PositionsTab />;
        case "holdings":  return <HoldingsTab />;
        case "settings":  return <LiveSettingsTab />;
      }
    }, [active]);

    return (
      <div className="flex flex-col">
        <LiveHeaderStrip />
        <div className="p-4 space-y-3">
          <div
            role="tablist"
            data-testid="algo-live-tabs"
            className="flex flex-wrap items-center gap-1 border-b
              border-gray-200 dark:border-gray-700"
          >
            {LIVE_TAB_ORDER.map((id) => (
              <button
                key={id}
                role="tab"
                aria-selected={id === active}
                data-testid={`algo-live-tab-${id}`}
                onClick={() => switchTo(id)}
                className={`px-3 py-2 text-sm transition-colors ${
                  id === active
                    ? "border-b-2 border-rose-500 text-rose-600 dark:text-rose-400 font-medium"
                    : "text-gray-600 dark:text-gray-300 hover:text-rose-600 dark:hover:text-rose-400"
                }`}
              >
                {LIVE_TAB_LABELS[id]}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            data-testid={`algo-live-panel-${active}`}
            className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4"
          >
            {panel}
          </div>
        </div>
      </div>
    );
  }
  ```

  `LiveSettingsTab` doesn't exist yet — its skeleton is in Task 5.1.
  To keep this slice green, create a minimal placeholder
  `LiveSettingsTab.tsx` for now:

  ```tsx
  "use client";
  export function LiveSettingsTab() {
    return <p className="text-sm">Live Settings — wired up in Slice 5.</p>;
  }
  ```

  Task 5.1 will replace the body.

- [ ] **Step 2: Type-check + commit**

  ```bash
  cd frontend && npx tsc --noEmit
  git add frontend/app/\(authenticated\)/algo-trading/live/LiveClient.tsx \
          frontend/components/algo-trading/live/LiveSettingsTab.tsx
  git commit -m "feat(algo-fe): LiveClient page shell + 4 tabs"
  ```

### Task 4.8 — Manual smoke + PR

- [ ] **Step 1: Restart and visually verify**

  ```bash
  ./run.sh restart frontend
  ```
  Browse to `/algo-trading/live`:
  - Header strip sticky on scroll
  - 4-zone grid visible above the fold on 1280×800
  - Click Positions / Holdings tabs — tables render
  - Click Live tab → Strategy picker → safety belts populate
  - Click PANIC CLOSE → modal opens, button stays disabled until
    you type PANIC

- [ ] **Step 2: PR**

  Slice 4 PR titled `feat(algo-fe): Live Trading dashboard +
  Positions + Holdings tabs (slice 4/6)`.

---

## Slice 5 — Live Settings tab + AlgoTradingClient cleanup (½ day, 1 PR)

### Task 5.1 — Move KillSwitch / Drift / LiveModeToggle to LiveSettingsTab

**Files:**
- Modify: `frontend/components/algo-trading/live/LiveSettingsTab.tsx`
- Delete: `frontend/components/algo-trading/SettingsTab.tsx`

- [ ] **Step 1: Replace LiveSettingsTab placeholder**

  ```tsx
  "use client";

  import { useEffect, useState } from "react";
  import useSWR from "swr";

  import { apiFetch } from "@/lib/apiFetch";
  import { API_URL } from "@/lib/config";
  import { useStrategies } from "@/hooks/useStrategies";

  import { KillSwitchToggle } from "../KillSwitchToggle";
  import { LiveModeToggle } from "../LiveModeToggle";
  import { LiveSafetyBeltsForm } from "../LiveSafetyBeltsForm";

  async function fetchThreshold(url: string): Promise<{ threshold_shares: number }> {
    const r = await apiFetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  function DriftThresholdInput() {
    const { data, mutate } = useSWR(
      `${API_URL}/algo/drift/threshold`,
      fetchThreshold,
      { revalidateOnFocus: false },
    );
    const [value, setValue] = useState<number>(0);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
      if (data !== undefined) setValue(data.threshold_shares);
    }, [data]);

    async function save() {
      setSaving(true);
      setSaved(false);
      try {
        const r = await apiFetch(`${API_URL}/algo/drift/threshold`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ threshold_shares: value }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        await mutate({ threshold_shares: value }, false);
        setSaved(true);
      } finally {
        setSaving(false);
      }
    }

    return (
      <div
        className="rounded-md border border-slate-200 p-4 dark:border-slate-700"
        data-testid="drift-threshold-widget"
      >
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Drift threshold
        </h3>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Minimum share discrepancy to flag as a position drift. 0
          means any non-zero difference triggers an alert.
        </p>
        <div className="mt-3 flex items-center gap-3">
          <input
            type="number"
            min={0}
            value={value}
            onChange={(e) => setValue(Math.max(0, Number(e.target.value)))}
            className="w-24 rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
            data-testid="drift-threshold-input"
          />
          <span className="text-xs text-slate-500">shares</span>
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="rounded-md bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            data-testid="drift-threshold-save"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          {saved && (
            <span className="text-xs text-green-600 dark:text-green-400">
              Saved
            </span>
          )}
        </div>
      </div>
    );
  }

  export function LiveSettingsTab() {
    const { strategies } = useStrategies();
    const [strategyId, setStrategyId] = useState<string>("");
    const selected = strategies.find((s) => s.id === strategyId);

    return (
      <div className="space-y-4" data-testid="live-settings-tab">
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          Live Settings
        </h2>

        <KillSwitchToggle />
        <DriftThresholdInput />

        <div className="rounded-md border border-slate-200 dark:border-slate-700 p-4">
          <h3 className="text-sm font-semibold">Per-strategy live arming</h3>
          <p className="mt-1 text-xs text-slate-500">
            Use the 4-gate toggle below to enable live order placement
            for a strategy. All four gates must pass server-side; the
            toggle is a convenience.
          </p>
          <label className="mt-3 flex flex-col gap-0.5">
            <span className="text-[11px] text-slate-500">Strategy</span>
            <select
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm w-64"
              data-testid="live-settings-strategy-select"
            >
              <option value="">Select strategy…</option>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </label>

          {strategyId && selected && (
            <div className="mt-3 space-y-3">
              <LiveModeToggle
                strategyId={strategyId}
                strategyName={selected.name}
              />
              <LiveSafetyBeltsForm strategyId={strategyId} />
            </div>
          )}
        </div>
      </div>
    );
  }
  ```

- [ ] **Step 2: Delete the old SettingsTab.tsx**

  ```bash
  rm frontend/components/algo-trading/SettingsTab.tsx
  ```

- [ ] **Step 3: Confirm no orphaned imports**

  ```bash
  grep -rn "from .*SettingsTab\"" frontend/ --include="*.tsx"
  ```
  Expected: empty.

- [ ] **Step 4: Type-check, commit**

  ```bash
  cd frontend && npx tsc --noEmit
  git add frontend/components/algo-trading/live/LiveSettingsTab.tsx
  git rm frontend/components/algo-trading/SettingsTab.tsx
  git commit -m "feat(algo-fe): LiveSettingsTab takes KillSwitch + Drift + LiveModeToggle"
  ```

### Task 5.2 — Delete the orphaned AlgoTradingClient

**Files:**
- Delete: `frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx`

- [ ] **Step 1: Confirm zero references**

  ```bash
  grep -rn "AlgoTradingClient" frontend/ --include="*.tsx" --include="*.ts"
  ```
  Expected: empty (the only consumer was rewritten in Task 1.4).

- [ ] **Step 2: Delete + commit**

  ```bash
  git rm frontend/app/\(authenticated\)/algo-trading/AlgoTradingClient.tsx
  git commit -m "chore(algo-fe): drop orphaned AlgoTradingClient"
  ```

### Task 5.3 — PR

- [ ] **Step 1: PR titled `feat(algo-fe): Live Settings + cleanup (slice 5/6)`.**

---

## Slice 6 — E2E + docs (½ day, 1 PR)

### Task 6.1 — Selectors registry

**Files:**
- Modify: `e2e/utils/selectors.ts`

- [ ] **Step 1: Append the new testids to the `FE` object**

  ```ts
  // Algo Trading — three-page split
  algoSidebarGroup: "sidebar-group-algo-trading",
  algoBrokerLink:   "sidebar-child-zerodha-connect",
  algoStrategiesLink: "sidebar-child-strategies",
  algoLiveLink:     "sidebar-child-live-trading",

  algoBrokerPage:   "algo-broker-page",
  algoStrategiesHeading: "algo-strategies-heading",
  algoStrategiesTab: (id: string) => `algo-strategies-tab-${id}`,
  algoStrategiesPanel: (id: string) => `algo-strategies-panel-${id}`,

  algoLivePage:     "algo-live-page",
  algoLiveTab:      (id: string) => `algo-live-tab-${id}`,
  algoLivePanel:    (id: string) => `algo-live-panel-${id}`,
  algoLiveHeader:   "live-header-strip",
  algoLiveModeChip: "live-mode-chip",
  algoLiveDashboard: "live-dashboard",
  algoPanicButton:  "panic-close-button",
  algoPanicInput:   "panic-close-input",
  algoPanicConfirm: "panic-close-confirm",
  algoPositionsTable: "positions-table",
  algoPositionsEmpty: "positions-empty",

  algoDryRunTab:    "dryrun-tab",
  algoDryRunBanner: "dryrun-arm-banner",
  algoDryRunArmBtn: "dryrun-arm-button",
  algoPaperTab:     "paper-tab",
  ```

### Task 6.2 — E2E specs

**Files:**
- Create: `e2e/specs/algo-sidebar-group.spec.ts`
- Create: `e2e/specs/algo-broker-page.spec.ts`
- Create: `e2e/specs/algo-strategies-tabs.spec.ts`
- Create: `e2e/specs/algo-live-dashboard.spec.ts`
- Create: `e2e/specs/algo-live-positions.spec.ts`

Each spec follows the existing POM pattern (see
`e2e/pages/frontend/`). Concrete content for sidebar-group spec:

- [ ] **Step 1: Sidebar group spec**

  ```ts
  import { test, expect } from "../fixtures/portfolio.fixture";
  import { FE } from "../utils/selectors";

  test.describe("Algo Trading sidebar group", () => {
    test("expands and lands on each child", async ({ page }) => {
      await page.goto("/dashboard");
      await page.getByTestId(FE.algoSidebarGroup).click();
      await expect(page.getByTestId(FE.algoBrokerLink)).toBeVisible();
      await expect(page.getByTestId(FE.algoStrategiesLink)).toBeVisible();
      await expect(page.getByTestId(FE.algoLiveLink)).toBeVisible();

      await page.getByTestId(FE.algoBrokerLink).click();
      await expect(page).toHaveURL(/\/algo-trading\/broker/);
      await expect(page.getByTestId(FE.algoBrokerPage)).toBeVisible();

      await page.getByTestId(FE.algoStrategiesLink).click();
      await expect(page).toHaveURL(/\/algo-trading\/strategies/);

      await page.getByTestId(FE.algoLiveLink).click();
      await expect(page).toHaveURL(/\/algo-trading\/live/);
      await expect(page.getByTestId(FE.algoLiveHeader)).toBeVisible();
    });

    test("legacy /algo-trading?tab=settings redirects to strategies", async ({ page }) => {
      await page.goto("/algo-trading?tab=settings");
      await expect(page).toHaveURL(
        /\/algo-trading\/strategies\?tab=settings/,
      );
    });
  });
  ```

- [ ] **Step 2: Strategies-tabs spec**

  ```ts
  import { test, expect } from "../fixtures/portfolio.fixture";
  import { FE } from "../utils/selectors";

  const TABS = [
    "instruments", "strategies", "backtest", "paper",
    "dryrun", "performance", "replay", "settings",
  ];

  test.describe("Strategies tabs", () => {
    test("all 8 tabs reachable, URL syncs", async ({ page }) => {
      await page.goto("/algo-trading/strategies");
      for (const id of TABS) {
        await page.getByTestId(FE.algoStrategiesTab(id)).click();
        await expect(page).toHaveURL(new RegExp(`tab=${id}`));
        await expect(
          page.getByTestId(FE.algoStrategiesPanel(id)),
        ).toBeVisible();
      }
    });

    test("Dry run tab shows amber arm banner", async ({ page }) => {
      await page.goto("/algo-trading/strategies?tab=dryrun");
      await expect(page.getByTestId(FE.algoDryRunBanner)).toBeVisible();
      await expect(page.getByTestId(FE.algoDryRunArmBtn)).toBeVisible();
    });
  });
  ```

- [ ] **Step 3: Live-dashboard spec**

  ```ts
  import { test, expect } from "../fixtures/portfolio.fixture";
  import { FE } from "../utils/selectors";

  test.describe("Live Trading dashboard", () => {
    test("header + 4 zones render above the fold", async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto("/algo-trading/live");
      await expect(page.getByTestId(FE.algoLiveHeader)).toBeVisible();
      await expect(page.getByTestId(FE.algoLiveDashboard)).toBeVisible();
      await expect(page.getByTestId("open-positions-widget")).toBeVisible();
      await expect(page.getByTestId("live-zone-b-regime")).toBeVisible();
      await expect(page.getByTestId("live-zone-c-strategy")).toBeVisible();
      await expect(page.getByTestId("recent-fills-tape")).toBeVisible();
    });

    test("panic close gated behind PANIC text", async ({ page }) => {
      await page.goto("/algo-trading/live");
      await page.getByTestId(FE.algoPanicButton).click();
      await expect(page.getByTestId(FE.algoPanicConfirm)).toBeDisabled();
      await page.getByTestId(FE.algoPanicInput).fill("PANIC");
      await expect(page.getByTestId(FE.algoPanicConfirm)).toBeEnabled();
    });
  });
  ```

- [ ] **Step 4: Live-positions spec (mocked backend)**

  Create `e2e/specs/algo-live-positions.spec.ts`:

  ```ts
  import { test, expect } from "../fixtures/portfolio.fixture";
  import { FE } from "../utils/selectors";

  test.describe("Live Positions tab", () => {
    test("renders mocked rows with strategy attribution", async ({ page }) => {
      await page.route("**/v1/algo/live/positions", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ledger_drift: false,
            rows: [
              {
                tradingsymbol: "ITC", exchange: "NSE", quantity: 8,
                average_price: "307.33", last_price: "311.20",
                pnl_inr: "30.96", pnl_pct: "1.26", product: "MIS",
                strategy_id: "v3", strategy_name: "V3 Multi",
                entry_ts_utc: "2026-05-11T04:19:54Z",
                entry_reason: "BULL · momentum_z=1.4",
              },
              {
                tradingsymbol: "MANUAL", exchange: "NSE", quantity: 1,
                average_price: "100", last_price: "100",
                pnl_inr: "0", pnl_pct: "0", product: "MIS",
                strategy_id: null, strategy_name: null,
                entry_ts_utc: null, entry_reason: null,
              },
            ],
          }),
        }),
      );

      await page.goto("/algo-trading/live?tab=positions");
      await expect(page.getByTestId(FE.algoPositionsTable)).toBeVisible();
      await expect(page.getByText("ITC")).toBeVisible();
      await expect(page.getByText("V3 Multi")).toBeVisible();
      await expect(page.getByText("MANUAL")).toBeVisible();
    });
  });
  ```

- [ ] **Step 5: Broker-page spec**

  Create `e2e/specs/algo-broker-page.spec.ts`:

  ```ts
  import { test, expect } from "../fixtures/portfolio.fixture";
  import { FE } from "../utils/selectors";

  test.describe("Zerodha Connect page", () => {
    test("renders broker page heading", async ({ page }) => {
      await page.goto("/algo-trading/broker");
      await expect(page.getByTestId(FE.algoBrokerPage)).toBeVisible();
      await expect(
        page.getByRole("heading", { name: /zerodha connect/i }),
      ).toBeVisible();
    });
  });
  ```

- [ ] **Step 6: Run the full algo E2E**

  ```bash
  cd e2e && npx playwright test \
    algo-sidebar-group algo-broker-page algo-strategies-tabs \
    algo-live-dashboard algo-live-positions \
    --project=frontend-chromium --workers=1
  ```
  Expected: all pass. Use `--update-snapshots` if any visual
  snapshots drift.

- [ ] **Step 7: Commit**

  ```bash
  git add e2e/utils/selectors.ts e2e/specs/algo-*.spec.ts
  git commit -m "test(algo-e2e): five specs for the three-page split"
  ```

### Task 6.3 — Docs

**Files:**
- Create: `docs/algo-trading/page-structure.md`
- Modify: `PROGRESS.md`

- [ ] **Step 1: Write page-structure.md**

  Short doc (≈80 lines) listing the three pages, their URLs, what
  lives where, the redirect table from spec § 6.2, and the rose
  vs amber vs indigo color contract.

- [ ] **Step 2: Update PROGRESS.md**

  Append a dated entry:

  ```markdown
  ## 2026-05-11 — Algo Trading three-page split

  Restructured `/algo-trading` into sidebar group with three pages:
  Zerodha Connect, Strategies (8 tabs), Live Trading (4 tabs).
  Paper + Dry-run are sibling tabs on Strategies — the in-page mode
  toggle (and its hidden Redis state flip) is gone. Live Trading
  page is real-money only with a 4-zone dashboard, dedicated
  Positions and Holdings tabs, and a PANIC CLOSE button gated
  behind a typed-confirm modal. Backend gained
  `/algo/live/dashboard-summary`, `/positions`, `/holdings`.
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add docs/algo-trading/page-structure.md PROGRESS.md
  git commit -m "docs(algo): three-page split structure + PROGRESS entry"
  ```

- [ ] **Step 4: PR — and final dev merge**

  Slice 6 PR titled `test(algo): E2E + docs for three-page split
  (slice 6/6)`. After all 6 PRs merge to `dev` via
  `gh pr merge <n> --squash` per CLAUDE.md § 4.4 #26, the feature
  branch can be deleted.

---

## Spec coverage check

| Spec section | Implemented by |
|---|---|
| § 3.1 sidebar group | Task 1.1 |
| § 3.2 page table (3 pages) | Tasks 1.2 (Broker), 2.2 (Strategies), 4.7 (Live) |
| § 4.1 4-zone grid | Task 4.6 |
| § 4.2 header strip composition | Task 4.2 |
| § 4.3 four Live tabs | Task 4.7 |
| § 5.1 dashboard-summary endpoint | Task 3.2 |
| § 5.2 positions + holdings endpoints | Tasks 3.3, 3.4 |
| § 5.3 settings split | Tasks 2.3 (Strategies), 5.1 (Live) |
| § 6.1 file map | Tasks 1.2–1.3, 2.4–2.5, 4.2–4.7, 5.1 |
| § 6.2 redirects | Task 1.4 |
| § 6.3 data hooks | Task 4.1 |
| § 6.4 visual / theming | Task 4.7 (rose accent on Live tabs), Task 2.5 (amber on Dry-run) |
| § 6.5 mobile | grid uses `lg:grid-cols-2` — collapses to single column < lg, covered by Task 4.6 |
| § 7.1 slicing | Slices 1–6 above |
| § 8.1 unit tests | Tasks 1.4, 2.5, 4.2, 4.3, 4.4 |
| § 8.2 E2E tests | Slice 6 |
| § 8.3 performance budgets | verified manually post Slice 4; not a task but called out |
