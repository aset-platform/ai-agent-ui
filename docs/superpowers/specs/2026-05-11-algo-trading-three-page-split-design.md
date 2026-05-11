# Algo Trading — Three-Page Split (Connect / Strategies / Live)

**Date:** 2026-05-11
**Status:** Spec / design — pending writing-plans
**Author:** Abhay (driver), Claude (drafting)
**Supersedes UI of:** `2026-05-08-algo-trading-platform-design.md` § tab strip,
`2026-05-09-algo-trading-v2-design.md` § live trading surface.

---

## 1. Problem

Today `/algo-trading` is a single page with eight peer tabs:
`Connect · Instruments · Strategies · Backtest · Trading · Performance · Replay · Settings`.

The `Trading` tab (`components/algo-trading/PaperTab.tsx`, 387 LOC) hosts
**Live + Paper + Dry-run** behind an internal 3-segment toggle. Five
observed problems:

1. **Configuration bleed.** Switching the in-page segment touches a
   per-user Redis dry-run flag (`/algo/live/dry-run/arm|disarm`). A
   user who lands on the page in `live` and toggles to `dryrun` to
   inspect a fixture has just *armed* dry-run on the backend. Reload
   keeps the flag where they left it. Real-money runs and synthetic
   runs share the same surface.
2. **Long scroll.** Live view stacks: DryRunBanner → KillBanner →
   ModeToggle (4 gates) → SafetyBeltsForm → InFlightOrders →
   ActiveRunsPanel → KitePostbackPanel → AttributionPanel →
   RegimeHistoryChart → PaperSessionSummary → EventsTimeline. Above
   the fold a user sees a banner and a strategy dropdown — nothing
   actionable.
3. **No positions / holdings surface.** Kite REST exposes
   `kc.positions()` and `kc.holdings()` (used by
   `kill_switch.py:225-236`) but no route or page consumes them. The
   user cannot see "what am I holding right now."
4. **Settings ambiguity.** The Settings tab today is *Kill Switch +
   Drift Threshold + Fee Preview* — all live-relevant except Fee
   Preview which is also used by Backtest. The page name promises
   global settings; the content is mostly live-trading risk knobs.
5. **Performance/Replay scope mismatch.** Performance and Replay
   today read paper events; "Replay" is paper-fixture-driven. Their
   home is naturally next to Backtest + Paper, not next to
   real-money Live.

## 2. Goals

- **Hard separation between Live and Paper/Dry-run.** Different URL,
  different page mount, no shared toggle state, no chance of a
  cross-mode click. Different visual tone (Live = rose accents and
  monospace tape; Paper = indigo, dev-friendly).
- **Trading-desk feel for Live.** Above-fold KPI strip + four-zone
  grid; no scrolling for the primary trading task during market
  hours. Positions and Holdings get their own tabs with strategy
  attribution joined in.
- **Three-section sidebar group** under "Algo Trading":
  `Zerodha Connect`, `Strategies`, `Live Trading` — reusing the
  existing collapsible-group pattern already shipped for the
  Dashboard group.
- **Domain-split Settings.** Backtest/Paper settings (Fee Preview,
  slippage) live under Strategies; live-risk settings (Kill Switch,
  Drift Threshold, default Safety Belts) live under Live Trading.
- **No backend route renames.** `/algo/*` paths stay; the split is
  purely a frontend route + sidebar restructure with three small
  backend additions (positions, holdings, dashboard summary).

**Three distinct mode surfaces** — each at its own URL, each with its
own visual tone, none sharing state with another:

| Mode | URL | Tone | Runtime | Order flow |
|---|---|---|---|---|
| Paper | `/algo-trading/strategies?tab=paper` | indigo | PaperRuntime | synthetic broker |
| Dry run | `/algo-trading/strategies?tab=dryrun` | amber | LiveRuntime | Kite calls intercepted, synthetic fills |
| Live | `/algo-trading/live` | rose | LiveRuntime | real Kite orders |

### Non-goals

- Rebuilding Strategy Builder, Backtest, or Performance tab internals.
- New trading-engine features; this is a UX restructure on top of v3.
- Mobile-first redesign. We keep responsive (single-column collapse
  on < md) but optimize the desktop trading-desk layout first.
- New broker integration; Kite stays the only real-money venue.

## 3. Information architecture

### 3.1 Sidebar (`frontend/lib/constants.tsx`)

