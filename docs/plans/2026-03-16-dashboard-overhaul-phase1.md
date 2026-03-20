# Dashboard UI Overhaul — Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the chat-first landing page with a native dashboard, add a collapsible sidebar, and move chat into a resizable FAB-triggered side panel.

**Architecture:** Next.js App Router route groups with `(authenticated)` layout containing sidebar + content + chat panel. Two new React contexts (ChatContext, LayoutContext) replace prop-drilling for cross-cutting state. Existing components (MessageBubble, ChatInput, IFrameView) are reused unchanged.

**Tech Stack:** Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4, existing WebSocket + apiFetch infrastructure.

**Jira Tickets:** ASETPLTFRM-82 through ASETPLTFRM-87

---

## Task 1: Route Structure + Middleware (ASETPLTFRM-82)

**Files:**
- Create: `frontend/middleware.ts`
- Create: `frontend/app/(authenticated)/layout.tsx`
- Create: `frontend/app/(authenticated)/dashboard/page.tsx`
- Create: `frontend/app/(authenticated)/docs/page.tsx`
- Create: `frontend/app/(authenticated)/analytics/page.tsx`
- Create: `frontend/app/(authenticated)/admin/page.tsx`
- Create: `frontend/app/(authenticated)/insights/page.tsx`
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/app/login/page.tsx:125`
- Modify: `frontend/app/auth/oauth/callback/page.tsx:105`
- Modify: `frontend/lib/constants.tsx:12,43-73`

### Step 1: Create middleware for root redirect

```typescript
// frontend/middleware.ts
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
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

### Step 2: Replace root page.tsx with server redirect

Replace entire contents of `frontend/app/page.tsx`:

```typescript
import { redirect } from "next/navigation";

export default function RootPage() {
  redirect("/dashboard");
}
```

### Step 3: Update constants.tsx — View type + nav items

In `frontend/lib/constants.tsx`:

- Change `View` type: remove `"chat"`, rename `"dashboard"` to `"analytics"`, add `"dashboard"`:
```typescript
export type View =
  | "dashboard"
  | "analytics"
  | "docs"
  | "insights"
  | "admin";
```

- Update `NavItem` interface — add `href` field for Next.js routing:
```typescript
export interface NavItem {
  view: View;
  href: string;
  label: string;
  superuserOnly?: boolean;
  requiresInsights?: boolean;
  icon: ReactNode;
}
```

- Update `NAV_ITEMS` — remove Chat, rename Dashboard → Analytics, add new Dashboard:
```typescript
export const NAV_ITEMS: NavItem[] = [
  {
    view: "dashboard",
    href: "/dashboard",
    label: "Dashboard",
    icon: (/* grid icon SVG — same as current dashboard icon */),
  },
  {
    view: "analytics",
    href: "/analytics",
    label: "Analytics",
    icon: (/* chart icon SVG */),
  },
  {
    view: "docs",
    href: "/docs",
    label: "Docs",
    icon: (/* document icon SVG — same as current */),
  },
  {
    view: "admin",
    href: "/admin",
    label: "Admin",
    superuserOnly: true,
    icon: (/* shield icon SVG */),
  },
  {
    view: "insights",
    href: "/insights",
    label: "Insights",
    requiresInsights: true,
    icon: (/* sparkle icon SVG */),
  },
];
```

### Step 4: Create authenticated layout shell (placeholder)

```typescript
// frontend/app/(authenticated)/layout.tsx
"use client";

import { useAuthGuard } from "@/hooks/useAuthGuard";

export default function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  useAuthGuard();

  return (
    <div className="flex flex-col h-screen bg-gray-50 dark:bg-gray-950 font-sans transition-colors">
      {children}
    </div>
  );
}
```

### Step 5: Create dashboard placeholder page

```typescript
// frontend/app/(authenticated)/dashboard/page.tsx
"use client";

export default function DashboardPage() {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">
          Dashboard
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-2">
          Widgets coming soon
        </p>
      </div>
    </div>
  );
}
```

