/**
 * Shared types, constants, and pure utility functions for the chat page.
 *
 * Extracted from page.tsx so hooks and components can import them without
 * creating circular dependencies.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type View =
  | "dashboard"
  | "analytics"
  | "docs"
  | "admin";

export interface ActionButton {
  label: string;
  prompt: string;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  actions?: ActionButton[];
  memoryUsed?: boolean;
}

// ---------------------------------------------------------------------------
// Agent configuration
// ---------------------------------------------------------------------------

/** Default hint shown in the empty chat state. */
export const CHAT_HINT =
  'Ask me anything — I can search the web or check the time.' +
  ' Try: "Analyse AAPL" · "Forecast TSLA for 6 months"';

// ---------------------------------------------------------------------------
// Navigation items
// ---------------------------------------------------------------------------

import { type ReactNode } from "react";

export interface NavItem {
  view: View;
  href: string;
  label: string;
  superuserOnly?: boolean;
  requiresInsights?: boolean;
  icon: ReactNode;
  children?: NavItem[];
}

export const NAV_ITEMS: NavItem[] = [
  {
    view: "dashboard",
    href: "/dashboard",
    label: "Portfolio",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M3 9h18M9 21V9" />
      </svg>
    ),
  },
  {
    view: "analytics",
    href: "/analytics",
    label: "Dashboard",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="18" y1="20" x2="18" y2="10" />
        <line x1="12" y1="20" x2="12" y2="4" />
        <line x1="6" y1="20" x2="6" y2="14" />
      </svg>
    ),
    children: [
      {
        view: "analytics",
        href: "/analytics",
        label: "Home",
        icon: (
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
            <polyline points="9 22 9 12 15 12 15 22" />
          </svg>
        ),
      },
      {
        view: "analytics",
        href: "/analytics/analysis",
        label: "Analysis",
        icon: (
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
          </svg>
        ),
      },
      {
        view: "analytics",
        href: "/analytics/insights",
        label: "Insights",
        requiresInsights: true,
        icon: (
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg>
        ),
      },
    ],
  },
  {
    view: "docs",
    href: "/docs",
    label: "Docs",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
      </svg>
    ),
  },
  {
    view: "admin",
    href: "/admin",
    label: "Admin",
    superuserOnly: true,
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
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