The existing `algo-trading` top-level entry becomes a collapsible
group, identical pattern to the Dashboard group at the top of
`NAV_ITEMS`. `requiresAlgoTrading` gate inherits to all children.

```
Algo Trading ▾                       (parent href = /algo-trading,
  Zerodha Connect    /algo-trading/broker         server-side
  Strategies         /algo-trading/strategies     redirects to
  Live Trading       /algo-trading/live           /algo-trading/
                                                  strategies — § 6.2)
```

Collapsed sidebar uses the flyout pattern already implemented at
`Sidebar.tsx:384-481`. Active-state highlighting per-child via
`pathname === item.href`.

The legacy `/algo-trading` and `/algo-trading?tab=*` URLs redirect
to their new homes (table in § 6.2) so existing bookmarks survive.

### 3.2 Three pages

| Page | Route | Tabs (URL-synced `?tab=`) |
|---|---|---|
| Zerodha Connect | `/algo-trading/broker` | *(no tabs — single page)* |
| Strategies | `/algo-trading/strategies` | Instruments · Strategies · Backtest · Paper · Dry run · Performance · Replay · Settings |
| Live Trading | `/algo-trading/live` | Live · Positions · Holdings · Settings |

**Zerodha Connect** is intentionally tab-less. It hosts only
`ConnectBrokerTab.tsx` content (login URL, callback handler, status
chip). One purpose, one screen, no clutter.

**Strategies** = today's tabs minus Connect, **plus a new top-level
`Dry run` tab**. The existing "Trading" tab (`PaperTab.tsx`) is
split: its Paper branch becomes the `Paper` tab (replay fixtures,
indigo); its Dry-run branch becomes the `Dry run` tab (live runtime
with synthetic Kite, amber). Live branch is removed entirely from
this page. There is no in-page Paper/Dry-run sub-toggle anywhere
on the Strategies page — switching modes means switching tabs (and
thus URL), which is itself the safety contract.

**Live Trading** is the new headline page (§ 4). It is **real-money
only** — no Dry-run state, no segment toggle.

## 4. Live Trading dashboard

### 4.1 Layout — dense 4-zone grid

```
┌──────────────────────────────────────────────────────────────────────┐
│ HEADER STRIP  (sticky, h≈56px)                                       │
│ Mode chip · Strategy picker · Kite WS dot · Today P&L · Open P&L     │
│   · Realised · Cash · Kill-switch button · Panic-close button        │
├───────────────────────────────────┬──────────────────────────────────┤
│ ZONE A — Open Positions (live)    │ ZONE B — Regime + Stress         │
│ Compact table, max 5 rows visible │ Regime chip, stress mini-chart,  │
│ Click row → drill via slideover   │ last switch ts, drift chip       │
├───────────────────────────────────┼──────────────────────────────────┤
│ ZONE C — Active Strategy controls │ ZONE D — Recent Fills tape       │
│ Strategy name · caps used bar     │ Reverse-chrono, 20 rows max      │
│ Safety belt toggles (compact)     │ click-through to event detail    │
│ "Edit caps" → slideover           │ pause / resume tape button       │
├───────────────────────────────────┴──────────────────────────────────┤
│ FOOTER: AttributionPanel (collapsed by default — click to expand)    │
└──────────────────────────────────────────────────────────────────────┘
```

Hard rules:

- **No scrolling for header + zones A–D on a 1280×800 viewport.**
  Footer (Attribution) is collapsed by default; expanding scrolls.
- **Sticky header strip.** KPIs, kill switch, and panic-close stay
  reachable from any zone.
- **Single mode per page.** This page is Live-only. Dry-run lives
  on the Strategies page as its own tab. There is no Dry-run
  toggle here at all; if `gates.dry_run` is somehow `true` on this
  page, the page renders a full-width amber warning banner with a
  CTA "Disarm dry-run in Live Settings" and disables the strategy
  picker (defensive — the only way to arm dry-run is via Live
  Settings, but we render a recovery surface anyway).
- **Color discipline.** Live page uses rose-600 for the Mode chip
  background when armed, slate otherwise; Paper page keeps indigo.
  Visual cue prevents wrong-page-wrong-mode mistakes.

### 4.2 Header strip composition

