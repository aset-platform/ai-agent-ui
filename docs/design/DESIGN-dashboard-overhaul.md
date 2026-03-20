# DESIGN: Dashboard UI Overhaul — Architecture & Technical Specification

> **Status**: Design Complete — Ready for Implementation
> **Date**: 2026-03-16
> **Source**: [REQ-dashboard-overhaul.md](../requirements/REQ-dashboard-overhaul.md)
> **Selected Mockup**: Variant C — Hybrid (Hero + Scrollable Widget Stream)
> **Sprint**: Sprint 1 (remainder) + Sprint 2

---

## 1. Architecture Overview

### 1.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        Next.js App Shell                     │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                    AppLayout (new)                       │ │
│  │  ┌──────────┐  ┌────────────────────────┐  ┌─────────┐ │ │
│  │  │ Sidebar  │  │     Page Content       │  │  Chat   │ │ │
│  │  │ Nav      │  │  ┌──────────────────┐  │  │  Panel  │ │ │
│  │  │          │  │  │  /dashboard      │  │  │         │ │ │
│  │  │ Dashboard│  │  │  /docs (iframe)  │  │  │ Header  │ │ │
│  │  │ Analytics│  │  │  /analytics      │  │  │ Messages│ │ │
│  │  │ Docs     │  │  │  /admin          │  │  │ Input   │ │ │
│  │  │ Admin*   │  │  │  /insights       │  │  │         │ │ │
│  │  │ Insights*│  │  └──────────────────┘  │  │ Past    │ │ │
│  │  │          │  │                        │  │ Sessions│ │ │
│  │  │ [theme]  │  │                        │  │         │ │ │
│  │  │ [collapse│  │                        │  │ [close] │ │ │
│  │  └──────────┘  └────────────────────────┘  └─────────┘ │ │
│  │                                             [Chat FAB]  │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 State Architecture

Current app uses **prop-drilling from page.tsx** with no context providers.
The overhaul introduces **two React contexts** to manage cross-cutting state
that multiple components need without deep prop chains:

```
ChatContext (new)
├── messages: Message[]
├── setMessages
├── isOpen: boolean
├── togglePanel()
├── closePanel()
├── agentId: string
├── setAgentId
└── sessionId: string (generated on login)

LayoutContext (new)
├── sidebarCollapsed: boolean
├── toggleSidebar()
├── chatOpen: boolean (read from ChatContext)
└── activeView: View
```

**Why contexts now?** The chat panel and sidebar need to coordinate
(sidebar auto-collapses when chat opens) and both are rendered by the
layout, not individual pages. Prop-drilling across layout boundaries
would be cumbersome.

---

## 2. Frontend Architecture

### 2.1 Route Structure (New)

```
frontend/app/
├── layout.tsx                    (root — fonts, dark mode script)
├── (authenticated)/              (NEW route group — layout with sidebar + chat)
│   ├── layout.tsx                (NEW — AppLayout: sidebar + chat panel + FAB)
│   ├── dashboard/
│   │   └── page.tsx              (NEW — native dashboard)
│   ├── docs/
│   │   └── page.tsx              (NEW — docs iframe wrapper)
│   ├── analytics/
│   │   └── page.tsx              (NEW — Plotly Dash iframe, renamed from "dashboard")
│   ├── admin/
│   │   └── page.tsx              (NEW — admin iframe wrapper)
│   └── insights/
│       └── page.tsx              (NEW — insights iframe wrapper)
├── login/
│   └── page.tsx                  (existing — unchanged)
└── auth/oauth/callback/
    └── page.tsx                  (existing — redirect target changes to /dashboard)
```

**Key decisions:**
- Use Next.js **route groups** `(authenticated)` for shared layout without URL prefix
- Each view is a real route (enables browser back/forward, bookmarking)
- `page.tsx` (root) redirects to `/dashboard` via middleware
- Existing `page.tsx` chat logic moves into `ChatPanel` component

### 2.2 Middleware (New)

```typescript
// frontend/middleware.ts
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Root → dashboard redirect
  if (pathname === "/") {
    return NextResponse.redirect(
      new URL("/dashboard", request.url)
    );
  }
}

export const config = {
  matcher: ["/"],
};
```

Auth guard remains client-side (`useAuthGuard` hook) — unchanged.

### 2.3 Login Redirect Change

```typescript
// login/page.tsx — change redirect target
- router.replace("/");
+ router.replace("/dashboard");

// auth/oauth/callback/page.tsx — same change
- router.replace("/");
+ router.replace("/dashboard");
```

### 2.4 Component Tree

```
app/(authenticated)/layout.tsx → AppLayout
├── providers/ChatProvider.tsx (NEW — ChatContext)
├── providers/LayoutProvider.tsx (NEW — LayoutContext)
├── components/Sidebar.tsx (NEW)
│   ├── Logo
│   ├── NavItem[] (Dashboard, Analytics, Docs, Admin*, Insights*)
│   ├── ThemeToggle
│   └── CollapseToggle
├── components/ChatFAB.tsx (NEW)
├── components/ChatPanel.tsx (NEW)
│   ├── ChatPanelHeader (title, tabs, agent switcher, close)
│   ├── ChatMessages (existing MessageBubble[], reused)
│   ├── ChatInput (existing, reused)
│   ├── PastSessionsTab (NEW)
│   └── ResizeHandle (NEW)
├── components/AppHeader.tsx (NEW — simplified from ChatHeader)
│   ├── Logo (mobile only — hidden on desktop, sidebar has it)
│   ├── Breadcrumb / page title
│   ├── ChatIcon (mobile — opens chat panel)
│   └── ProfileChip (existing dropdown logic, reused)
└── {children} → page content
```

### 2.5 New Components Specification

#### 2.5.1 `AppLayout` — `app/(authenticated)/layout.tsx`

```typescript
interface AppLayoutProps {
  children: React.ReactNode;
}
```

Responsibilities:
- Wraps children in `ChatProvider` and `LayoutProvider`
- Renders `Sidebar`, `AppHeader`, `ChatFAB`, `ChatPanel`
- Manages CSS grid: `sidebar | content | chat-panel`
- Applies `useAuthGuard()`

CSS grid (conceptual):
```css
.app-layout {
  display: grid;
  grid-template-columns: var(--sidebar-w) 1fr var(--chat-w, 0px);
  grid-template-rows: var(--header-h) 1fr;
  height: 100vh;
}
/* Sidebar collapsed: --sidebar-w = 62px */
/* Chat open: --chat-w = 50% of remaining */
/* Chat closed: --chat-w = 0px */
```

#### 2.5.2 `Sidebar` — `components/Sidebar.tsx`

```typescript
interface SidebarProps {
  // Reads from LayoutContext — no props needed
}
```

State from context:
- `collapsed` from `LayoutContext`
- `activeView` derived from `usePathname()`

Features:
- Expanded: 220px, icon + label
- Collapsed: 62px, icon-only with hover tooltips
- Active item: gradient left border + subtle bg
- Auto-collapses when `chatOpen` is true
- Collapse state persisted in `localStorage` key `sidebar_collapsed`
- Permission-based item visibility (reuse `canSeeItem` logic)

Nav items:
```typescript
const SIDEBAR_ITEMS = [
  { href: "/dashboard",  label: "Dashboard",  icon: GridIcon },
  { href: "/analytics",  label: "Analytics",  icon: ChartIcon },
  { href: "/docs",       label: "Docs",       icon: FileIcon },
  { href: "/admin",      label: "Admin",      icon: ShieldIcon, superuserOnly: true },
  { href: "/insights",   label: "Insights",   icon: SparkleIcon, requiresInsights: true },
];
```

Uses `next/link` for navigation (actual routes now, not view state).

#### 2.5.3 `ChatFAB` — `components/ChatFAB.tsx`

```typescript
// No props — reads/writes ChatContext
```

- 52px circular button, brand gradient bg
- Chat bubble icon (white)
- Fixed position: `bottom-6 right-6`
- `z-index: 40` (below modals, above content)
- Click: `togglePanel()` from `ChatContext`
- Hidden when chat panel is open
- Desktop: always visible
- Mobile: hidden (chat icon in header instead)
- Entrance animation: subtle scale-in on mount

#### 2.5.4 `ChatPanel` — `components/ChatPanel.tsx`

```typescript
// No props — reads ChatContext + LayoutContext
```

Structure:
```
┌──────────────────────────────┐
│ ↔ │ AI Assistant    [Gen|Stock]  ✕ │
│   ├──────────────────────────────┤
│   │ [Chat] [Past Sessions]       │
│ R ├──────────────────────────────┤
│ E │                              │
│ S │  Message bubbles...          │
│ I │                              │
│ Z ├──────────────────────────────┤
│ E │ [textarea input]       [➤]  │
│   └──────────────────────────────┘
```

Features:
- Slide-in from right edge (CSS transform + transition)
- Default width: 50% of content area (minus sidebar)
- Resizable via drag handle (left edge)
- Min width: 320px, max: 80% of content area
- Close: X button or ESC key
- Tabs: "Chat" (live) | "Past Sessions" (audit)
- Agent switcher: pill toggle (General / Stock Analysis)
- Reuses existing `MessageBubble` and `ChatInput` components
- WebSocket connection maintained even when panel closed

Resize implementation:
```typescript
// hooks/useResizePanel.ts (NEW)
export function useResizePanel(
  minWidth: number,
  maxWidth: number,
  defaultWidth: number,
) {
  const [width, setWidth] = useState(defaultWidth);
  const isDragging = useRef(false);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current = true;
    e.preventDefault();
    const onMove = (ev: MouseEvent) => {
      if (!isDragging.current) return;
      const newW = window.innerWidth - ev.clientX;
      setWidth(Math.min(maxWidth, Math.max(minWidth, newW)));
    };
    const onUp = () => {
      isDragging.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [minWidth, maxWidth]);

  return { width, onMouseDown };
}
```

#### 2.5.5 `PastSessionsTab` — `components/PastSessionsTab.tsx`

```typescript
interface PastSessionsTabProps {
  // Reads from ChatContext for user ID
}
```

- Fetches `GET /v1/audit/chat-sessions` on mount
- Displays list of session cards: date, message count, preview
- Click to expand: full transcript (read-only)
- Filters: date range picker (simple start/end inputs)
- Loading skeleton while fetching
- Error state if fetch fails
- Lazy loaded (only fetched when tab is active)

#### 2.5.6 `AppHeader` — `components/AppHeader.tsx`

```typescript
interface AppHeaderProps {
  // Minimal — reads context for most state
}
```

- Height: 56px, sticky top
- Left: hamburger (mobile), breadcrumb with current page name
- Right: chat icon (mobile only), profile chip + dropdown
- Profile dropdown reuses existing logic from `ChatHeader`

### 2.6 Dashboard Page — `app/(authenticated)/dashboard/page.tsx`

```typescript
// Native dashboard with widgets
export default function DashboardPage() {
  // Fetch dashboard data via custom hooks
  const watchlist = useWatchlist();
  const forecasts = useForecastSummary();
  const analysis  = useAnalysisLatest();
  const llmUsage  = useLLMUsage();
  const profile   = useProfile(); // existing pattern

  return (
    <div className="dashboard-grid">
      <HeroSection profile={profile} watchlist={watchlist} />
      <WatchlistWidget data={watchlist} />
      <AnalysisSignalsWidget data={analysis} />
      <LLMUsageWidget data={llmUsage} />
      <ForecastChartWidget data={forecasts} />
    </div>
  );
}
```

Layout follows Variant C mockup:
```
┌───────────────────────────────────────┐
│         Hero (full width)              │
├──────────────────┬────────────────────┤
│                  │  Analysis Signals   │
│   Watchlist      ├────────────────────┤
│   (tall)         │  LLM Usage         │
├──────────────────┴────────────────────┤
│      Forecast Chart (full width)       │
└───────────────────────────────────────┘
```

CSS Grid:
```css
.dashboard-grid {
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  gap: 16px;
  padding: 20px;
}
.hero { grid-column: 1 / -1; }
.forecast-chart { grid-column: 1 / -1; }
/* Mobile: single column */
@media (max-width: 768px) {
  .dashboard-grid { grid-template-columns: 1fr; }
}
```

### 2.7 Dashboard Widget Components

Each widget follows the same pattern:

```typescript
interface WidgetProps<T> {
  data: {
    value: T | null;
    loading: boolean;
    error: string | null;
  };
}
```

#### Widgets:

| Component | Data Hook | API Endpoint |
|-----------|-----------|-------------|
| `HeroSection` | `useProfile` + `useWatchlist` | `/v1/auth/me` + `/v1/dashboard/watchlist` |
| `WatchlistWidget` | `useWatchlist` | `GET /v1/dashboard/watchlist` |
| `AnalysisSignalsWidget` | `useAnalysisLatest` | `GET /v1/dashboard/analysis/latest` |
| `LLMUsageWidget` | `useLLMUsage` | `GET /v1/dashboard/llm-usage` |
| `ForecastChartWidget` | `useForecastSummary` | `GET /v1/dashboard/forecasts/summary` |
| `QuickActionsWidget` | — (static buttons) | Opens chat panel with pre-filled prompt |

### 2.8 New Frontend Hooks

```typescript
// hooks/useDashboardData.ts — generic fetcher pattern
function useDashboardData<T>(endpoint: string) {
  const [value, setValue] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    apiFetch(`${API_URL}${endpoint}`, { signal: controller.signal })
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(data => { setValue(data); setError(null); })
      .catch(err => {
        if (err instanceof Error && err.name === "AbortError") return;
        setError(String(err));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [endpoint]);

  return { value, loading, error };
}

// Typed wrappers
export const useWatchlist = () =>
  useDashboardData<WatchlistResponse>("/dashboard/watchlist");
export const useForecastSummary = () =>
  useDashboardData<ForecastsResponse>("/dashboard/forecasts/summary");
export const useAnalysisLatest = () =>
  useDashboardData<AnalysisResponse>("/dashboard/analysis/latest");
export const useLLMUsage = () =>
  useDashboardData<LLMUsageResponse>("/dashboard/llm-usage");
```

```typescript
// hooks/useChatSession.ts (NEW) — replaces useChatHistory for panel
// Session lifecycle: login-to-logout, flushed to audit on logout
export function useChatSession() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId] = useState(() => crypto.randomUUID());

  // Flush to audit log on logout/unmount
  const flush = useCallback(async () => {
    if (messages.length === 0) return;
    await apiFetch(`${API_URL}/audit/chat-sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        messages: messages.map(m => ({
          role: m.role,
          content: m.content,
          timestamp: m.timestamp.toISOString(),
        })),
      }),
    }).catch(() => {}); // fire-and-forget
  }, [messages, sessionId]);

  return { messages, setMessages, sessionId, flush };
}
```

```typescript
// hooks/useResizePanel.ts (NEW) — described in §2.5.4
// hooks/usePastSessions.ts (NEW) — fetches audit log
export function usePastSessions() {
  return useDashboardData<ChatSession[]>("/audit/chat-sessions");
}
```

### 2.9 Context Providers

```typescript
// providers/ChatProvider.tsx
interface ChatContextValue {
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  isOpen: boolean;
  togglePanel: () => void;
  closePanel: () => void;
  agentId: string;
  setAgentId: (id: string) => void;
  sessionId: string;
  flush: () => Promise<void>;
}
```

```typescript
// providers/LayoutProvider.tsx
interface LayoutContextValue {
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (v: boolean) => void;
  toggleSidebar: () => void;
}
```

### 2.10 Migration Path from Existing `page.tsx`

The existing `page.tsx` is a monolith that handles everything.
Here's what moves where:

| Current location | New location |
|-----------------|--------------|
| `useAuthGuard()` | `(authenticated)/layout.tsx` |
| `view` state + `switchView()` | Replaced by Next.js routing |
| Chat state (messages, input, loading, ws) | `ChatProvider` + `ChatPanel` |
| `useChatHistory` | Replaced by `useChatSession` |
| `useSendMessage` | Moved into `ChatPanel` (unchanged) |
| `useWebSocket` | Moved into `ChatProvider` (shared) |
| `useTheme` | Stays in root layout or `AppHeader` |
| `useEditProfile` | Stays in `AppHeader` profile dropdown |
| `useChangePassword` | Stays in `AppHeader` profile dropdown |
| `useSessionManagement` | Stays in `AppHeader` profile dropdown |
| `NavigationMenu` | Replaced by `Sidebar` |
| `ChatHeader` | Replaced by `AppHeader` (simplified) |
| `MessageBubble` | Reused in `ChatPanel` (unchanged) |
| `ChatInput` | Reused in `ChatPanel` (unchanged) |
| `IFrameView` | Reused in `/analytics`, `/docs`, `/admin`, `/insights` pages |
| Modals (profile, password, sessions) | Moved to `AppHeader` or layout |

**Note:** `page.tsx` remains but becomes a redirect to `/dashboard`:
```typescript
// app/page.tsx
import { redirect } from "next/navigation";
export default function RootPage() { redirect("/dashboard"); }
```

---

## 3. Backend Architecture

### 3.1 New Router — `backend/dashboard_routes.py`

```python
"""Dashboard API endpoints.

Provides aggregated data for the native Next.js dashboard widgets:
watchlist, forecasts, analysis signals, and LLM usage summary.
"""

