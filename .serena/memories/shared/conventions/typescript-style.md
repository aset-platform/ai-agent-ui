# TypeScript / Frontend Style Conventions

## Component Patterns
- `"use client"` directive on all client components
- Named exports for components (not default, except page.tsx)
- Props interfaces defined above component
- Callback-driven: props flow down, callbacks up
- `React.memo` for list items
- Modals: `if (!isOpen) return null`

## Data Fetching
- **Always use `apiFetch`** not bare `fetch` — auto-refreshes JWT
- AbortController cleanup in useEffect
- `useDashboardData<T>` generic hook for dashboard widgets
- Loading/error/value pattern: `DashboardData<T>`

## Images
- **Always use `<Image />` from next/image**, never `<img>` (ESLint enforced)

## Styling
- Tailwind CSS 4 with `dark:` class variants
- `dark` class on `<html>` toggled by useTheme hook
- Common patterns: `bg-white dark:bg-gray-900`, `text-gray-900 dark:text-gray-100`

## Charts (react-plotly.js)
- Use `PlotlyChart` wrapper from `@/components/charts/PlotlyChart`
- Dynamic import with `ssr: false` (plotly needs window)
- Auto dark/light theming via `useTheme()`
- Chart builders in `chartBuilders.ts` for reusable trace patterns
- Unified subplot approach for linked charts (shared x-axis)

## Currency
- `tickerCurrency(ticker)` helper: `.NS`/`.BO` → ₹, else $
- Never hardcode `$` — always derive from ticker/market

## SSR Safety
- Layout uses `mounted` guard (spinner on server, full app on client)
- No `crypto.randomUUID()` in useState — use useRef with typeof window guard
- `toLocaleString("en-US")` with explicit locale to prevent server/client mismatch

## Context Providers
- ChatProvider: messages, panel state, WebSocket, sessionId, flush
- LayoutProvider: sidebar collapsed, mobile menu
- Both in `(authenticated)/layout.tsx`
