/**
 * Shared types, constants, and pure utility functions for the chat page.
 *
 * Extracted from page.tsx so hooks and components can import them without
 * creating circular dependencies.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type View = "chat" | "docs" | "dashboard" | "insights" | "admin";

export interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

// ---------------------------------------------------------------------------
// Agent configuration
// ---------------------------------------------------------------------------

export const AGENTS = [
  { id: "general", label: "General", hint: "Ask me anything — I can search the web or check the time." },
  { id: "stock",   label: "Stock Analysis", hint: 'Try: "Analyse AAPL" · "Forecast TSLA for 6 months" · "Compare AAPL and MSFT"' },
] as const;

// ---------------------------------------------------------------------------
// Navigation items
// ---------------------------------------------------------------------------

import { type ReactNode } from "react";

export interface NavItem {
  view: View;
  label: string;
  superuserOnly?: boolean;
  requiresInsights?: boolean;
  icon: ReactNode;
}

export const NAV_ITEMS: NavItem[] = [
  {
    view: "chat",
    label: "Chat",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    ),
  },
  {
    view: "docs",
    label: "Docs",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
      </svg>
    ),
  },
  {
    view: "dashboard",
    label: "Dashboard",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M3 9h18M9 21V9" />
      </svg>
    ),
  },
  {
    view: "insights",
    label: "Insights",
    requiresInsights: true,
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="18" y1="20" x2="18" y2="10" />
        <line x1="12" y1="20" x2="12" y2="4" />
        <line x1="6" y1="20" x2="6" y2="14" />
      </svg>
    ),
  },
  {
    view: "admin",
    label: "Admin",
    superuserOnly: true,
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
        <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      </svg>
    ),
  },
];

// ---------------------------------------------------------------------------
// Pure utilities
// ---------------------------------------------------------------------------

export function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function toolLabel(name: string): string {
  const labels: Record<string, string> = {
    fetch_stock_data:     "Fetching stock data",
    get_stock_info:       "Getting stock info",
    load_stock_data:      "Loading stock data",
    fetch_multiple_stocks:"Fetching multiple stocks",
    get_dividend_history: "Getting dividend history",
    list_available_stocks:"Listing available stocks",
    analyse_stock_price:  "Analysing price",
    forecast_stock:       "Generating forecast",
    search_market_news:   "Searching market news",
    search_web:           "Searching the web",
    get_current_time:     "Checking time",
  };
  return labels[name] ?? `Calling ${name}`;
}