### Step 6: Create iframe pages (docs, analytics, admin, insights)

Each iframe page follows the same pattern — use existing `IFrameView` component with the appropriate URL. Each page manages its own iframe loading/error state.

```typescript
// frontend/app/(authenticated)/docs/page.tsx
"use client";

import { useState, useMemo } from "react";
import { IFrameView } from "@/components/IFrameView";
import { DOCS_URL } from "@/lib/config";
import { useTheme } from "@/hooks/useTheme";

export default function DocsPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const theme = useTheme();

  const src = useMemo(() => {
    const sep = DOCS_URL.includes("?") ? "&" : "?";
    return `${DOCS_URL}${sep}theme=${theme.resolvedTheme}`;
  }, [theme.resolvedTheme]);

  return (
    <IFrameView
      src={src}
      title="Documentation"
      loading={loading}
      error={error}
      onLoad={() => setLoading(false)}
      onError={() => { setLoading(false); setError(true); }}
    />
  );
}
```

```typescript
// frontend/app/(authenticated)/analytics/page.tsx
"use client";

import { useState, useMemo } from "react";
import { IFrameView } from "@/components/IFrameView";
import { DASHBOARD_URL } from "@/lib/config";
import { getAccessToken } from "@/lib/auth";
import { useTheme } from "@/hooks/useTheme";

export default function AnalyticsPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const theme = useTheme();

  const src = useMemo(() => {
    const token = getAccessToken();
    const sep = DASHBOARD_URL.includes("?") ? "&" : "?";
    const params = token
      ? `token=${encodeURIComponent(token)}&theme=${theme.resolvedTheme}`
      : `theme=${theme.resolvedTheme}`;
    return `${DASHBOARD_URL}${sep}${params}`;
  }, [theme.resolvedTheme]);

  return (
    <IFrameView
      src={src}
      title="Analytics"
      loading={loading}
      error={error}
      onLoad={() => setLoading(false)}
      onError={() => { setLoading(false); setError(true); }}
    />
  );
}
```

```typescript
// frontend/app/(authenticated)/admin/page.tsx
"use client";

import { useState, useMemo } from "react";
import { IFrameView } from "@/components/IFrameView";
import { DASHBOARD_URL } from "@/lib/config";
import { getAccessToken } from "@/lib/auth";
import { useTheme } from "@/hooks/useTheme";

export default function AdminPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const theme = useTheme();

  const src = useMemo(() => {
    const base = `${DASHBOARD_URL}/admin/users`;
    const token = getAccessToken();
    const sep = base.includes("?") ? "&" : "?";
    const params = token
      ? `token=${encodeURIComponent(token)}&theme=${theme.resolvedTheme}`
      : `theme=${theme.resolvedTheme}`;
    return `${base}${sep}${params}`;
  }, [theme.resolvedTheme]);

  return (
    <IFrameView
      src={src}
      title="Admin"
      loading={loading}
      error={error}
      onLoad={() => setLoading(false)}
      onError={() => { setLoading(false); setError(true); }}
    />
  );
}
```

```typescript
// frontend/app/(authenticated)/insights/page.tsx
"use client";

import { useState, useMemo } from "react";
import { IFrameView } from "@/components/IFrameView";
import { DASHBOARD_URL } from "@/lib/config";
import { getAccessToken } from "@/lib/auth";
import { useTheme } from "@/hooks/useTheme";

export default function InsightsPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const theme = useTheme();

  const src = useMemo(() => {
    const base = `${DASHBOARD_URL}/insights`;
    const token = getAccessToken();
    const sep = base.includes("?") ? "&" : "?";
    const params = token
      ? `token=${encodeURIComponent(token)}&theme=${theme.resolvedTheme}`
      : `theme=${theme.resolvedTheme}`;
    return `${base}${sep}${params}`;
  }, [theme.resolvedTheme]);

  return (
    <IFrameView
      src={src}
      title="Insights"
      loading={loading}
      error={error}
      onLoad={() => setLoading(false)}
      onError={() => { setLoading(false); setError(true); }}
    />
  );
}
```

