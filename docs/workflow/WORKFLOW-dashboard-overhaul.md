# WORKFLOW: Dashboard UI Overhaul — Implementation Plan

> **Status**: Ready for Execution
> **Date**: 2026-03-16
> **Design**: [DESIGN-dashboard-overhaul.md](../design/DESIGN-dashboard-overhaul.md)
> **Requirements**: [REQ-dashboard-overhaul.md](../requirements/REQ-dashboard-overhaul.md)
> **Mockup**: Variant C — Hybrid (approved)

---

## Sprint Allocation

| Sprint | Dates | Focus | SP |
|--------|-------|-------|----|
| Sprint 1 (remaining) | Mar 16–18 | Foundation — routing, layout, sidebar, chat panel | 14 |
| Sprint 2 | Mar 19–25 | Full build — widgets, backend, audit, polish | 28 |

---

## Phase 1: Frontend Foundation (Sprint 1 — Days 1–2)

### Step 1.1: Route Structure + Middleware
> **Est**: 2 SP | **Deps**: None | **Parallel**: No (everything depends on this)

**Files to create:**
- `frontend/middleware.ts` — root `/` → `/dashboard` redirect
- `frontend/app/(authenticated)/layout.tsx` — shell (placeholder, just renders children)
- `frontend/app/(authenticated)/dashboard/page.tsx` — placeholder "Dashboard" text
- `frontend/app/(authenticated)/docs/page.tsx` — docs iframe (extract from page.tsx)
- `frontend/app/(authenticated)/analytics/page.tsx` — Plotly Dash iframe (rename from "dashboard")
- `frontend/app/(authenticated)/admin/page.tsx` — admin iframe
- `frontend/app/(authenticated)/insights/page.tsx` — insights iframe

**Files to modify:**
- `frontend/app/page.tsx` — replace with `redirect("/dashboard")`
- `frontend/app/login/page.tsx` — change `router.replace("/")` → `router.replace("/dashboard")`
- `frontend/app/auth/oauth/callback/page.tsx` — same redirect change
- `frontend/lib/constants.tsx` — update `View` type, rename "dashboard" → "analytics" in nav items

**Checkpoint:** Login → lands on `/dashboard` (placeholder). Navigate to `/docs`, `/analytics` — iframes load. Browser back/forward works.

---

### Step 1.2: Context Providers
> **Est**: 2 SP | **Deps**: 1.1 | **Parallel**: Can parallel with 1.3

**Files to create:**
- `frontend/providers/ChatProvider.tsx` — ChatContext with messages, panel state, session ID, flush
- `frontend/providers/LayoutProvider.tsx` — sidebar collapsed state, auto-collapse logic

**Files to modify:**
- `frontend/app/(authenticated)/layout.tsx` — wrap children in both providers

**Checkpoint:** Contexts accessible from any child component. `useChatContext()` and `useLayoutContext()` hooks work.

---

### Step 1.3: Sidebar Navigation
> **Est**: 3 SP | **Deps**: 1.1, 1.2 | **Parallel**: Can parallel with 1.2

**Files to create:**
- `frontend/components/Sidebar.tsx` — full sidebar with nav items, collapse, theme toggle

**Reuse from existing:**
- Permission logic from `NavigationMenu.tsx` (`canSeeItem`)
- Theme toggle from `NavigationMenu.tsx` (sun/moon icons)
- Follow Variant C mockup styling

**Implementation details:**
- Use `next/link` + `usePathname()` for active state
- Collapse state in `localStorage` key `sidebar_collapsed`
- Read `chatOpen` from `ChatContext` → auto-collapse when true
- Expanded: 220px with icon + label
- Collapsed: 62px icon-only with CSS `title` tooltips
- Gradient left border on active item
- Mobile: hidden (hamburger drawer — keep existing pattern)

**Checkpoint:** Sidebar renders. Click items → navigate between routes. Collapse/expand works. Theme toggle works. Active item highlights.

---

### Step 1.4: App Header (Simplified)
> **Est**: 1 SP | **Deps**: 1.1, 1.2 | **Parallel**: Yes (with 1.3)