import logging
from fastapi import APIRouter, Depends
from auth.dependencies import get_current_user, superuser_only

_logger = logging.getLogger(__name__)


def create_dashboard_router(
    stock_repo,
    obs_collector=None,
) -> APIRouter:
    """Factory — injected dependencies via closure."""
    router = APIRouter(
        prefix="/dashboard",
        tags=["dashboard"],
    )

    @router.get("/watchlist")
    async def get_watchlist(
        user=Depends(get_current_user),
    ):
        """User's linked tickers + latest OHLCV."""
        ...

    @router.get("/forecasts/summary")
    async def get_forecasts_summary(
        user=Depends(get_current_user),
    ):
        """Latest forecast runs per linked ticker."""
        ...

    @router.get("/analysis/latest")
    async def get_analysis_latest(
        user=Depends(get_current_user),
    ):
        """Latest analysis summary + signals per ticker."""
        ...

    @router.get("/llm-usage")
    async def get_llm_usage(
        user=Depends(get_current_user),
    ):
        """LLM usage stats (own usage for users, all for superusers)."""
        ...

    return router
```

### 3.2 New Router — `backend/audit_routes.py`

```python
"""Chat audit log endpoints.

Stores and retrieves chat session transcripts for the audit trail.
Partition-isolated by user_id in Iceberg.
"""