### Step 7: Update login redirect targets

In `frontend/app/login/page.tsx:125`: Change `router.replace("/")` → `router.replace("/dashboard")`

Also line 29 (already-authenticated redirect): Change `router.replace("/")` → `router.replace("/dashboard")`

In `frontend/app/auth/oauth/callback/page.tsx:105`: Change `router.replace("/")` → `router.replace("/dashboard")`

### Step 8: Verify and commit

Run: `cd frontend && npx next build` (should compile without errors)

Manual test: Start frontend → navigate to `localhost:3000` → should redirect to `/dashboard` → shows placeholder. Navigate to `/docs`, `/analytics` → iframes should load.

```bash
git add frontend/middleware.ts \
  frontend/app/page.tsx \
  "frontend/app/(authenticated)" \
  frontend/app/login/page.tsx \
  frontend/app/auth/oauth/callback/page.tsx \
  frontend/lib/constants.tsx
git commit -m "feat: route structure + middleware for dashboard overhaul (ASETPLTFRM-82)"
```

---

## Task 2: Context Providers (ASETPLTFRM-83)

**Files:**
- Create: `frontend/providers/ChatProvider.tsx`
- Create: `frontend/providers/LayoutProvider.tsx`
- Modify: `frontend/app/(authenticated)/layout.tsx`

### Step 1: Create ChatProvider

```typescript
// frontend/providers/ChatProvider.tsx
"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import type { Message } from "@/lib/constants";
import { useWebSocket, type UseWebSocketReturn } from "@/hooks/useWebSocket";

interface ChatContextValue {
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  isOpen: boolean;
  togglePanel: () => void;
  closePanel: () => void;
  openPanel: () => void;
  agentId: string;
  setAgentId: (id: string) => void;
  sessionId: string;
  ws: UseWebSocketReturn;
}

const ChatContext = createContext<ChatContextValue | null>(null);

export function ChatProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [agentId, setAgentId] = useState("general");
  const [sessionId] = useState(() => crypto.randomUUID());
  const ws = useWebSocket();

  const togglePanel = useCallback(() => setIsOpen((v) => !v), []);
  const closePanel = useCallback(() => setIsOpen(false), []);
  const openPanel = useCallback(() => setIsOpen(true), []);

  return (
    <ChatContext.Provider
      value={{
        messages,
        setMessages,
        isOpen,
        togglePanel,
        closePanel,
        openPanel,
        agentId,
        setAgentId,
        sessionId,
        ws,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}

export function useChatContext(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) {
    throw new Error("useChatContext must be used within ChatProvider");
  }
  return ctx;
}
```

### Step 2: Create LayoutProvider

```typescript
// frontend/providers/LayoutProvider.tsx
"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from "react";

interface LayoutContextValue {
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (v: boolean) => void;
  toggleSidebar: () => void;
  mobileMenuOpen: boolean;
  setMobileMenuOpen: (v: boolean | ((prev: boolean) => boolean)) => void;
}

const LayoutContext = createContext<LayoutContextValue | null>(null);

const STORAGE_KEY = "sidebar_collapsed";

function getStoredCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(STORAGE_KEY) === "true";
}

export function LayoutProvider({ children }: { children: ReactNode }) {
  const [sidebarCollapsed, setSidebarCollapsedRaw] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const hydrated = useRef(false);

  // Hydrate from localStorage once
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    setSidebarCollapsedRaw(getStoredCollapsed());
  }, []);

  const setSidebarCollapsed = useCallback((v: boolean) => {
    setSidebarCollapsedRaw(v);
    localStorage.setItem(STORAGE_KEY, String(v));
  }, []);

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsedRaw((prev) => {
      const next = !prev;
      localStorage.setItem(STORAGE_KEY, String(next));
      return next;
    });
  }, []);

  return (
    <LayoutContext.Provider
      value={{
        sidebarCollapsed,
        setSidebarCollapsed,
        toggleSidebar,
        mobileMenuOpen,
        setMobileMenuOpen,
      }}
    >
      {children}
    </LayoutContext.Provider>
  );
}

export function useLayoutContext(): LayoutContextValue {
  const ctx = useContext(LayoutContext);
  if (!ctx) {
    throw new Error("useLayoutContext must be used within LayoutProvider");
  }
  return ctx;
}
```

