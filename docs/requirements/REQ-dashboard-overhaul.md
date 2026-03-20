# REQ: Dashboard UI Overhaul — Native Landing Page + Chat Side Panel

> **Status**: Requirements Complete — Ready for Design
> **Date**: 2026-03-16
> **Sprint**: Sprint 1 (remaining) + Sprint 2
> **Author**: Abhay Singh (brainstorm with Claude)

---

## 1. Problem Statement

After login, users land on the Chat view — a conversational interface
that doesn't surface the rich data already available (stocks, forecasts,
technical indicators, LLM usage). Users must manually navigate to the
Plotly Dash iframe to see any dashboard content. The chat experience
dominates the UI, making the analytical capabilities feel secondary.

**Goal**: Flip the paradigm — make the **Dashboard the primary surface**
and Chat an **always-available side panel**, giving users immediate
visibility into their portfolio, forecasts, and system health while
keeping conversational AI one click away.

---

## 2. User Stories

### US-1: Dashboard as Landing Page
> As a logged-in user, I want to land on a native dashboard showing my
> portfolio, forecasts, and recent analysis so I can immediately see
> what matters without navigating.

**Acceptance Criteria:**
- [ ] Post-login redirect goes to native Dashboard (not Chat)
- [ ] Dashboard is a Next.js page (not Plotly Dash iframe)
- [ ] Dashboard renders key widgets from Iceberg data layer
- [ ] Dashboard respects dark/light mode
- [ ] Dashboard is responsive (mobile + desktop)

### US-2: Chat as Side Panel (FAB-triggered)
> As a user on any view, I want to open a chat side panel via a
> floating action button so I can ask questions without leaving my
> current context.

**Acceptance Criteria:**
- [ ] Chat FAB visible on ALL views (Dashboard, Docs, Analytics, Admin, Insights)
- [ ] Desktop: FAB in bottom-right opens a resizable side panel
- [ ] Panel starts at 50/50 split, user can drag to resize
- [ ] Mobile: Chat icon in header bar opens chat panel
- [ ] Chat panel has close button; closing preserves state
- [ ] Re-opening chat in same session restores full conversation

### US-3: Chat Session Lifecycle
> As a user, I want my chat to persist within a login session but
> start fresh on next login, with prior sessions available in audit
> logs.

**Acceptance Criteria:**
- [ ] Chat session = login-to-logout window
- [ ] Messages persist in-memory during session, across view switches
- [ ] On logout, chat messages are flushed to audit log storage
- [ ] On next login, chat starts empty (clean slate)
- [ ] User A cannot see User B's chat history (strict segregation)

### US-4: Chat Audit Logs
> As a user, I want to review my past chat sessions so I can
> reference previous conversations and analysis.

**Acceptance Criteria:**
- [ ] "Past Sessions" tab within chat panel shows prior session logs
- [ ] Profile Settings → Audit Log page shows filterable chat history
- [ ] Filters: date range, keyword search
- [ ] Each log entry shows: session date, message count, preview
- [ ] Expanding a log shows full conversation transcript
- [ ] Users can only see their own audit logs

### US-5: Sidebar Navigation
> As a user, I want a persistent sidebar to navigate between
> Dashboard, Docs, Analytics, Admin, and Insights without hunting
> for a menu.

**Acceptance Criteria:**
- [ ] Collapsible sidebar: expanded (labeled) by default on desktop
- [ ] Can toggle to icon-only mode; preference persists
- [ ] Auto-collapses to icon-rail when chat panel is open
- [ ] Re-expands when chat panel closes
- [ ] Mobile: sidebar hidden, navigation via header/hamburger
- [ ] Nav items: Dashboard, Docs, Analytics (Plotly Dash), Admin*, Insights*
  - *Admin/Insights: conditional on user role/permissions

### US-6: Native Dashboard Widgets
> As a user, I want to see interactive widgets for my stocks,
> forecasts, and system metrics on the dashboard.