| Slot | Component | Source |
|---|---|---|
| Mode chip | `<LiveModeChip>` (new) — `LIVE ARMED` / `LIVE DISARMED` pill (no `DRY-RUN` state — dry-run lives on Strategies page) | `useLiveCaps().live_orders_enabled` |
| Strategy picker | reused select | `useStrategies()` + URL `?strategy=` |
| WS dot | `<LiveWsHealthDot>` (reused) | `/algo/live/ws-health` |
| Today P&L | new — derived | `/algo/live/dashboard-summary` (§ 5.1) |
| Open P&L | new — derived | same |
| Realised | new — derived | same |
| Cash | new — derived from Kite margins | same (server-side calls `kite._kc.margins()`) |
| Kill switch | `<KillSwitchToggle>` (reused) | `useKillSwitch()` |
| Panic close | `<PanicCloseButton>` (new) | calls existing `/algo/kill-switch/panic-close` |

The dashboard summary endpoint is one new backend route
(§ 5.1) — it batches the four scalars so the header strip is a
single network read instead of three.

### 4.3 Tabs

| Tab | Source page | Notes |
|---|---|---|
| Live | new `LiveDashboard.tsx` (§ 4.1) | default tab on `/algo-trading/live` |
| Positions | new `PositionsTab.tsx` (§ 5.2) | Kite REST `positions/net` + paper_events join |
| Holdings | new `HoldingsTab.tsx` (§ 5.2) | Kite REST `holdings` + paper_events join |
| Settings | new `LiveSettingsTab.tsx` (§ 5.3) | KillSwitch + DriftThreshold + Safety Belts defaults |

The Live tab embeds — in the four zones — these existing components
moved from PaperTab (not rebuilt):

- Zone A → new `<OpenPositionsWidget>` (thin wrapper around
  Kite positions feed)
- Zone B → `<RegimeWidget>` + `<RegimeHistoryChart>` (mini variant)
- Zone C → `<LiveSafetyBeltsForm>` (compact mode, full editor in
  slideover) + caps progress bar
- Zone D → `<LiveLandedOrdersList>` retitled "Recent Fills"
- Footer → `<AttributionPanel>`

`LiveModeToggle` (the 4-gate dry-run/test/live toggle) **moves to
Live Settings**. The Live page assumes Live mode; the user goes to
Settings to arm/disarm.

## 5. Backend additions

Three thin endpoints. No schema changes. No new tables.

### 5.1 `GET /algo/live/dashboard-summary`

Aggregates the header-strip scalars. Response:

```json
{
  "today_pnl_inr": 1240.50,
  "open_pnl_inr": 820.30,
  "realised_pnl_inr": 420.20,
  "cash_inr": 98432.10,
  "open_position_count": 3,
  "mode": "live",       // "live" | "dry_run"
  "ws_age_seconds": 2,
  "kill_switch_active": false
}
```

Implementation: parallel awaits of
`kite._kc.margins()` + `kite._kc.positions()` + existing
`paper_events_v3` realised aggregate. Cache TTL **15s** under
`cache:algo:dash:{user_id}` per § 5.13 Redis pattern in CLAUDE.md.
Invalidated by Iceberg writes to `paper_events_v3` via the existing
`_CACHE_INVALIDATION_MAP`.

### 5.2 `GET /algo/live/positions` and `GET /algo/live/holdings`

Truth source: Kite REST. Performance overlay joined from
`paper_events_v3`.

`GET /algo/live/positions` → intraday positions (Kite `net` array
filtered to `quantity != 0`). Response row:

```json
{
  "tradingsymbol": "ITC",
  "exchange": "NSE",
  "quantity": 8,
  "average_price": 307.33,
  "last_price": 311.20,
  "pnl_inr": 30.96,
  "pnl_pct": 1.26,
  "product": "MIS",
  "strategy_id": "v3-regime-multi",        // joined
  "strategy_name": "V3 Regime Multi",       // joined
  "entry_ts_utc": "2026-05-11T04:19:54Z",   // joined — first BUY fill today
  "entry_reason": "BULL · momentum_z=1.4"   // joined — from event payload
}
```

Join keys: `(tradingsymbol, product, entry_date_ist)`. When no
matching fill exists in `paper_events_v3` (manual order or partial
fill we missed), strategy_* fields are `null` — the row still
renders, just without attribution.

`GET /algo/live/holdings` → Kite `holdings` array. Adds
`days_held` (today − `t1_quantity` first-fill date from our ledger)
and `strategy_origin` join.

Reconciliation: when our ledger has open positions that Kite does
not (or vice-versa), surface in the existing
`<ReconciliationDriftPanel>` — already wired, just remount it on
the Positions tab.