**Files to create:**
- `frontend/components/AppHeader.tsx` — simplified header

**Reuse from existing:**
- Profile chip + dropdown from `ChatHeader.tsx`
- Avatar rendering logic
- Edit Profile, Change Password, Manage Sessions modal triggers

**Changes vs ChatHeader:**
- Remove agent switcher (moved to chat panel)
- Remove clear chat button (moved to chat panel)
- Add page title / breadcrumb (from `usePathname`)
- Add chat icon button (mobile only — triggers ChatContext.togglePanel)
- Keep hamburger (mobile, triggers sidebar drawer)

**Checkpoint:** Header shows on all pages. Profile dropdown works. Mobile: hamburger + chat icon visible.

---

### Step 1.5: Chat FAB + Chat Panel (Core)
> **Est**: 5 SP | **Deps**: 1.2, 1.4 | **Parallel**: No (critical path)

**Files to create:**
- `frontend/components/ChatFAB.tsx` — floating button
- `frontend/components/ChatPanel.tsx` — slide-in panel
- `frontend/components/ChatPanelHeader.tsx` — header with tabs, agent switcher, close
- `frontend/components/ResizeHandle.tsx` — drag-to-resize
- `frontend/hooks/useResizePanel.ts` — resize state + mouse handlers
- `frontend/hooks/useChatSession.ts` — session lifecycle (replaces useChatHistory)

**Reuse from existing:**
- `MessageBubble.tsx` — unchanged, render in panel
- `ChatInput.tsx` — unchanged, render in panel
- `useSendMessage.ts` — unchanged, used inside panel
- `useWebSocket.ts` — moved into ChatProvider for shared access

**Implementation order within this step:**
1. `useResizePanel` hook (stateless, testable)
2. `useChatSession` hook (replaces localStorage with in-memory + flush)
3. `ChatFAB` (simple — reads context, toggles panel)
4. `ResizeHandle` (pure UI + mouse events)
5. `ChatPanelHeader` (tabs UI, agent switcher, close button)
6. `ChatPanel` (compose all pieces: header, messages, input, resize)

**Key behaviors:**
- Panel default: 50% of content area width
- Drag resize: min 320px, max 80%
- ESC closes panel
- Sidebar auto-collapses on open, restores on close
- WebSocket stays connected even when panel hidden
- Messages persist in context across open/close
- Agent switcher: pill toggle [General | Stock Analysis]
- "Past Sessions" tab shown but disabled (placeholder — Sprint 2)

**Checkpoint:** Click FAB → panel slides in. Send message → streaming response works. Resize by dragging. ESC/X closes. Sidebar collapses. Reopen → messages still there.

---

### Step 1.6: Layout Integration + Cleanup
> **Est**: 1 SP | **Deps**: 1.1–1.5 all complete | **Parallel**: No

**Files to modify:**
- `frontend/app/(authenticated)/layout.tsx` — assemble: providers → header → sidebar → content → chat panel → FAB
- CSS grid for layout: `sidebar | content | chat-panel`

**Files to deprecate:**
- `frontend/components/NavigationMenu.tsx` — no longer imported (keep file for now, remove in cleanup)
- `frontend/hooks/useChatHistory.ts` — replaced by `useChatSession`

**Modals integration:**
- Move EditProfileModal, ChangePasswordModal, SessionManagementModal into `(authenticated)/layout.tsx`
- They trigger from AppHeader profile dropdown

**Checkpoint:** Full layout works end-to-end. Login → /dashboard → sidebar + header + content. Open chat → half-split works. Navigate between routes → sidebar active updates, content changes, chat persists. Modals work. Dark/light mode works everywhere.

---

## Phase 2: Backend Dashboard Endpoints (Sprint 2 — Days 1–2)

### Step 2.1: Pydantic Models + Router Skeleton
> **Est**: 2 SP | **Deps**: None (backend independent of frontend) | **Parallel**: Yes (with 2.2)

**Files to create:**
- `backend/dashboard_models.py` — all response models (WatchlistResponse, ForecastsResponse, AnalysisResponse, LLMUsageResponse, ChatSessionCreate, ChatSessionSummary)
- `backend/dashboard_routes.py` — router factory with endpoint stubs returning mock data
- `backend/audit_routes.py` — router factory with endpoint stubs