**Acceptance Criteria:**
- [ ] **Watchlist/Portfolio Overview**: linked tickers, current prices, daily change
- [ ] **Prophet Forecast Charts**: interactive 3/6/9-month forecast with confidence bands
- [ ] **Recent Analysis Reports**: latest analysis summaries per ticker with signals
- [ ] **LLM Usage Summary**: cost breakdown by model, daily trend, cascade stats
- [ ] **Quick-Action Cards**: run analysis, compare stocks, link ticker, view forecast
- [ ] All widgets pull from existing + new backend API endpoints
- [ ] Widgets respect dark/light mode

---

## 3. Functional Requirements

### FR-1: Routing & Navigation

| ID | Requirement |
|----|-------------|
| FR-1.1 | Post-login redirect: `/login` and `/auth/oauth/callback` redirect to `/dashboard` (new route) |
| FR-1.2 | Root `/` redirects to `/dashboard` for authenticated users |
| FR-1.3 | Sidebar navigation replaces the current grid-menu FAB |
| FR-1.4 | Sidebar items: Dashboard, Docs, Analytics, Admin (conditional), Insights (conditional) |
| FR-1.5 | Active view highlighted in sidebar |
| FR-1.6 | Sidebar collapse state persisted in localStorage |

### FR-2: Chat Side Panel

| ID | Requirement |
|----|-------------|
| FR-2.1 | Chat FAB: circular button with chat/message icon, fixed bottom-right |
| FR-2.2 | Click FAB → slide-in panel from right edge |
| FR-2.3 | Panel default width: 50% of viewport (minus sidebar) |
| FR-2.4 | Draggable resize handle on left edge of panel; min 320px, max 80% |
| FR-2.5 | Panel contains: header (title + close button), message list, input box, agent switcher |
| FR-2.6 | "Past Sessions" tab in panel header for audit log access |
| FR-2.7 | Close button hides panel; state preserved in React context |
| FR-2.8 | ESC key closes panel |
| FR-2.9 | Mobile: panel opens as full-width overlay (no split) |
| FR-2.10 | WebSocket connection maintained while panel is closed (messages still received) |

### FR-3: Chat Session Management

| ID | Requirement |
|----|-------------|
| FR-3.1 | Session starts on successful login (JWT issued) |
| FR-3.2 | Messages stored in React state/context during session |
| FR-3.3 | On logout: POST chat transcript to audit log endpoint before clearing |
| FR-3.4 | On token expiry + failed refresh: same as logout (flush + clear) |
| FR-3.5 | User ID attached to all audit log entries for segregation |
| FR-3.6 | No cross-user data leakage — query audit logs filtered by authenticated user_id |

### FR-4: Audit Log Storage

| ID | Requirement |
|----|-------------|
| FR-4.1 | Store chat transcripts in Iceberg data layer (new table: `stocks.chat_audit_log`) |
| FR-4.2 | Schema: `session_id, user_id, started_at, ended_at, message_count, messages_json, agent_ids_used` |
| FR-4.3 | Partitioned by `user_id` for query performance and data isolation |
| FR-4.4 | Backend endpoint: `POST /v1/audit/chat-sessions` (write on logout) |
| FR-4.5 | Backend endpoint: `GET /v1/audit/chat-sessions` (read, filtered by authenticated user) |
| FR-4.6 | Query params: `start_date`, `end_date`, `keyword`, `limit`, `offset` |
| FR-4.7 | Keyword search across `messages_json` content |

### FR-5: Dashboard Widgets & Data