### 5.3 Settings splits

No backend changes. The existing endpoints are simply consumed by
different frontend pages:

| Endpoint | Today consumed by | After split |
|---|---|---|
| `/algo/kill-switch/state` | Settings (algo) | Live Settings |
| `/algo/drift/threshold` | Settings (algo) | Live Settings |
| `/algo/fees/preview` | Settings (algo) | Strategies Settings |
| `/algo/live/safety-belts/{strategy_id}` | embedded in PaperTab | Live Settings (defaults) + Live Dashboard Zone C (per-strategy) |

## 6. Frontend changes

### 6.1 File map

**New routes** (Next.js App Router):

```
frontend/app/(authenticated)/algo-trading/
├── broker/
│   ├── page.tsx          (RSC shell)
│   └── BrokerClient.tsx  (renders <ConnectBrokerTab>)
├── strategies/
│   ├── page.tsx
│   └── StrategiesClient.tsx  (renders 7-tab strip)
├── live/
│   ├── page.tsx
│   └── LiveClient.tsx        (renders 4-tab strip + header strip)
├── page.tsx              (redirect → /algo-trading/strategies)
└── loading.tsx           (kept)
```

**New components** (`frontend/components/algo-trading/live/`):

```
LiveClient.tsx               — page shell, header strip, tabs
LiveHeaderStrip.tsx          — sticky KPI bar
LiveDashboard.tsx            — 4-zone grid (Zone A/B/C/D)
OpenPositionsWidget.tsx      — Zone A
RecentFillsTape.tsx          — Zone D (thin wrapper on LiveLandedOrdersList)
PositionsTab.tsx             — Positions tab content
HoldingsTab.tsx              — Holdings tab content
LiveSettingsTab.tsx          — KillSwitch + Drift + LiveModeToggle (4-gate)
PanicCloseButton.tsx         — confirm modal + POST /kill-switch/panic-close
LiveModeChip.tsx             — LIVE ARMED / DISARMED pill (rose / slate)
```

**New components** (`frontend/components/algo-trading/dryrun/`):

```
DryRunTab.tsx                — page-level tab content for Dry run
DryRunArmBanner.tsx          — amber banner showing dry-run flag state +
                               arm/disarm button (POST /algo/live/dry-run/arm)
```

The Dry-run tab embeds (reused, not duplicated):
`LiveWsHealthDot`, `LiveSafetyBeltsForm`, `ActiveRunsPanel`
(passed `tradingMode="dryrun"`), `KitePostbackPanel`,
`RegimeHistoryChart`, `AttributionPanel`, `PaperSessionSummary`
(filtered `mode="live"`, `dryRun=true`), `PaperEventsTimeline`
(filtered `mode="live"`, `dryRun=true`). LiveModeToggle is **not**
on this tab — the canonical arm/disarm-live surface is Live
Settings; the Dry-run tab only flips the dry-run flag.

**Modified components:**

- `PaperTab.tsx` → split into two files:
  - `PaperTab.tsx` (new, indigo) — Paper-only:
    ActiveRunsPanel + PaperSessionSummary + PaperEventsTimeline
    filtered to `mode="paper"`. Removes `ViewMode`,
    `setDryRunRedis()`, `<LiveSection>`, postback/attribution/
    regime blocks, and the segment toggle.
  - `DryRunTab.tsx` (new, amber) — composes the dry-run widgets
    listed above.
- `SettingsTab.tsx` → split into `StrategiesSettingsTab.tsx`
  (Fee Preview only) and `LiveSettingsTab.tsx` (KillSwitch + Drift
  + LiveModeToggle 4-gate + Safety Belts defaults). Delete the
  union file.
- `AlgoTradingClient.tsx` → delete. Three new `*Client.tsx`
  replace it.
- `frontend/lib/types/algoTrading.ts` → split `AlgoTabId` union
  into `StrategiesTabId` (`instruments | strategies | backtest |
  paper | dryrun | performance | replay | settings`) and
  `LiveTabId` (`live | positions | holdings | settings`).
- `frontend/lib/constants.tsx` → add the `Algo Trading` group with
  three children; remove the flat `algo-trading` entry.

### 6.2 Redirects (preserve bookmarks)

In `frontend/app/(authenticated)/algo-trading/page.tsx`, server-side
redirect based on `?tab=`:

| Legacy `?tab=` | Redirect to |
|---|---|
| `connect` | `/algo-trading/broker` |
| `instruments` `strategies` `backtest` `performance` `replay` | `/algo-trading/strategies?tab=<same>` |
| `paper` | `/algo-trading/strategies?tab=paper` (legacy PaperTab default mode was `live`, then `paper` after the recent change — by today's date both are stale and we re-anchor users to the new Paper tab; Dry-run users get there in one extra click) |
| `settings` | `/algo-trading/strategies?tab=settings` (fee preview is the only thing the legacy Settings tab unambiguously surfaces to backtest users; live users will navigate to the new Live Settings on their own from the sidebar) |
| *(no `tab`)* | `/algo-trading/strategies` (default landing) |

There is no legacy URL that meant "live trading," so no Live
redirect target is needed — Live is reached only via the new
sidebar entry.

### 6.3 Data hooks

| Hook | Purpose | Status |
|---|---|---|
| `useLiveDashboardSummary()` | header-strip KPIs | **new** (SWR, 15s) |
| `useLivePositions()` | Positions tab table | **new** (SWR, 10s) |
| `useLiveHoldings()` | Holdings tab table | **new** (SWR, 30s) |
| `useLiveStatus(strategyId)` | gates | reused |
| `useLiveCaps(strategyId)` | safety belt caps | reused |
| `useKillSwitch()` | KS state | reused |
| `useStrategies()` | strategy picker | reused |
| `usePaperEvents()` | event tape | reused (filter `mode=live` for Live tab; `mode=paper` for Paper tab) |

All new hooks follow CLAUDE.md § 5.3 `apiFetch` + SWR pattern with
2-min dedup and `revalidateOnFocus: false`, and § 5.13 Redis cache
keys are seeded on the backend side (no client-side cache).

### 6.4 Visual / theming

| Surface | Accent | Reason |
|---|---|---|
| Strategies page → Paper tab | indigo-600 / slate | dev / replay rehearsal |
| Strategies page → Dry run tab | amber-500 / slate | live-runtime rehearsal |
| Live Trading page | rose-600 / slate | real money |
| Mode chip when LIVE armed | rose-600 | active real money |
| Mode chip when LIVE disarmed | slate-400 | safe state |
| Dry-run-arm-banner (Dry run tab) | amber-50 bg / amber-700 text | rehearsal mode warning |

Color is decorative; meaning is also conveyed via the tab label,
URL, and page route, so colorblind users have three redundant
signals.

Theme tokens stay on the existing Tailwind palette already used
across the app (`bg-rose-600`, `text-slate-900`, etc.) — no new
design-system additions per CLAUDE.md § "keep theme consistent."

### 6.5 Mobile

- Sidebar already collapses to a drawer (< md).
- Live header strip wraps to a 2-row grid on < md (KPIs in row 2,
  controls in row 1).
- 4-zone grid collapses to single-column stack on < md, in order:
  Header → Open Positions → Recent Fills → Strategy + safety belts
  → Regime → Attribution.

## 7. Migration & rollout

### 7.1 Slicing

Six implementation slices, sized to be a single PR each:

1. **Sidebar group + routes scaffold** (½ day): three new
   route folders, three new `*Client.tsx` placeholders, the
   constants change, the redirect on `/algo-trading`. E2E:
   sidebar shows the group; clicking each entry lands.
2. **Move existing tabs into Strategies page; split Paper/Dry-run**
   (1 day): Cut tabs out of `AlgoTradingClient.tsx`, wire into
   `StrategiesClient.tsx`. Split `PaperTab.tsx` into
   `PaperTab.tsx` (replay-only, indigo) and new
   `DryRunTab.tsx` (live-runtime synthetic, amber). The Live
   branch in today's PaperTab is *deleted* in this slice — it has
   no destination yet (Slice 4 builds Live page). To keep the
   feature reachable in between slices, ship Slice 2 with the
   *old* `/algo-trading?view=live` URL still routable to a
   minimal placeholder that links to "coming in next release" —
   only relevant if Slices 2 and 4 don't land in one PR window.
3. **Backend: dashboard summary + positions + holdings** (1 day):
   3 endpoints, Pydantic responses, unit tests.
4. **Live dashboard + Positions + Holdings tabs** (2 days):
   `LiveHeaderStrip`, `LiveDashboard`, `PositionsTab`,
   `HoldingsTab`, the four widgets. Restart backend per CLAUDE.md
   § 6.2 (new routes + new response_model fields).
5. **Live Settings tab** (½ day):
   move `KillSwitchToggle`, `DriftThresholdInput`,
   `LiveSafetyBeltsForm` (defaults variant), and the full 4-gate
   `LiveModeToggle` to `LiveSettingsTab.tsx`. Strategies →
   Settings keeps only `FeePreviewWidget`. (Live branch removal
   from PaperTab already done in Slice 2; this slice just lands
   the new home for the live-risk knobs.)
6. **E2E + docs** (½ day): Playwright tests per CLAUDE.md § 5.14
   for the four navigation flows + the dashboard render +
   positions reconciliation drift. Update `PROGRESS.md`,
   `docs/algo-trading/`.

Total: ~5 days, ~5 PRs against `feature/algo-trading-three-page-split`.

### 7.2 Risk mitigation

- **Slice 2 is risk-free.** Same components, different parent
  shell. Tests should pass with only selector updates.
- **Slice 4 is the largest.** Behind a feature flag? Not needed —
  the new page is at a new URL; if it's broken, the user just goes
  back to Strategies. The dashboard-summary endpoint is the only
  net-new code path with real-money implications; it's read-only
  and cached.
- **Slice 5 has the highest "did I break Live" risk.** Plan: PR 5
  ships only the migration of the three live controls. We verify
  by smoke-testing with `ALGO_LIVE_DRY_RUN=true` before market
  open, then with a 1-share order at market open.

## 8. Testing

### 8.1 Unit

- `LiveHeaderStrip` renders all 8 KPIs with mocked
  `useLiveDashboardSummary`.
- `LiveModeChip` shows correct color/label for each of
  `{live-armed, live-disarmed, dry-run, missing-status}`.
- `PositionsTab` joins paper_events strategy attribution when
  present; renders `—` when absent.
- Redirect logic on `/algo-trading?tab=*` returns 307 to the right
  new URL for all 8 legacy tab IDs.

### 8.2 E2E (per CLAUDE.md § 5.14)

New tests in `e2e/specs/`:

- `algo-sidebar-group.spec.ts` — group collapsed/expanded states,
  flyout in collapsed sidebar.
- `algo-broker-page.spec.ts` — page loads, login URL renders.
- `algo-strategies-tabs.spec.ts` — all 7 tabs reachable, URL
  syncs, F5 preserves tab.
- `algo-live-dashboard.spec.ts` — header strip + 4 zones above the
  fold on 1280×800; panic-close button gated behind confirm modal.
- `algo-live-positions.spec.ts` — table renders with mocked Kite
  response; reconciliation drift chip surfaces when ledger ≠ Kite.

All new interactive elements register data-testids in
`e2e/utils/selectors.ts` `FE` object.

### 8.3 Performance (per CLAUDE.md § 5.15)

`/algo-trading/live` budget:

| Metric | Target |
|---|---|
| Perf | ≥ 75 |
| LCP | ≤ 3.0 s |
| TBT | ≤ 200 ms |
| CLS | ≤ 0.02 |

Risks: header strip mounts 8 KPIs at once → render delay. Mitigate
with `?? 0` placeholders during SWR loading (no loading-gate skeleton
per § 5.3 anti-pattern). Charts (Regime mini chart) via
`next/dynamic({ ssr: false })`.

## 9. Open questions

None blocking — the four key questions were resolved before this
spec was written:

1. Sidebar = single parent group with 3 children (decided).
2. Settings = domain split, fee preview → Strategies, risk →
   Live (decided).
3. Live layout = dense 4-zone grid (decided).
4. Positions source = Kite REST + paper_events overlay (decided).

Items to validate during implementation:

- Cache TTL for dashboard-summary: spec says 15s; revisit after we
  see real-world request rate.
- Whether Holdings tab should also include intraday positions that
  are *about* to settle (i.e., today's CNC buys). Default: no —
  Holdings = Kite holdings array only, intraday CNC shows in
  Positions until T+1 settles.
- "Recent Fills" tape pause/resume: scope to Live tab only, or
  application-wide? Default: page-local toggle.

## 10. Out of scope (future)

- Per-strategy P&L breakdown chart (could live on a future
  "Strategy" sub-page under Live).
- Charting integration on the Positions row (click → intraday
  candle modal). Tracked as nice-to-have.
- Multi-broker support. Kite remains the only live venue.
- Mobile-first redesign of the dense grid.
