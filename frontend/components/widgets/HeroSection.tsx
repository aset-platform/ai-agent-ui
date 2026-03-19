"use client";

import type { DashboardData } from "@/hooks/useDashboardData";
import type { WatchlistResponse } from "@/lib/types";
import type { UserProfile } from "@/hooks/useEditProfile";
import type { MarketFilter } from "@/app/(authenticated)/dashboard/page";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";

interface HeroSectionProps {
  watchlist: DashboardData<WatchlistResponse>;
  profile: UserProfile | null;
  marketFilter: MarketFilter;
  onMarketFilterChange: (f: MarketFilter) => void;
  onQuickAction: (prompt: string) => void;
  /** Portfolio totals per currency from usePortfolio. */
  portfolioTotals?: Record<string, number>;
  portfolioHoldingsCount?: number;
}

const quickActions = [
  { label: "Analyze", prompt: "Analyze AAPL", primary: true },
  { label: "Forecast", prompt: "Forecast AAPL", primary: false },
  { label: "Compare", prompt: "Compare AAPL vs MSFT", primary: false },
  {
    label: "Link Ticker",
    prompt: "Link ticker AAPL",
    primary: false,
  },
] as const;

export function HeroSection({
  watchlist,
  profile,
  marketFilter,
  onMarketFilterChange,
  onQuickAction,
  portfolioTotals = {},
  portfolioHoldingsCount = 0,
}: HeroSectionProps) {
  if (watchlist.loading) {
    return (
      <div className="col-span-full">
        <WidgetSkeleton className="h-56" />
      </div>
    );
  }

  if (watchlist.error) {
    return (
      <div className="col-span-full">
        <WidgetError message={watchlist.error} />
      </div>
    );
  }

  // Portfolio value from portfolio holdings
  const currSym = marketFilter === "india" ? "₹" : "$";
  const portfolioCcy =
    marketFilter === "india" ? "INR" : "USD";
  const portfolioValue =
    portfolioTotals[portfolioCcy] ?? 0;

  // Daily change from watchlist (approximate — uses
  // linked ticker price changes as proxy)
  const data = watchlist.value;
  const dailyChange = data?.daily_change ?? 0;
  const dailyChangePct = data?.daily_change_pct ?? 0;
  const positive = dailyChange >= 0;

  return (
    <div className="col-span-full">
      <div
        className="
          relative overflow-hidden rounded-2xl
          bg-white dark:bg-gray-900
          border border-gray-200 dark:border-gray-800
          p-6 sm:p-8
        "
      >
        {/* Gradient mesh background */}
        <div
          className="
            pointer-events-none absolute inset-0
            opacity-[0.07] dark:opacity-[0.12]
          "
          style={{
            backgroundImage: `
              radial-gradient(
                ellipse 60% 50% at 20% 30%,
                #d946ef 0%, transparent 70%
              ),
              radial-gradient(
                ellipse 50% 60% at 80% 60%,
                #7c3aed 0%, transparent 70%
              ),
              radial-gradient(
                ellipse 40% 40% at 50% 80%,
                #a78bfa 0%, transparent 60%
              )
            `,
          }}
        />
        {/* Noise texture overlay */}
        <div
          className="
            pointer-events-none absolute inset-0
            opacity-[0.03] dark:opacity-[0.05]
          "
          style={{
            backgroundImage:
              "url(\"data:image/svg+xml,%3Csvg xmlns="
              + "'http://www.w3.org/2000/svg' width='200'"
              + " height='200'%3E%3Cfilter id='n'%3E"
              + "%3CfeTurbulence type='fractalNoise'"
              + " baseFrequency='0.9'/%3E%3C/filter%3E"
              + "%3Crect width='100%25' height='100%25'"
              + " filter='url(%23n)'/%3E%3C/svg%3E\")",
          }}
        />

        {/* Content */}
        <div className="relative z-10">
          {/* Top row: greeting + stats */}
          <div
            className="
              flex flex-col sm:flex-row
              sm:items-start sm:justify-between
              gap-4
            "
          >
            {/* Left: greeting + portfolio value */}
            <div>
              <p
                className="
                  text-sm font-medium
                  text-gray-500 dark:text-gray-400
                "
                style={{ fontFamily: "'DM Sans', sans-serif" }}
              >
                Welcome back,{" "}
                {profile?.full_name || "there"}
              </p>
              <p
                className="
                  mt-1 text-4xl font-bold tracking-tight
                  text-gray-900 dark:text-white
                "
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                }}
              >
                {currSym}
                {portfolioValue.toLocaleString("en-US", {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}
              </p>
              <span
                className={`
                  mt-2 inline-flex items-center gap-1
                  rounded-full px-2.5 py-0.5 text-xs
                  font-semibold
                  ${
                    positive
                      ? "bg-emerald-100 text-emerald-700"
                        + " dark:bg-emerald-900/30"
                        + " dark:text-emerald-400"
                      : "bg-red-100 text-red-700"
                        + " dark:bg-red-900/30"
                        + " dark:text-red-400"
                  }
                `}
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                }}
              >
                {positive ? "+" : ""}
                {currSym}
                {Math.abs(dailyChange).toLocaleString(
                  "en-US",
                  {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  },
                )}
                {" ("}
                {positive ? "+" : ""}
                {dailyChangePct.toFixed(2)}%{")"}
              </span>
            </div>

            {/* Right: country filter + stats */}
            <div className="flex flex-col items-end gap-2">
              {/* Country filter */}
              <div className="flex items-center gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-0.5">
                <button
                  onClick={() => onMarketFilterChange("india")}
                  className={`text-xs px-3 py-1.5 rounded-md font-medium transition-colors whitespace-nowrap ${
                    marketFilter === "india"
                      ? "bg-white dark:bg-gray-700 text-indigo-700 dark:text-indigo-400 shadow-sm"
                      : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                  }`}
                >
                  {"\u{1F1EE}\u{1F1F3}"} India
                </button>
                <button
                  onClick={() => onMarketFilterChange("us")}
                  className={`text-xs px-3 py-1.5 rounded-md font-medium transition-colors whitespace-nowrap ${
                    marketFilter === "us"
                      ? "bg-white dark:bg-gray-700 text-indigo-700 dark:text-indigo-400 shadow-sm"
                      : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                  }`}
                >
                  {"\u{1F1FA}\u{1F1F8}"} US
                </button>
              </div>
              {/* Stats */}
              <div className="flex items-center gap-2">
                <span
                  className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
                  style={{ fontFamily: "'IBM Plex Mono', monospace" }}
                >
                  {portfolioHoldingsCount} Stock{portfolioHoldingsCount !== 1 && "s"}
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                  <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                  Active
                </span>
              </div>
            </div>
          </div>

          {/* Bottom row: quick actions */}
          <div className="mt-6 flex flex-wrap gap-2">
            {quickActions.map(({ label, prompt, primary }) => (
              <button
                key={label}
                type="button"
                onClick={() => onQuickAction(prompt)}
                className={`
                  rounded-lg px-4 py-2 text-sm
                  font-medium transition-colors
                  focus:outline-none focus:ring-2
                  focus:ring-fuchsia-500/50
                  ${
                    primary
                      ? "bg-gradient-to-r"
                        + " from-fuchsia-600 to-violet-600"
                        + " text-white shadow-sm"
                        + " hover:from-fuchsia-700"
                        + " hover:to-violet-700"
                      : "border border-gray-300"
                        + " dark:border-gray-700"
                        + " text-gray-700"
                        + " dark:text-gray-300"
                        + " hover:bg-gray-100"
                        + " dark:hover:bg-gray-800"
                  }
                `}
                style={{
                  fontFamily: "'DM Sans', sans-serif",
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