### Step 3: Wire providers into authenticated layout

Update `frontend/app/(authenticated)/layout.tsx`:

```typescript
"use client";

import { useAuthGuard } from "@/hooks/useAuthGuard";
import { ChatProvider } from "@/providers/ChatProvider";
import { LayoutProvider } from "@/providers/LayoutProvider";

export default function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  useAuthGuard();

  return (
    <LayoutProvider>
      <ChatProvider>
        <div className="flex flex-col h-screen bg-gray-50 dark:bg-gray-950 font-sans transition-colors">
          {children}
        </div>
      </ChatProvider>
    </LayoutProvider>
  );
}
```

### Step 4: Commit

```bash
git add frontend/providers/ "frontend/app/(authenticated)/layout.tsx"
git commit -m "feat: ChatProvider + LayoutProvider contexts (ASETPLTFRM-83)"
```

---

## Task 3: Sidebar Navigation (ASETPLTFRM-84)

**Files:**
- Create: `frontend/components/Sidebar.tsx`

### Step 1: Build Sidebar component

The Sidebar reads from `LayoutContext` (collapsed state) and `ChatContext` (auto-collapse when chat opens). Uses `next/link` + `usePathname()` for active state. Reuses `canSeeItem` permission logic from existing `NavigationMenu.tsx`.

Key styling from Variant C mockup:
- Expanded: 220px width, icon + label
- Collapsed: 62px, icon only
- Active item: gradient left border (`border-l-2 border-violet-500`) + bg tint
- Pill-shaped active indicator with slide transition
- Theme toggle in footer
- Collapse toggle button at bottom

The component should be ~150 lines. Full implementation should:
- Import `NAV_ITEMS` from constants, `usePathname` from `next/navigation`, `Link` from `next/link`
- Import `useLayoutContext` and `useChatContext`
- Auto-collapse sidebar when `chatContext.isOpen` is true
- Store previous collapsed state before auto-collapse, restore on chat close
- Render SunIcon/MoonIcon for theme toggle (reuse SVGs from NavigationMenu)
- Support mobile: render as slide-out drawer when `mobileMenuOpen` is true

### Step 2: Commit

```bash
git add frontend/components/Sidebar.tsx
git commit -m "feat: collapsible sidebar navigation (ASETPLTFRM-84)"
```

---

## Task 4: Simplified AppHeader (ASETPLTFRM-85)

**Files:**
- Create: `frontend/components/AppHeader.tsx`

### Step 1: Build AppHeader

Simplified version of ChatHeader. Reuses:
- Profile chip + dropdown (avatar, initials, dropdown menu)
- Edit Profile, Change Password, Manage Sessions, Sign Out actions

Removes: agent switcher, clear chat button (both moved to chat panel).

Adds:
- Page title derived from `usePathname()` (capitalize first segment)
- Chat icon button on mobile (hidden on `md+`) — triggers `chatContext.togglePanel()`
- Hamburger button on mobile — triggers `layoutContext.setMobileMenuOpen()`
- ASET logo on mobile (hidden on desktop — sidebar has it)

The component should accept props for modal triggers:
```typescript
interface AppHeaderProps {
  profile: UserProfile | null;
  onEditProfile: () => void;
  onChangePassword: () => void;
  onManageSessions: () => void;
}
```

### Step 2: Commit

```bash
git add frontend/components/AppHeader.tsx
git commit -m "feat: simplified AppHeader for dashboard layout (ASETPLTFRM-85)"
```