| ID | Requirement |
|----|-------------|
| FR-5.1 | **Watchlist Widget**: GET `/v1/users/me/tickers` + latest OHLCV per ticker |
| FR-5.2 | **Forecast Widget**: latest `forecast_runs` + `forecasts` time series per ticker |
| FR-5.3 | **Analysis Widget**: latest `analysis_summary` per ticker (signals, risk metrics) |
| FR-5.4 | **LLM Usage Widget**: aggregated `llm_usage` by date/model with cost calculation |
| FR-5.5 | **Quick Actions Widget**: buttons to trigger analysis, forecast, link ticker |
| FR-5.6 | All widgets use `apiFetch` (auto-refresh JWT) — never raw `fetch` |
| FR-5.7 | Loading skeletons for each widget during data fetch |
| FR-5.8 | Error states per widget (don't crash entire dashboard) |

### FR-6: New Backend Endpoints Required

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/v1/dashboard/watchlist` | GET | User | Tickers + latest prices + daily change |
| `/v1/dashboard/forecasts/summary` | GET | User | Latest forecast targets per ticker |
| `/v1/dashboard/analysis/latest` | GET | User | Latest analysis summaries + signals |
| `/v1/dashboard/llm-usage` | GET | User* | Usage/cost by date range (user's own) |
| `/v1/audit/chat-sessions` | POST | User | Write chat transcript on logout |
| `/v1/audit/chat-sessions` | GET | User | Read own past chat sessions |
| `/v1/dashboard/company-info/{ticker}` | GET | User | Company fundamentals |

*Superusers see all users' LLM usage; regular users see only their own.

---

## 4. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | Dashboard initial load < 2s (skeleton → data) |
| NFR-2 | Chat panel open/close animation < 200ms |
| NFR-3 | Sidebar collapse/expand animation < 150ms |
| NFR-4 | Chat audit log flush on logout < 500ms (fire-and-forget OK) |
| NFR-5 | Dark/light mode: all new components must support both themes |
| NFR-6 | Responsive breakpoints: mobile (<768px), tablet (768-1024px), desktop (>1024px) |
| NFR-7 | Accessibility: keyboard navigation for sidebar, chat panel, FAB |
| NFR-8 | Line length 79 chars (Python), ESLint compliance (TypeScript) |
| NFR-9 | Test coverage: happy path + 1 error path per new component/endpoint |

---

## 5. Audit Log Storage — Architecture Decision

### Recommended: Iceberg (`stocks.chat_audit_log`)

**Why Iceberg (primary recommendation):**
- Consistent with existing data layer (11 tables already)
- Partitioning by `user_id` ensures fast per-user queries
- Append-only pattern matches audit log semantics perfectly
- No new infrastructure needed
- Time-travel queries for compliance if needed later

**Alternative considered: PostgreSQL**
- Pros: faster for small, frequent reads; native full-text search
- Cons: adds new infrastructure dependency; project currently has no RDBMS
- Verdict: not justified for audit-log-volume data

**Alternative considered: SQLite**
- Pros: zero-config, lightweight
- Cons: doesn't scale for multi-user, no partitioning, file-lock contention
- Verdict: not suitable

**Hybrid option (future):** If keyword search across audit logs becomes
a bottleneck, add a search index (e.g., SQLite FTS or Meilisearch) that
syncs from Iceberg. Not needed for MVP.

### Proposed Table Schema

```
stocks.chat_audit_log
├── session_id        STRING (PK, UUID)
├── user_id           STRING (partition key)
├── started_at        TIMESTAMP
├── ended_at          TIMESTAMP
├── message_count     INT
├── messages_json     STRING (JSON array of {role, content, timestamp, agent_id})
├── agent_ids_used    LIST<STRING>
├── ip_address        STRING
├── user_agent        STRING
└── created_at        TIMESTAMP
```

---

## 6. Existing Endpoint Mapping (Ready to Use)

| Widget Need | Existing Endpoint | Status |
|-------------|-------------------|--------|
| User profile | `GET /v1/auth/me` | Ready |
| User tickers | `GET /v1/users/me/tickers` | Ready |
| Cascade health | `GET /v1/admin/tier-health` | Ready (superuser) |
| Admin metrics | `GET /v1/admin/metrics` | Ready (superuser) |
| Session list | `GET /v1/auth/sessions` | Ready |
| Existing audit log | `GET /v1/admin/audit-log` | Ready (superuser) |

| Widget Need | Data Source | Gap |
|-------------|-------------|-----|
| Stock prices | `stocks.ohlcv` | Needs HTTP endpoint |
| Forecasts | `stocks.forecast_runs` + `stocks.forecasts` | Needs HTTP endpoint |
| Analysis signals | `stocks.analysis_summary` | Needs HTTP endpoint |
| Company info | `stocks.company_info` | Needs HTTP endpoint |
| LLM usage (per-user) | `stocks.llm_usage` | Needs HTTP endpoint |
| Chat audit | N/A | Needs table + endpoints |

---

## 7. Sprint Phasing

### Sprint 1 (Remaining: Mar 16–18) — Foundation

| Priority | Item | Estimate |
|----------|------|----------|
| P0 | Sidebar navigation component | 3 SP |
| P0 | Chat FAB + side panel (resizable) | 5 SP |
| P0 | Post-login routing change (→ /dashboard) | 1 SP |
| P0 | Dashboard page shell (layout + skeleton widgets) | 3 SP |
| P0 | HTML design mockups (3 options for review) | 2 SP |
| — | **Sprint 1 subtotal** | **14 SP** |

### Sprint 2 (Mar 19–25) — Full Build

| Priority | Item | Estimate |
|----------|------|----------|
| P0 | New backend dashboard endpoints (6 endpoints) | 5 SP |
| P0 | Native dashboard widgets (5 widgets) | 8 SP |
| P0 | Chat session lifecycle (persist + flush) | 3 SP |
| P1 | Chat audit log table + endpoints | 3 SP |
| P1 | Past Sessions tab in chat panel | 2 SP |
| P1 | Profile Settings audit log page | 2 SP |
| P1 | Plotly Dash renamed to "Analytics" in sidebar | 1 SP |
| P2 | Mobile responsive polish | 2 SP |
| P2 | Keyboard navigation + accessibility | 2 SP |
| — | **Sprint 2 subtotal** | **28 SP** |

---

## 8. Open Questions

| # | Question | Impact |
|---|----------|--------|
| OQ-1 | Should quick-action cards trigger agent chat (opening the panel) or run silently in background? | UX flow for quick actions |
| OQ-2 | Should the dashboard auto-refresh widget data on an interval, or only on page load + manual refresh? | Performance, API load |
| OQ-3 | For the forecast chart widget, should users select which ticker to view, or show all linked tickers? | Widget complexity |
| OQ-4 | Should the chat audit log store raw markdown or rendered text? | Storage size, display logic |
| OQ-5 | Maximum chat message history size before truncation within a session? | Memory usage |

---

## 9. Design Mockup Request

**3 HTML mockup variants needed** (for Abhay's review before implementation):

### Variant A: "Command Center"
- Dense, data-rich layout with 2×3 widget grid
- Sidebar with icons + labels
- Chat panel as clean right drawer
- Dark mode emphasis (trading terminal aesthetic)

### Variant B: "Clean Dashboard"
- Spacious card-based layout with generous whitespace
- Top-level KPI cards + expandable detail sections
- Sidebar icon-rail with hover expand
- Chat panel with subtle glass-morphism effect

### Variant C: "Hybrid"
- Hero section (portfolio summary) + scrollable widget stream
- Collapsible sidebar
- Chat panel with tabs (Current Chat | Past Sessions)
- Balanced information density

---

## 10. Next Steps

1. **Now**: Generate HTML design mockups (3 variants) → Abhay reviews
2. **After review**: Select variant → `/sc:design` for architecture
3. **Then**: `/sc:workflow` for implementation plan
4. **Sprint 1**: Foundation (sidebar, chat panel, routing, shell)
5. **Sprint 2**: Full build (widgets, endpoints, audit, polish)