def create_audit_router(stock_repo) -> APIRouter:
    router = APIRouter(
        prefix="/audit",
        tags=["audit"],
    )

    @router.post("/chat-sessions")
    async def save_chat_session(
        body: ChatSessionCreate,
        user=Depends(get_current_user),
    ):
        """Flush chat transcript on logout."""
        ...

    @router.get("/chat-sessions")
    async def list_chat_sessions(
        start_date: str | None = None,
        end_date: str | None = None,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
        user=Depends(get_current_user),
    ):
        """List user's own past chat sessions."""
        ...

    return router
```

### 3.3 Router Registration

```python
# In routes.py:create_app() — add after existing routers:
from dashboard_routes import create_dashboard_router
from audit_routes import create_audit_router

dashboard_router = create_dashboard_router(
    stock_repo=stock_repo,
    obs_collector=obs_collector,
)
audit_router = create_audit_router(stock_repo=stock_repo)

v1_router.include_router(dashboard_router)
v1_router.include_router(audit_router)
```

### 3.4 Pydantic Response Models — `backend/dashboard_models.py`

```python
"""Response models for dashboard endpoints."""

from pydantic import BaseModel


class TickerPrice(BaseModel):
    ticker: str
    company_name: str | None
    current_price: float
    previous_close: float
    change: float
    change_pct: float
    sparkline: list[float]  # last 30 close prices