---

## Task 5: Chat FAB + Resizable Side Panel (ASETPLTFRM-86)

**Files:**
- Create: `frontend/hooks/useResizePanel.ts`
- Create: `frontend/hooks/useChatSession.ts`
- Create: `frontend/components/ChatFAB.tsx`
- Create: `frontend/components/ResizeHandle.tsx`
- Create: `frontend/components/ChatPanelHeader.tsx`
- Create: `frontend/components/ChatPanel.tsx`

### Step 1: Create useResizePanel hook

```typescript
// frontend/hooks/useResizePanel.ts
"use client";

import { useState, useCallback, useRef } from "react";

export function useResizePanel(
  minWidth: number,
  maxWidth: number,
  defaultWidth: number,
) {
  const [width, setWidth] = useState(defaultWidth);
  const isDragging = useRef(false);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      isDragging.current = true;

      const onMove = (ev: MouseEvent) => {
        if (!isDragging.current) return;
        const newW = window.innerWidth - ev.clientX;
        setWidth(Math.min(maxWidth, Math.max(minWidth, newW)));
      };

      const onUp = () => {
        isDragging.current = false;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };

      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [minWidth, maxWidth],
  );

  return { width, onMouseDown };
}
```

### Step 2: Create useChatSession hook

Replaces `useChatHistory`. In-memory only (no localStorage). Session = login-to-logout.

```typescript
// frontend/hooks/useChatSession.ts
"use client";

import { useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { Message } from "@/lib/constants";

export function useChatSession(
  messages: Message[],
  sessionId: string,
  agentId: string,
) {
  const flush = useCallback(async () => {
    if (messages.length === 0) return;
    try {
      await apiFetch(`${API_URL}/audit/chat-sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          messages: messages.map((m) => ({
            role: m.role,
            content: m.content,
            timestamp: m.timestamp.toISOString(),
            agent_id: agentId,
          })),
        }),
      });
    } catch {
      // fire-and-forget — don't block logout
    }
  }, [messages, sessionId, agentId]);

  return { flush };
}
```

### Step 3: Create ChatFAB

```typescript
// frontend/components/ChatFAB.tsx
"use client";

import { useChatContext } from "@/providers/ChatProvider";

export function ChatFAB() {
  const { isOpen, togglePanel } = useChatContext();

  if (isOpen) return null;

  return (
    <button
      onClick={togglePanel}
      title="Open chat"
      data-testid="chat-fab"
      className="fixed bottom-6 right-6 z-40 hidden md:flex w-13 h-13 rounded-full bg-gradient-to-br from-fuchsia-500 to-violet-600 items-center justify-center text-white shadow-lg hover:shadow-xl hover:scale-105 transition-all"
    >
      <svg xmlns="http://www.w3.org/2000/svg" className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    </button>
  );
}
```

### Step 4: Create ResizeHandle

```typescript
// frontend/components/ResizeHandle.tsx
interface ResizeHandleProps {
  onMouseDown: (e: React.MouseEvent) => void;
}

export function ResizeHandle({ onMouseDown }: ResizeHandleProps) {
  return (
    <div
      onMouseDown={onMouseDown}
      className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize group hover:bg-violet-500/20 transition-colors z-10"
    >
      <div className="absolute left-0.5 top-1/2 -translate-y-1/2 w-0.5 h-8 bg-gray-300 dark:bg-gray-600 rounded-full group-hover:bg-violet-500 transition-colors" />
    </div>
  );
}
```

### Step 5: Create ChatPanelHeader

Header with: title, agent switcher pills, "Past Sessions" tab (disabled placeholder), close button.

```typescript
// frontend/components/ChatPanelHeader.tsx
"use client";

import { useChatContext } from "@/providers/ChatProvider";
import { AGENTS } from "@/lib/constants";