**Files to modify:**
- `backend/routes.py` — register `dashboard_router` and `audit_router` under `/v1`

**Checkpoint:** `curl /v1/dashboard/watchlist` returns mock JSON. All 6 endpoints respond. Auth required (401 without token).

---

### Step 2.2: Repository Methods (Iceberg Queries)
> **Est**: 3 SP | **Deps**: None | **Parallel**: Yes (with 2.1)

**Files to modify:**
- `stocks/repository.py` — add new methods:
  - `get_latest_ohlcv(ticker, limit=30)` — last N OHLCV rows
  - `get_latest_company_info(ticker)` — most recent snapshot
  - `get_latest_forecast_runs(tickers)` — latest per ticker
  - `get_latest_analysis(tickers)` — latest per ticker
  - `get_llm_usage_summary(user_id, days=30)` — aggregated stats
  - `save_chat_session(session_dict)` — append to chat_audit_log
  - `list_chat_sessions(user_id, filters)` — query audit log

**New table creation:**
- Add `chat_audit_log` table creation in repository init (or migration script)
- Schema: session_id, user_id, started_at, ended_at, message_count, messages_json, agent_ids_used, ip_address, user_agent, created_at
- Partitioned by `user_id`

**Checkpoint:** Repository methods return correct DataFrames from test catalog. Chat session writes + reads work.

---

### Step 2.3: Wire Endpoints to Repository
> **Est**: 2 SP | **Deps**: 2.1, 2.2 | **Parallel**: No

**Files to modify:**
- `backend/dashboard_routes.py` — replace mock data with real Iceberg queries
- `backend/audit_routes.py` — wire to repository

**Endpoint logic:**
1. `/v1/dashboard/watchlist` — get user tickers → get latest OHLCV + company_info per ticker → compute change → return WatchlistResponse
2. `/v1/dashboard/forecasts/summary` — get user tickers → get latest forecast_runs → return ForecastsResponse
3. `/v1/dashboard/analysis/latest` — get user tickers → get latest analysis_summary → extract signals → return AnalysisResponse
4. `/v1/dashboard/llm-usage` — get llm_usage_summary (filter by user_id for non-superusers) → return LLMUsageResponse
5. `POST /v1/audit/chat-sessions` — validate ChatSessionCreate → save_chat_session
6. `GET /v1/audit/chat-sessions` — list_chat_sessions (filtered by authenticated user)

**Checkpoint:** Live data from Iceberg flows through all endpoints. Auth filtering works (user sees own data only, superuser sees all LLM usage).

---

### Step 2.4: Backend Tests
> **Est**: 2 SP | **Deps**: 2.3 | **Parallel**: Yes (with Phase 3)

**Files to create:**
- `tests/test_dashboard_routes.py` — endpoint tests
- `tests/test_audit_routes.py` — audit endpoint tests
- `tests/test_dashboard_repository.py` — repository method tests

**Test matrix:**
| Endpoint | Happy path | Auth required | Empty data | Error path |
|----------|-----------|---------------|------------|------------|
| GET /dashboard/watchlist | User with tickers | 401 without token | User with no tickers → empty | - |
| GET /dashboard/forecasts/summary | Tickers with forecasts | 401 | No forecasts → empty | - |
| GET /dashboard/analysis/latest | Tickers with analysis | 401 | No analysis → empty | - |
| GET /dashboard/llm-usage | User with usage | 401 | No usage → zeroes | - |
| POST /audit/chat-sessions | Valid session | 401 | Empty messages → reject | Invalid JSON |
| GET /audit/chat-sessions | User with sessions | 401 | No sessions → empty | - |

**Checkpoint:** `pytest tests/test_dashboard_routes.py tests/test_audit_routes.py -v` — all pass.

---

## Phase 3: Native Dashboard Widgets (Sprint 2 — Days 2–4)

### Step 3.1: Data Hooks + Types
> **Est**: 1 SP | **Deps**: 2.1 (models defined) | **Parallel**: Yes (start once models exist)