class WatchlistResponse(BaseModel):
    tickers: list[TickerPrice]
    portfolio_value: float | None
    daily_change: float | None
    daily_change_pct: float | None


class ForecastTarget(BaseModel):
    horizon_months: int
    target_date: str
    target_price: float
    pct_change: float
    lower_bound: float
    upper_bound: float


class TickerForecast(BaseModel):
    ticker: str
    run_date: str
    current_price: float
    sentiment: str | None
    targets: list[ForecastTarget]
    mae: float | None
    rmse: float | None


class ForecastsResponse(BaseModel):
    forecasts: list[TickerForecast]


class SignalInfo(BaseModel):
    name: str
    value: float | str
    signal: str         # "Bullish" | "Bearish" | "Neutral"
    description: str


class TickerAnalysis(BaseModel):
    ticker: str
    analysis_date: str
    signals: list[SignalInfo]
    sharpe_ratio: float | None
    annualized_return_pct: float | None
    annualized_volatility_pct: float | None
    max_drawdown_pct: float | None


class AnalysisResponse(BaseModel):
    analyses: list[TickerAnalysis]


class ModelUsage(BaseModel):
    model: str
    provider: str
    request_count: int
    total_tokens: int
    estimated_cost_usd: float


class LLMUsageResponse(BaseModel):
    total_requests: int
    total_cost_usd: float
    avg_latency_ms: float | None
    models: list[ModelUsage]
    daily_trend: list[dict]  # [{date, requests, cost}]


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: str
    agent_id: str | None = None