export function ChatPanelHeader() {
  const { agentId, setAgentId, closePanel } = useChatContext();

  return (
    <div className="border-b border-gray-200 dark:border-gray-700 px-4 py-3 shrink-0">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          AI Assistant
        </h2>
        <button
          onClick={closePanel}
          className="w-8 h-8 flex items-center justify-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg"
          aria-label="Close chat"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      {/* Agent switcher */}
      <div className="flex items-center gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-0.5">
        {AGENTS.map((a) => (
          <button
            key={a.id}
            onClick={() => setAgentId(a.id)}
            className={`text-xs px-3 py-1.5 rounded-md font-medium transition-colors whitespace-nowrap ${
              agentId === a.id
                ? "bg-white dark:bg-gray-700 text-indigo-700 dark:text-indigo-400 shadow-sm"
                : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            }`}
          >
            {a.label}
          </button>
        ))}
      </div>
    </div>
  );
}
```

### Step 6: Create ChatPanel

Composes header, messages, input, resize handle. Reads from ChatContext. Uses `useSendMessage` hook (existing, unchanged). Slides in from right.

The ChatPanel component should:
- Use `useResizePanel` for drag-to-resize
- Render `ChatPanelHeader` at top
- Render message list (`MessageBubble` for each message)
- Render `ChatInput` at bottom
- Render `ResizeHandle` on left edge
- Handle ESC key to close
- Animate with `transform: translateX` transition
- Use `useSendMessage` with context values (messages, setMessages, agentId, ws)

### Step 7: Commit

```bash
git add frontend/hooks/useResizePanel.ts \
  frontend/hooks/useChatSession.ts \
  frontend/components/ChatFAB.tsx \
  frontend/components/ResizeHandle.tsx \
  frontend/components/ChatPanelHeader.tsx \
  frontend/components/ChatPanel.tsx
git commit -m "feat: Chat FAB + resizable side panel (ASETPLTFRM-86)"
```

---

## Task 6: Layout Integration + Cleanup (ASETPLTFRM-87)

**Files:**
- Modify: `frontend/app/(authenticated)/layout.tsx`

### Step 1: Assemble full layout

Update `frontend/app/(authenticated)/layout.tsx` to compose everything:

```typescript
"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useAuthGuard } from "@/hooks/useAuthGuard";
import { ChatProvider, useChatContext } from "@/providers/ChatProvider";
import { LayoutProvider } from "@/providers/LayoutProvider";
import { useTheme } from "@/hooks/useTheme";
import { useEditProfile, type UserProfile } from "@/hooks/useEditProfile";
import { useChangePassword } from "@/hooks/useChangePassword";
import { useSessionManagement } from "@/hooks/useSessionManagement";
import { Sidebar } from "@/components/Sidebar";
import { AppHeader } from "@/components/AppHeader";
import { ChatPanel } from "@/components/ChatPanel";
import { ChatFAB } from "@/components/ChatFAB";
import { EditProfileModal } from "@/components/EditProfileModal";
import { ChangePasswordModal } from "@/components/ChangePasswordModal";
import { SessionManagementModal } from "@/components/SessionManagementModal";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { getSessionIdFromToken } from "@/lib/auth";