**Files to create:**
- `frontend/lib/types.ts` — TypeScript interfaces mirroring Pydantic models
- `frontend/hooks/useDashboardData.ts` — generic fetcher hook
- Typed wrappers: `useWatchlist`, `useForecastSummary`, `useAnalysisLatest`, `useLLMUsage`

**Checkpoint:** Hooks compile. With backend running, hooks return real data.

---

### Step 3.2: Widget Components (Parallel)
> **Est**: 5 SP | **Deps**: 3.1 | **Parallel**: All widgets can be built in parallel

Build these **in parallel** (independent components):

#### 3.2a: HeroSection (1 SP)
- `frontend/components/widgets/HeroSection.tsx`
- Welcome greeting (from profile), portfolio value, change pill, stat pills, quick action buttons
- Quick actions: open chat panel with pre-filled prompt (e.g., "Analyze AAPL")
- Gradient mesh background (CSS), noise texture overlay

#### 3.2b: WatchlistWidget (1.5 SP)
- `frontend/components/widgets/WatchlistWidget.tsx`
- Table: ticker dot, ticker+company, price (mono font), change pill, sparkline SVG
- Green/red coloring for positive/negative
- Sparkline: render from last 30 OHLCV close prices as SVG polyline
- Alternating row stripes, hover highlight

#### 3.2c: AnalysisSignalsWidget (1 SP)
- `frontend/components/widgets/AnalysisSignalsWidget.tsx`
- Stacked signal cards: RSI, MACD, SMA, Bollinger
- Each: name, value, signal text, colored badge
- Bullish=green, Bearish=red, Neutral=yellow

#### 3.2d: LLMUsageWidget (0.5 SP)
- `frontend/components/widgets/LLMUsageWidget.tsx`
- Stats grid: total requests, total cost, avg latency
- Model breakdown: CSS donut or horizontal bars
- Mono font for all numbers