class ChatSessionCreate(BaseModel):
    session_id: str
    messages: list[ChatMessage]


class ChatSessionSummary(BaseModel):
    session_id: str
    started_at: str
    ended_at: str
    message_count: int
    preview: str          # first 150 chars of first assistant message
    agent_ids_used: list[str]


class ChatSessionDetail(ChatSessionSummary):
    messages: list[ChatMessage]
```

### 3.5 Iceberg Query Patterns for Dashboard Endpoints

```python
# GET /v1/dashboard/watchlist
# 1. Get user's tickers from auth repo
tickers = user_repo.get_user_tickers(user.id)
# 2. For each ticker, get latest OHLCV + company_info
for t in tickers:
    ohlcv = stock_repo.get_ohlcv(t)       # last 30 rows
    info = stock_repo.get_company_info(t)   # latest snapshot
    # Compute change from last 2 rows

# GET /v1/dashboard/forecasts/summary
# Scan forecast_runs WHERE ticker IN user_tickers
# AND run_date = MAX(run_date) per ticker
df = stock_repo.get_latest_forecast_runs(tickers)

# GET /v1/dashboard/analysis/latest
# Scan analysis_summary WHERE ticker IN user_tickers
# AND analysis_date = MAX(analysis_date) per ticker
df = stock_repo.get_latest_analysis(tickers)