function AuthenticatedShell({ children }: { children: React.ReactNode }) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const editProfile = useEditProfile();
  const changePassword = useChangePassword();
  const sessionMgmt = useSessionManagement();

  // Fetch profile on mount
  useEffect(() => {
    const controller = new AbortController();
    apiFetch(`${API_URL}/auth/me`, { signal: controller.signal })
      .then((res) => res.ok ? res.json() : null)
      .then((data: UserProfile | null) => { if (data) setProfile(data); })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
      });
    return () => controller.abort();
  }, []);

  const handleEditProfileSave = async (fullName: string, avatarFile: File | null) => {
    const updated = await editProfile.save(fullName, avatarFile);
    if (updated) setProfile(updated);
  };

  const handleChangePasswordSave = async (newPw: string, confirmPw: string) => {
    await changePassword.save(profile?.email ?? "", newPw, confirmPw);
  };

  return (
    <div className="flex h-screen bg-gray-50 dark:bg-gray-950 font-sans transition-colors overflow-hidden">
      <Sidebar profile={profile} />
      <div className="flex flex-col flex-1 min-w-0">
        <AppHeader
          profile={profile}
          onEditProfile={editProfile.open}
          onChangePassword={changePassword.open}
          onManageSessions={sessionMgmt.open}
        />
        <main className="flex-1 overflow-y-auto">
          {children}
        </main>
      </div>
      <ChatPanel />
      <ChatFAB />

      <EditProfileModal
        isOpen={editProfile.isOpen}
        profile={profile}
        saving={editProfile.saving}
        error={editProfile.error}
        onClose={editProfile.close}
        onSave={handleEditProfileSave}
      />
      <ChangePasswordModal
        isOpen={changePassword.isOpen}
        saving={changePassword.saving}
        error={changePassword.error}
        onClose={changePassword.close}
        onSave={handleChangePasswordSave}
      />
      <SessionManagementModal
        isOpen={sessionMgmt.isOpen}
        sessions={sessionMgmt.sessions}
        loading={sessionMgmt.loading}
        revoking={sessionMgmt.revoking}
        revokingAll={sessionMgmt.revokingAll}
        error={sessionMgmt.error}
        currentSessionId={getSessionIdFromToken()}
        onClose={sessionMgmt.close}
        onRevoke={sessionMgmt.revokeSession}
        onRevokeAll={sessionMgmt.revokeAllSessions}
      />
    </div>
  );
}

export default function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  useAuthGuard();

  return (
    <LayoutProvider>
      <ChatProvider>
        <AuthenticatedShell>{children}</AuthenticatedShell>
      </ChatProvider>
    </LayoutProvider>
  );
}
```

### Step 2: Verify end-to-end

Run: `cd frontend && npx next build`

Manual test checklist:
- [ ] Login → redirects to `/dashboard`
- [ ] Sidebar renders with nav items
- [ ] Click sidebar items → navigates between routes
- [ ] Sidebar collapse/expand works
- [ ] Click Chat FAB → panel slides in
- [ ] Send message → streaming response
- [ ] Resize panel by dragging
- [ ] ESC closes panel
- [ ] Sidebar auto-collapses when chat opens
- [ ] Navigate routes → chat persists
- [ ] Profile dropdown → all modals work
- [ ] Dark/light mode toggle works
- [ ] Mobile: hamburger + chat icon in header

### Step 3: Commit

```bash
git add "frontend/app/(authenticated)/layout.tsx"
git commit -m "feat: full layout integration — sidebar + chat panel + FAB (ASETPLTFRM-87)"
```

---

## Summary of all files created/modified

### New files (17):
1. `frontend/middleware.ts`
2. `frontend/app/(authenticated)/layout.tsx`
3. `frontend/app/(authenticated)/dashboard/page.tsx`
4. `frontend/app/(authenticated)/docs/page.tsx`
5. `frontend/app/(authenticated)/analytics/page.tsx`
6. `frontend/app/(authenticated)/admin/page.tsx`
7. `frontend/app/(authenticated)/insights/page.tsx`
8. `frontend/providers/ChatProvider.tsx`
9. `frontend/providers/LayoutProvider.tsx`
10. `frontend/components/Sidebar.tsx`
11. `frontend/components/AppHeader.tsx`
12. `frontend/components/ChatFAB.tsx`
13. `frontend/components/ResizeHandle.tsx`
14. `frontend/components/ChatPanelHeader.tsx`
15. `frontend/components/ChatPanel.tsx`
16. `frontend/hooks/useResizePanel.ts`
17. `frontend/hooks/useChatSession.ts`

### Modified files (4):
1. `frontend/app/page.tsx` — replaced with redirect
2. `frontend/app/login/page.tsx` — redirect target
3. `frontend/app/auth/oauth/callback/page.tsx` — redirect target
4. `frontend/lib/constants.tsx` — View type + nav items
