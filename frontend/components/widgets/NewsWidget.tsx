"use client";
/**
 * W4: News & Sentiment widget (ASETPLTFRM-290).
 */

import { useState } from "react";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";
import type { DashboardData } from "@/hooks/useDashboardData";
import type { PortfolioNewsResponse } from "@/lib/types";

interface Props {
  data: DashboardData<PortfolioNewsResponse>;
}

function UnanalyzedChip({
  tickers,
}: {
  tickers: string[];
}) {
  const [open, setOpen] = useState(false);
  if (tickers.length === 0) return null;
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        data-testid="news-unanalyzed-chip"
        className="inline-flex items-center gap-1 rounded-md bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400 px-2 py-0.5 text-xs font-medium border border-amber-200 dark:border-amber-800/50"
      >
        <svg
          className="w-3 h-3"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
        {tickers.length} holding
        {tickers.length === 1 ? "" : "s"} unanalyzed
      </button>
      {open && (
        <div
          role="tooltip"
          data-testid="news-unanalyzed-tooltip"
          className="absolute right-0 top-full mt-1 z-20 min-w-[260px] rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg p-3 text-xs"
        >
          <p className="text-gray-700 dark:text-gray-200 font-medium mb-2">
            Sentiment for these holdings is the
            market-wide proxy (no per-stock headlines):
          </p>
          <ul className="grid grid-cols-2 gap-x-3 gap-y-1">
            {tickers.map((t) => (
              <li
                key={t}
                className="font-mono text-gray-600 dark:text-gray-300"
              >
                {t}
              </li>
            ))}
          </ul>
          <p className="text-gray-400 dark:text-gray-500 mt-2 text-[11px]">
            The aggregate above may not reflect
            stock-specific signals for these positions.
          </p>
        </div>
      )}
    </div>
  );
}

function sentimentColor(label: string): string {
  if (label === "Bullish") return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
  if (label === "Bearish") return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
  return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400";
}

function timeAgo(dateStr: string): string {
  try {
    const diff = Date.now() - new Date(dateStr).getTime();
    const hrs = Math.floor(diff / 3_600_000);
    if (hrs < 1) return "Just now";
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days === 1) return "Yesterday";
    return `${days}d ago`;
  } catch {
    return "";
  }
}

export function NewsWidget({ data }: Props) {
  if (data.loading) return <WidgetSkeleton className="h-72" />;
  if (data.error) return <WidgetError message={data.error} />;

  const resp = data.value;
  const headlines = resp?.headlines ?? [];

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
      <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            News & Sentiment
          </h3>
          <div className="flex items-center gap-2">
            {resp && (
              <>
                <UnanalyzedChip
                  tickers={
                    resp.unanalyzed_tickers ?? []
                  }
                />
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${sentimentColor(resp.portfolio_sentiment_label)}`}>
                  Portfolio: {resp.portfolio_sentiment_label} ({resp.portfolio_sentiment > 0 ? "+" : ""}{resp.portfolio_sentiment.toFixed(2)})
                </span>
              </>
            )}
          </div>
        </div>
      </div>
      <div className="divide-y divide-gray-100 dark:divide-gray-800">
        {headlines.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
            No recent news for your holdings
          </p>
        ) : (
          headlines.slice(0, 8).map((h, i) => (
            <a
              key={`${h.url}-${i}`}
              href={h.url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-start gap-3 px-5 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm text-gray-900 dark:text-gray-100 line-clamp-2">
                  {h.title}
                </p>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-xs text-gray-400">
                    {h.source}
                  </span>
                  {h.ticker && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400">
                      {h.ticker}
                    </span>
                  )}
                  <span className="text-xs text-gray-400">
                    {timeAgo(h.published_at)}
                  </span>
                </div>
              </div>
            </a>
          ))
        )}
      </div>
    </div>
  );
}