# GET /v1/dashboard/llm-usage
# Scan llm_usage WHERE user_id = current_user
# GROUP BY model, provider → aggregate tokens, cost
# Also group by date for daily_trend
df = stock_repo.get_llm_usage_summary(
    user_id=user.id if not superuser else None,
)
```

### 3.6 New Iceberg Table — `stocks.chat_audit_log`

```python
# Schema definition for PyIceberg
from pyiceberg.schema import Schema
from pyiceberg.types import (
    StringType, TimestampType, IntegerType, NestedField,
)

CHAT_AUDIT_LOG_SCHEMA = Schema(
    NestedField(1, "session_id", StringType(), required=True),
    NestedField(2, "user_id", StringType(), required=True),
    NestedField(3, "started_at", TimestampType()),
    NestedField(4, "ended_at", TimestampType()),
    NestedField(5, "message_count", IntegerType()),
    NestedField(6, "messages_json", StringType()),  # JSON array
    NestedField(7, "agent_ids_used", StringType()),  # JSON array
    NestedField(8, "ip_address", StringType()),
    NestedField(9, "user_agent", StringType()),
    NestedField(10, "created_at", TimestampType()),
)

# Partition by user_id for query isolation
from pyiceberg.partitioning import PartitionSpec, PartitionField
from pyiceberg.transforms import IdentityTransform

CHAT_AUDIT_PARTITION = PartitionSpec(
    PartitionField(
        source_id=2,  # user_id
        field_id=1000,
        transform=IdentityTransform(),
        name="user_id_partition",
    )
)
```

### 3.7 New Repository Methods

Add to `stocks/repository.py`:

```python
def get_latest_ohlcv(
    self, ticker: str, limit: int = 30,
) -> pd.DataFrame:
    """Last N rows of OHLCV for sparkline/price data."""

def get_latest_company_info(
    self, ticker: str,
) -> dict | None:
    """Most recent company_info snapshot."""

def get_latest_forecast_runs(
    self, tickers: list[str],
) -> pd.DataFrame:
    """Latest forecast_runs per ticker."""

def get_latest_analysis(
    self, tickers: list[str],
) -> pd.DataFrame:
    """Latest analysis_summary per ticker."""

def get_llm_usage_summary(
    self, user_id: str | None = None,
    days: int = 30,
) -> dict:
    """Aggregated LLM usage with daily breakdown."""

def save_chat_session(self, session: dict) -> None:
    """Append chat transcript to chat_audit_log."""