#### 3.2e: ForecastChartWidget (1 SP)
- `frontend/components/widgets/ForecastChartWidget.tsx`
- SVG chart: historical line (solid), forecast (dashed), confidence band (gradient fill)
- Ticker selector dropdown (from user's linked tickers)
- Axis labels, grid lines, legend

#### Shared:
- `frontend/components/widgets/WidgetSkeleton.tsx` — loading skeleton (reusable pulse animation)
- `frontend/components/widgets/WidgetError.tsx` — error state card

**Checkpoint:** Each widget renders with mock data. Loading skeletons show. Error states display.

---

### Step 3.3: Dashboard Page Assembly
> **Est**: 2 SP | **Deps**: 3.2 complete | **Parallel**: No

**Files to modify:**
- `frontend/app/(authenticated)/dashboard/page.tsx` — compose all widgets

**Layout (Variant C grid):**
```
Hero (full width)
Watchlist (1.1fr) | Analysis + LLM (0.9fr, stacked)
Forecast Chart (full width)
```

**CSS grid with responsive breakpoints:**
- Desktop (>1024px): 2-column asymmetric
- Tablet (768–1024): 2-column equal
- Mobile (<768): single column stack

**Checkpoint:** Dashboard renders with live data from all endpoints. Responsive at all breakpoints. Dark/light mode correct.

---

## Phase 4: Chat Audit + Past Sessions (Sprint 2 — Days 4–5)

### Step 4.1: Chat Session Flush on Logout
> **Est**: 1 SP | **Deps**: 2.3 (audit endpoints live) | **Parallel**: Yes

**Files to modify:**
- `frontend/hooks/useChatSession.ts` — implement `flush()` → POST to audit endpoint
- `frontend/components/AppHeader.tsx` — call `flush()` before `clearTokens()` on sign out
- `frontend/providers/ChatProvider.tsx` — expose `flush` in context

**Also handle:**
- Token expiry with failed refresh → same flush + redirect
- Browser close / tab close → `beforeunload` event → `navigator.sendBeacon` as last resort

**Checkpoint:** Sign out → chat saved to Iceberg. Sign back in → chat is empty. Verify via `GET /v1/audit/chat-sessions`.

---

### Step 4.2: Past Sessions Tab
> **Est**: 2 SP | **Deps**: 4.1 | **Parallel**: No

**Files to create:**
- `frontend/hooks/usePastSessions.ts` — fetches audit log
- `frontend/components/PastSessionsTab.tsx` — session list with expand

**Enable the tab:**
- `ChatPanelHeader.tsx` — enable "Past Sessions" tab (was placeholder)

**Features:**
- List of session cards: date, message count, preview (first 150 chars)
- Click to expand → full read-only transcript
- Date range filter (start/end date inputs)
- Loading skeleton while fetching
- Empty state: "No past sessions yet"

**Checkpoint:** Open chat panel → Past Sessions tab → shows prior sessions. Expand one → full transcript visible. Filter by date works.

---

### Step 4.3: Profile Settings Audit Log Page
> **Est**: 2 SP | **Deps**: 4.1 | **Parallel**: Yes (with 4.2)

**Files to create:**
- `frontend/app/(authenticated)/settings/page.tsx` — or add tab to existing profile modal

**Decision:** Add as a section in the existing Edit Profile modal (simpler, no new route needed):
- New tab in EditProfileModal: "Profile" | "Audit Log"
- Audit Log tab: same PastSessionsTab component, reused
- Add keyword search filter in addition to date range

**Files to modify:**
- `frontend/components/EditProfileModal.tsx` — add tab system + audit log tab
- Add sidebar nav item or access via profile dropdown → "Activity Log"

**Checkpoint:** Profile dropdown → "Activity Log" → shows filterable chat session history.

---

## Phase 5: Polish + Testing (Sprint 2 — Days 5–7)

### Step 5.1: Responsive Polish
> **Est**: 2 SP | **Deps**: All phases complete | **Parallel**: Yes

- Mobile sidebar: slide-out drawer (existing pattern, reconnect)
- Mobile chat: full-screen overlay (no split)
- Mobile header: hamburger + chat icon
- Tablet: sidebar icon-rail + 50% chat
- Widget responsive: single column on mobile
- Hero quick actions: 2x2 grid on mobile

### Step 5.2: Accessibility + Keyboard Nav
> **Est**: 1 SP | **Deps**: 5.1 | **Parallel**: Yes

- Tab order: sidebar items → content → chat panel
- ESC: close chat panel, close modals
- Sidebar: arrow keys to navigate items
- Chat input: Enter send, Shift+Enter newline (existing)
- ARIA labels on FAB, sidebar toggle, resize handle
- Focus trap in chat panel when open

### Step 5.3: Frontend Tests
> **Est**: 2 SP | **Deps**: All phases | **Parallel**: Yes

**Files to create:**
- `frontend/__tests__/Sidebar.test.tsx`
- `frontend/__tests__/ChatPanel.test.tsx`
- `frontend/__tests__/ChatFAB.test.tsx`
- `frontend/__tests__/DashboardPage.test.tsx`
- `frontend/__tests__/hooks/useDashboardData.test.ts`
- `frontend/__tests__/hooks/useChatSession.test.ts`
- `frontend/__tests__/hooks/useResizePanel.test.ts`

**Test approach:** vitest + React Testing Library, mock `apiFetch`

### Step 5.4: E2E Tests
> **Est**: 2 SP | **Deps**: 5.3 | **Parallel**: No (needs live services)

**Add to existing E2E suite:**
1. Login → lands on /dashboard (not /chat)
2. Dashboard widgets render with data
3. Sidebar navigation between all routes
4. Sidebar collapse/expand
5. Chat FAB → open panel → send message → receive response
6. Chat panel resize
7. Close chat → reopen → messages persist
8. Sign out → sign in → chat is empty
9. Past Sessions tab shows prior session

### Step 5.5: Cleanup + PROGRESS.md
> **Est**: 1 SP | **Deps**: All tests pass | **Parallel**: No

- Remove deprecated `NavigationMenu.tsx` (once fully replaced)
- Remove deprecated `useChatHistory.ts`
- Update old `page.tsx` references in any tests
- Lint: `black`, `isort`, `flake8` (backend) + `eslint` (frontend)
- Update `PROGRESS.md` with session entries
- Update `CLAUDE.md` if architecture references changed

---

## Dependency Graph

```
Phase 1 (Sprint 1):
  1.1 Route Structure ──┬──→ 1.2 Contexts ──┐
                        │                    ├──→ 1.5 Chat Panel ──→ 1.6 Integration
                        ├──→ 1.3 Sidebar ────┘
                        └──→ 1.4 App Header ─┘

Phase 2 (Sprint 2, Days 1-2):
  2.1 Models/Skeleton ──┬──→ 2.3 Wire Endpoints ──→ 2.4 Backend Tests
  2.2 Repository ───────┘

Phase 3 (Sprint 2, Days 2-4):
  3.1 Data Hooks ──→ 3.2a Hero ────────┐
                     3.2b Watchlist ────┤
                     3.2c Signals ──────┼──→ 3.3 Dashboard Assembly
                     3.2d LLM Usage ───┤
                     3.2e Forecast ─────┘

Phase 4 (Sprint 2, Days 4-5):
  4.1 Session Flush ──┬──→ 4.2 Past Sessions Tab
                      └──→ 4.3 Profile Audit Log

Phase 5 (Sprint 2, Days 5-7):
  5.1 Responsive ──→ 5.2 A11y ──→ 5.3 Frontend Tests ──→ 5.4 E2E ──→ 5.5 Cleanup
```

---

## Parallelization Opportunities

| Window | Parallel tracks |
|--------|----------------|
| Sprint 1, Day 1 | 1.1 (routing) then 1.2 + 1.3 + 1.4 in parallel |
| Sprint 1, Day 2 | 1.5 (chat panel) then 1.6 (integration) |
| Sprint 2, Day 1 | 2.1 + 2.2 in parallel (backend), 3.1 starts once 2.1 done |
| Sprint 2, Day 2 | 2.3 (wire), 3.2a-e widgets in parallel |
| Sprint 2, Day 3 | 2.4 (backend tests) + 3.3 (dashboard assembly) |
| Sprint 2, Day 4 | 4.1 + 4.2 + 4.3 in parallel |
| Sprint 2, Day 5-6 | 5.1-5.4 sequential |
| Sprint 2, Day 7 | 5.5 cleanup + PR |

---

## Quality Gates

| Gate | Criteria | When |
|------|----------|------|
| G1 | Routes work, sidebar navigates, chat panel opens/closes | End of Phase 1 |
| G2 | All 6 backend endpoints return live Iceberg data | End of Phase 2 |
| G3 | Dashboard renders all widgets with real data | End of Phase 3 |
| G4 | Chat audit: flush on logout, past sessions visible | End of Phase 4 |
| G5 | All tests pass (backend 273+, frontend 18+, E2E 49+) | End of Phase 5 |
| G6 | Lint clean: black, isort, flake8, eslint | Before PR |

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Iceberg query perf for dashboard (5 tickers × 4 queries) | Slow page load | Parallel queries with `asyncio.gather`, add response caching |
| Chat panel + sidebar + content layout breaks on edge widths | UI glitch | Test at 1024, 1280, 1440, 1920px widths |
| WebSocket reconnect when panel hidden | Lost messages | Keep WS in provider (always connected), queue events |
| `beforeunload` flush unreliable | Lost audit data | Use `navigator.sendBeacon` + periodic auto-save every 60s |
| Variant C mockup → real Tailwind divergence | Visual mismatch | Reference mockup CSS values directly in implementation |

---

## Jira Ticket Mapping

| Step | Jira Ticket | Sprint |
|------|-------------|--------|
| 1.1–1.6 | Create under Sprint 1 (id=35) | Sprint 1 |
| 2.1–2.4 | Create under Sprint 2 (id=36) | Sprint 2 |
| 3.1–3.3 | Create under Sprint 2 (id=36) | Sprint 2 |
| 4.1–4.3 | Create under Sprint 2 (id=36) | Sprint 2 |
| 5.1–5.5 | Create under Sprint 2 (id=36) | Sprint 2 |

---

## Next Step

Execute with `/sc:implement` — start Phase 1, Step 1.1.