def list_chat_sessions(
    self, user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    keyword: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Query chat_audit_log filtered by user_id."""
```

---

## 4. Data Flow Diagrams

### 4.1 Post-Login Flow

```
User logs in
    → JWT issued (access + refresh)
    → router.replace("/dashboard")
    → Next.js middleware: / → /dashboard
    → (authenticated)/layout.tsx mounts
        → useAuthGuard() validates JWT
        → ChatProvider initializes (empty messages, new sessionId)
        → LayoutProvider initializes (sidebar expanded)
        → Sidebar renders nav items
        → /dashboard/page.tsx renders
            → useWatchlist() → GET /v1/dashboard/watchlist
            → useForecastSummary() → GET /v1/dashboard/forecasts/summary
            → useAnalysisLatest() → GET /v1/dashboard/analysis/latest
            → useLLMUsage() → GET /v1/dashboard/llm-usage
            → Widgets render with loading skeletons → then data
```

### 4.2 Chat Panel Flow

```
User clicks Chat FAB (or mobile header icon)
    → ChatContext.togglePanel()
    → LayoutContext: sidebar auto-collapses
    → ChatPanel slides in (CSS transition, 300ms)
    → If first open: WebSocket connects + authenticates
    → User types message → Enter
        → useSendMessage: WS streaming (or HTTP fallback)
        → Messages append in ChatContext
    → User clicks close (X or ESC)
        → ChatPanel slides out
        → Sidebar restores previous state
        → Messages preserved in ChatContext
```

### 4.3 Logout / Session Flush

```
User clicks "Sign out" in profile dropdown
    → ChatContext.flush() fires
        → POST /v1/audit/chat-sessions (fire-and-forget)
            → Backend writes to stocks.chat_audit_log
    → clearTokens()
    → router.replace("/login")
    → ChatProvider unmounts (state cleared)
```

---

## 5. Responsive Design

### 5.1 Breakpoints

| Breakpoint | Sidebar | Chat Panel | Dashboard Grid |
|------------|---------|------------|----------------|
| < 768px (mobile) | Hidden (hamburger drawer) | Full-width overlay | Single column |
| 768–1024px (tablet) | Icon-rail (62px) | 50% width | 2 columns |
| > 1024px (desktop) | Full (220px) | 50% of remaining | 2 columns (1.1fr/0.9fr) |

### 5.2 Mobile-Specific Behavior

- Sidebar: slide-out drawer from left (existing pattern)
- Chat: opens as full-screen overlay (no split)
- Chat trigger: icon button in `AppHeader` (not FAB)
- Dashboard: widgets stack vertically
- Hero: quick actions wrap to 2x2 grid

---

## 6. Animation & Transitions

| Element | Trigger | Animation | Duration |
|---------|---------|-----------|----------|
| Chat panel | Open/close | `translateX(100%)` → `translateX(0)` | 300ms ease |
| Sidebar | Collapse/expand | Width transition | 300ms cubic-bezier |
| FAB | Page load | Scale 0 → 1 with bounce | 400ms |
| FAB | Chat open | Fade out + scale down | 200ms |
| Widget skeletons | Data loading | Pulse animation | — |
| Sidebar nav pill | Route change | Slide to active item | 200ms |
| Theme toggle | Click | Smooth color transitions on all vars | 350ms |

---

## 7. TypeScript Types (Shared)

```typescript
// lib/constants.tsx — updated
export type View =
  | "dashboard"
  | "analytics"  // renamed from "dashboard" (Plotly Dash)
  | "docs"
  | "admin"
  | "insights";

// lib/types.ts (NEW)
export interface WatchlistResponse { ... }
export interface ForecastsResponse { ... }
export interface AnalysisResponse { ... }
export interface LLMUsageResponse { ... }
export interface ChatSession { ... }
// (mirrors backend Pydantic models)
```

---

## 8. File Inventory (New + Modified)

### New Files

| File | Purpose |
|------|---------|
| `frontend/middleware.ts` | Root → /dashboard redirect |
| `frontend/app/(authenticated)/layout.tsx` | AppLayout shell |
| `frontend/app/(authenticated)/dashboard/page.tsx` | Native dashboard |
| `frontend/app/(authenticated)/docs/page.tsx` | Docs iframe |
| `frontend/app/(authenticated)/analytics/page.tsx` | Plotly Dash iframe |
| `frontend/app/(authenticated)/admin/page.tsx` | Admin iframe |
| `frontend/app/(authenticated)/insights/page.tsx` | Insights iframe |
| `frontend/providers/ChatProvider.tsx` | Chat context |
| `frontend/providers/LayoutProvider.tsx` | Layout context |
| `frontend/components/Sidebar.tsx` | Sidebar nav |
| `frontend/components/ChatFAB.tsx` | Floating action button |
| `frontend/components/ChatPanel.tsx` | Resizable chat drawer |
| `frontend/components/ChatPanelHeader.tsx` | Panel header + tabs |
| `frontend/components/PastSessionsTab.tsx` | Audit log viewer |
| `frontend/components/ResizeHandle.tsx` | Drag-to-resize bar |
| `frontend/components/AppHeader.tsx` | Simplified top header |
| `frontend/components/widgets/HeroSection.tsx` | Portfolio hero |
| `frontend/components/widgets/WatchlistWidget.tsx` | Ticker table |
| `frontend/components/widgets/AnalysisSignalsWidget.tsx` | Signal cards |
| `frontend/components/widgets/LLMUsageWidget.tsx` | Usage stats |
| `frontend/components/widgets/ForecastChartWidget.tsx` | SVG chart |
| `frontend/components/widgets/QuickActionsWidget.tsx` | Action buttons |
| `frontend/components/widgets/WidgetSkeleton.tsx` | Loading skeleton |
| `frontend/hooks/useDashboardData.ts` | Generic data fetcher |
| `frontend/hooks/useChatSession.ts` | Session lifecycle |
| `frontend/hooks/useResizePanel.ts` | Panel resize |
| `frontend/hooks/usePastSessions.ts` | Audit log fetcher |
| `frontend/lib/types.ts` | Shared response types |
| `backend/dashboard_routes.py` | Dashboard API endpoints |
| `backend/dashboard_models.py` | Pydantic response models |
| `backend/audit_routes.py` | Chat audit endpoints |

### Modified Files

| File | Change |
|------|--------|
| `frontend/app/page.tsx` | Replace with redirect to /dashboard |
| `frontend/app/login/page.tsx` | Redirect target: `/dashboard` |
| `frontend/app/auth/oauth/callback/page.tsx` | Redirect target: `/dashboard` |
| `frontend/lib/constants.tsx` | Update `View` type, rename "dashboard" → "analytics" |
| `backend/routes.py` | Register dashboard + audit routers |
| `stocks/repository.py` | Add new query methods + chat_audit_log table |

### Removed/Deprecated

| File | Status |
|------|--------|
| `frontend/components/NavigationMenu.tsx` | Replaced by `Sidebar.tsx` |
| `frontend/hooks/useChatHistory.ts` | Replaced by `useChatSession.ts` |

---

## 9. Security Considerations

| Concern | Mitigation |
|---------|-----------|
| Chat audit data isolation | Iceberg partitioned by `user_id`; all queries filtered by authenticated user |
| Admin-only endpoints | `Depends(superuser_only)` on LLM usage (all-users view) |
| XSS in chat messages | `react-markdown` with sanitized output (existing) |
| JWT in dashboard requests | All use `apiFetch` with auto-refresh (existing) |
| Audit log size | `messages_json` capped at session scope; old sessions queryable but not loaded in bulk |
| CORS | Already configured for localhost:3000 (existing) |

---

## 10. Testing Strategy

| Layer | What to test | Approach |
|-------|-------------|----------|
| Backend endpoints | 6 new endpoints | pytest: happy path + auth + empty data |
| Iceberg queries | New repository methods | pytest with test catalog |
| Frontend hooks | `useDashboardData`, `useChatSession`, `useResizePanel` | vitest + mock apiFetch |
| Frontend components | Sidebar, ChatPanel, widgets | vitest + React Testing Library |
| E2E | Login → dashboard → open chat → send message → logout | Playwright |
| Responsive | Mobile/tablet/desktop breakpoints | Playwright viewport tests |

---

## 11. Next Steps

1. `/sc:workflow` — generate implementation plan with task breakdown
2. Sprint 1 (remaining): Foundation (routing, layout, sidebar, chat panel)
3. Sprint 2: Full build (widgets, backend endpoints, audit, polish)
