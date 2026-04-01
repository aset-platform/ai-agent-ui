"use client";

import {
  useState,
  useEffect,
  useMemo,
  useCallback,
} from "react";
import { useRouter } from "next/navigation";
import type {
  TickerPrice,
  TickerAnalysis,
  RegistryTicker,
} from "@/lib/types";
import {
  useWatchlist,
  useAnalysisLatest,
  useRegistry,
} from "@/hooks/useDashboardData";
import { useTickerRefresh } from "@/hooks/useTickerRefresh";
import type { RefreshState } from "@/hooks/useTickerRefresh";
import { useLinkUnlink } from "@/hooks/useLinkUnlink";
import {
  usePortfolio,
  type PortfolioHolding,
} from "@/hooks/usePortfolio";
import { AddStockModal } from "@/components/widgets/AddStockModal";
import { ConfirmDialog } from "@/components/ConfirmDialog";

// ---------------------------------------------------------------
// Constants
// ---------------------------------------------------------------

const PAGE_SIZE = 6;

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

type MarketFilter = "all" | "india" | "us";
type StatusFilter =
  | "all"
  | "portfolio"
  | "watchlist"
  | "unlinked";
type CardTier = "portfolio" | "watchlist" | "unlinked";
const TIER_WEIGHT: Record<CardTier, number> = {
  portfolio: 0,
  watchlist: 1,
  unlinked: 2,
};
type Sentiment = "Bullish" | "Bearish" | "Neutral";

interface UnifiedCardData {
  ticker: string;
  companyName: string | null;
  price: number | null;
  currency: string;
  market: string;
  isLinked: boolean;
  tier: CardTier;
  // Linked-only enrichment
  change: number | null;
  changePct: number | null;
  sparkline: number[];
  sentiment: Sentiment;
  annualizedReturnPct: number | null;
  analysisDate: string | null;
  lastFetchDate: string | null;
}

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

function deriveSentiment(
  analysis: TickerAnalysis | undefined,
): Sentiment {
  if (!analysis || analysis.signals.length === 0) {
    return "Neutral";
  }
  let bull = 0;
  let bear = 0;
  for (const s of analysis.signals) {
    const sig = s.signal.toLowerCase();
    if (sig.includes("bull") || sig.includes("buy")) {
      bull++;
    } else if (
      sig.includes("bear") ||
      sig.includes("sell")
    ) {
      bear++;
    }
  }
  if (bull > bear) return "Bullish";
  if (bear > bull) return "Bearish";
  return "Neutral";
}

function currencySymbol(ccy: string): string {
  return ccy === "INR" ? "\u20B9" : "$";
}

// ---------------------------------------------------------------
// Sparkline SVG
// ---------------------------------------------------------------

function SparklineSVG({
  data,
  sentiment,
}: {
  data: number[];
  sentiment: Sentiment;
}) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 200;
  const h = 36;
  const pts = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      return `${x},${y}`;
    })
    .join(" L");

  const strokeColor =
    sentiment === "Bearish"
      ? "var(--color-red-500)"
      : sentiment === "Bullish"
        ? "var(--color-emerald-500)"
        : "var(--color-amber-500)";
  const fillId = `spark-${data.length}-${sentiment}`;

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="h-9 w-full"
    >
      <defs>
        <linearGradient
          id={fillId}
          x1="0"
          y1="0"
          x2="0"
          y2="1"
        >
          <stop
            offset="0%"
            stopColor={strokeColor}
            stopOpacity="0.18"
          />
          <stop
            offset="100%"
            stopColor={strokeColor}
            stopOpacity="0"
          />
        </linearGradient>
      </defs>
      <path
        d={`M${pts}`}
        fill="none"
        stroke={strokeColor}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d={`M${pts} L${w},${h} L0,${h}Z`}
        fill={`url(#${fillId})`}
      />
    </svg>
  );
}

// ---------------------------------------------------------------
// Icons (inline SVG)
// ---------------------------------------------------------------

function RefreshIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-[15px] w-[15px]"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  );
}

function LinkIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-[15px] w-[15px]"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  );
}

function UnlinkIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-[15px] w-[15px]"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m18.84 12.25 1.72-1.71a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="m5.17 11.75-1.71 1.71a5 5 0 0 0 7.07 7.07l1.71-1.71" />
      <line x1="2" y1="2" x2="22" y2="22" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function AnalyseIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
      <path d="M10 8v8M7 11h6" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
    </svg>
  );
}

function DotsIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-[18px] w-[18px]"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
    >
      <circle cx="12" cy="5" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="12" cy="19" r="1" />
    </svg>
  );
}

function ChevronIcon({
  dir,
}: {
  dir: "left" | "right";
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {dir === "left" ? (
        <path d="m15 18-6-6 6-6" />
      ) : (
        <path d="m9 18 6-6-6-6" />
      )}
    </svg>
  );
}

// ---------------------------------------------------------------
// Sentiment badge styles
// ---------------------------------------------------------------

const sentimentStyles: Record<
  Sentiment,
  { badge: string; glow: string }
> = {
  Bullish: {
    badge:
      "bg-emerald-100 text-emerald-700 " +
      "dark:bg-emerald-500/15 dark:text-emerald-400",
    glow:
      "shadow-[0_0_12px_rgba(16,185,129,0.15)," +
      "0_0_4px_rgba(16,185,129,0.15)] " +
      "dark:shadow-[0_0_12px_rgba(16,185,129,0.2)," +
      "0_0_4px_rgba(16,185,129,0.2)]",
  },
  Bearish: {
    badge:
      "bg-red-100 text-red-700 " +
      "dark:bg-red-500/15 dark:text-red-400",
    glow:
      "shadow-[0_0_12px_rgba(239,68,68,0.15)," +
      "0_0_4px_rgba(239,68,68,0.15)] " +
      "dark:shadow-[0_0_12px_rgba(239,68,68,0.2)," +
      "0_0_4px_rgba(239,68,68,0.2)]",
  },
  Neutral: {
    badge:
      "bg-amber-100 text-amber-700 " +
      "dark:bg-amber-500/15 dark:text-amber-400",
    glow:
      "shadow-[0_0_12px_rgba(245,158,11,0.15)," +
      "0_0_4px_rgba(245,158,11,0.15)] " +
      "dark:shadow-[0_0_12px_rgba(245,158,11,0.2)," +
      "0_0_4px_rgba(245,158,11,0.2)]",
  },
};

// ---------------------------------------------------------------
// StockCard — Linked
// ---------------------------------------------------------------

// ---------------------------------------------------------------
// StockCard — Portfolio (green accent, "you own this")
// ---------------------------------------------------------------

function PortfolioStockCard({
  card,
  holding,
  isSelected,
  onToggleSelect,
  refreshState,
  onRefresh,
  onUnlink,
  linkBusy,
  onAnalyse,
  onAddToPortfolio,
}: {
  card: UnifiedCardData;
  holding: PortfolioHolding;
  isSelected: boolean;
  onToggleSelect: () => void;
  refreshState: RefreshState;
  onRefresh: () => void;
  onUnlink: () => void;
  linkBusy: boolean;
  onAnalyse: () => void;
  onAddToPortfolio: () => void;
}) {
  const ss = sentimentStyles[card.sentiment];
  const gainPositive =
    (holding.gain_loss_pct ?? 0) >= 0;

  return (
    <div
      data-testid={`stock-card-${card.ticker}`}
      className={`
        group relative flex flex-col
        overflow-hidden rounded-2xl border
        bg-white transition-all duration-300
        dark:bg-gray-900/80
        hover:shadow-[0_4px_16px_rgba(0,0,0,0.06)]
        dark:hover:shadow-[0_4px_16px_rgba(0,0,0,0.3)]
        hover:-translate-y-0.5
        ${
          isSelected
            ? "border-emerald-500 ring-2 ring-emerald-500/25"
            : "border-gray-200 dark:border-gray-800"
        }
      `}
    >
      {/* Emerald gradient accent */}
      <div
        className="h-[3px] bg-gradient-to-r
          from-emerald-600 to-teal-500"
      />

      <div className="flex flex-1 flex-col p-[18px]">
        {/* Header */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span
                className="font-mono text-[17px]
                  font-bold tracking-tight
                  text-gray-900 dark:text-gray-100"
              >
                {card.ticker}
              </span>
              <span
                className={`
                  inline-flex items-center rounded-md
                  px-[7px] py-[2px] text-[10px]
                  font-bold uppercase tracking-wide
                  ${
                    card.market === "india"
                      ? "bg-orange-50 text-orange-700 dark:bg-orange-500/12 dark:text-orange-400"
                      : "bg-blue-50 text-blue-700 dark:bg-blue-500/12 dark:text-blue-400"
                  }
                `}
              >
                {card.market === "india" ? "IN" : "US"}
              </span>
            </div>
            <p
              className="mt-0.5 truncate text-xs
                text-gray-400 dark:text-gray-500"
            >
              {card.companyName ?? "\u2014"}
            </p>
          </div>

          {/* Sentiment badge */}
          <span
            className={`
              inline-flex items-center gap-[5px]
              rounded-full px-2.5 py-1 text-[11px]
              font-bold tracking-wide
              ${ss.badge} ${ss.glow}
            `}
          >
            <span
              className={`
                inline-block h-1.5 w-1.5 rounded-full
                ${
                  card.sentiment === "Bullish"
                    ? "bg-emerald-500"
                    : card.sentiment === "Bearish"
                      ? "bg-red-500"
                      : "bg-amber-500"
                }
              `}
            />
            {card.sentiment}
          </span>
        </div>

        {/* Sparkline */}
        {card.sparkline.length > 1 && (
          <div className="mt-3.5">
            <SparklineSVG
              data={card.sparkline}
              sentiment={card.sentiment}
            />
          </div>
        )}

        {/* Price */}
        <div className="mt-2">
          <div className="flex items-baseline gap-2">
            <span
              className="font-mono text-[26px] font-bold
                tracking-tighter text-gray-900
                dark:text-gray-100"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {currencySymbol(card.currency)}
              {(card.price ?? 0).toLocaleString(
                undefined,
                {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                },
              )}
            </span>
            {card.changePct != null && (
              <span
                className={`
                  inline-flex items-center rounded-full
                  px-2 py-[3px] font-mono text-[11px]
                  font-semibold
                  ${
                    card.changePct >= 0
                      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400"
                      : "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400"
                  }
                `}
              >
                {card.changePct >= 0 ? "+" : ""}
                {card.changePct.toFixed(1)}%
              </span>
            )}
          </div>
          {card.annualizedReturnPct != null && (
            <p className="mt-1 text-[13px]">
              <span
                className={`
                  font-mono font-semibold
                  ${
                    card.annualizedReturnPct >= 0
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-red-600 dark:text-red-400"
                  }
                `}
              >
                {card.annualizedReturnPct >= 0
                  ? "+"
                  : ""}
                {card.annualizedReturnPct.toFixed(1)}%
              </span>
              <span className="ml-1 text-gray-400 dark:text-gray-500">
                annualized
              </span>
            </p>
          )}
        </div>

        {/* Portfolio holdings row */}
        <div
          className="mt-2.5 flex items-center gap-1.5
            rounded-lg bg-emerald-50 px-2.5 py-1.5
            text-[11px] font-semibold text-emerald-700
            dark:bg-emerald-500/10 dark:text-emerald-400"
        >
          <span>{holding.quantity} shares</span>
          <span className="text-emerald-400 dark:text-emerald-600">
            &middot;
          </span>
          <span>
            Avg {currencySymbol(card.currency)}
            {holding.avg_price.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
          </span>
          <span className="text-emerald-400 dark:text-emerald-600">
            &middot;
          </span>
          <span
            className={
              gainPositive
                ? ""
                : "text-red-600 dark:text-red-400"
            }
          >
            {gainPositive ? "+" : ""}
            {(holding.gain_loss_pct ?? 0).toFixed(1)}%
          </span>
        </div>

        {/* Last updated */}
        <p className="mt-2 text-[11px] text-gray-400 dark:text-gray-500">
          Updated{" "}
          {card.analysisDate ?? card.lastFetchDate ?? "N/A"}
        </p>

        {/* Action row */}
        <div
          className="mt-3.5 flex items-center gap-2
            border-t border-gray-100 pt-3.5
            dark:border-gray-800"
        >
          <button
            data-testid={`refresh-${card.ticker}`}
            title="Refresh data"
            disabled={refreshState === "pending"}
            onClick={onRefresh}
            className={`
              flex h-[34px] w-[34px] shrink-0
              items-center justify-center rounded-[10px]
              border transition-all duration-200
              ${
                refreshState === "success"
                  ? "border-emerald-500 text-emerald-500"
                  : refreshState === "error"
                    ? "border-red-500 text-red-500"
                    : "border-gray-200 text-gray-400 hover:border-emerald-500 hover:bg-emerald-50 hover:text-emerald-600 dark:border-gray-700 dark:text-gray-500 dark:hover:border-emerald-500 dark:hover:bg-emerald-500/10 dark:hover:text-emerald-400"
              }
              ${refreshState === "pending" ? "animate-spin" : ""}
              disabled:opacity-50
            `}
          >
            <RefreshIcon />
          </button>

          <button
            data-testid={`unlink-${card.ticker}`}
            title="Unlink stock"
            disabled={linkBusy}
            onClick={onUnlink}
            className="flex h-[34px] w-[34px] shrink-0
              items-center justify-center rounded-[10px]
              border-transparent bg-emerald-50
              text-emerald-600 transition-all duration-200
              hover:bg-red-50 hover:text-red-600
              disabled:opacity-50
              dark:bg-emerald-500/12 dark:text-emerald-400
              dark:hover:bg-red-500/12 dark:hover:text-red-400"
          >
            <LinkIcon />
          </button>

          {/* Add to Portfolio */}
          <button
            data-testid={`add-portfolio-${card.ticker}`}
            title="Add to Portfolio"
            onClick={onAddToPortfolio}
            className="flex h-[34px] w-[34px] shrink-0
              items-center justify-center rounded-[10px]
              border border-emerald-500 text-emerald-600
              transition-all duration-200
              hover:bg-emerald-50 hover:text-emerald-700
              hover:scale-105
              dark:border-emerald-600
              dark:text-emerald-400
              dark:hover:bg-emerald-500/10"
          >
            <PlusIcon />
          </button>

          <button
            data-testid={`analyse-${card.ticker}`}
            title="Analyse"
            onClick={onAnalyse}
            className="flex h-[34px] w-[34px] shrink-0
              items-center justify-center rounded-[10px]
              border border-emerald-600 bg-emerald-600
              text-white transition-all duration-200
              hover:bg-emerald-700 hover:border-emerald-700
              hover:scale-105
              hover:shadow-[0_2px_10px_rgba(16,185,129,0.2)]"
          >
            <AnalyseIcon />
          </button>
        </div>
      </div>

      {/* Checkbox */}
      <div
        className={`
          absolute bottom-3 right-3 z-10
          transition-all duration-200
          ${
            isSelected
              ? "scale-100 opacity-100"
              : "scale-80 opacity-0 group-hover:scale-100 group-hover:opacity-100"
          }
        `}
      >
        <input
          type="checkbox"
          checked={isSelected}
          onChange={onToggleSelect}
          className="h-[18px] w-[18px] cursor-pointer
            rounded accent-emerald-600"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// StockCard — Watchlist (indigo accent, "tracking this")
// ---------------------------------------------------------------

function LinkedStockCard({
  card,
  isSelected,
  onToggleSelect,
  refreshState,
  onRefresh,
  onUnlink,
  linkBusy,
  onAnalyse,
  onAddToPortfolio,
}: {
  card: UnifiedCardData;
  isSelected: boolean;
  onToggleSelect: () => void;
  refreshState: RefreshState;
  onRefresh: () => void;
  onUnlink: () => void;
  linkBusy: boolean;
  onAnalyse: () => void;
  onAddToPortfolio: () => void;
}) {
  const ss = sentimentStyles[card.sentiment];

  return (
    <div
      data-testid={`stock-card-${card.ticker}`}
      className={`
        group relative flex flex-col
        overflow-hidden rounded-2xl border
        bg-white transition-all duration-300
        dark:bg-gray-900/80
        hover:shadow-[0_4px_16px_rgba(0,0,0,0.06)]
        dark:hover:shadow-[0_4px_16px_rgba(0,0,0,0.3)]
        hover:-translate-y-0.5
        ${
          isSelected
            ? "border-indigo-500 ring-2 ring-indigo-500/25"
            : "border-gray-200 dark:border-gray-800"
        }
      `}
    >
      {/* Gradient accent top */}
      <div
        className="h-[3px] bg-gradient-to-r
          from-indigo-600 to-violet-500"
      />

      <div className="flex flex-1 flex-col p-[18px]">
        {/* Header */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span
                className="font-mono text-[17px]
                  font-bold tracking-tight
                  text-gray-900 dark:text-gray-100"
              >
                {card.ticker}
              </span>
              <span
                className={`
                  inline-flex items-center rounded-md
                  px-[7px] py-[2px] text-[10px]
                  font-bold uppercase tracking-wide
                  ${
                    card.market === "india"
                      ? "bg-orange-50 text-orange-700 dark:bg-orange-500/12 dark:text-orange-400"
                      : "bg-blue-50 text-blue-700 dark:bg-blue-500/12 dark:text-blue-400"
                  }
                `}
              >
                {card.market === "india" ? "IN" : "US"}
              </span>
            </div>
            <p
              className="mt-0.5 truncate text-xs
                text-gray-400 dark:text-gray-500"
            >
              {card.companyName ?? "\u2014"}
            </p>
          </div>

          {/* Sentiment badge with glow */}
          <span
            className={`
              inline-flex items-center gap-[5px]
              rounded-full px-2.5 py-1 text-[11px]
              font-bold tracking-wide
              ${ss.badge} ${ss.glow}
            `}
          >
            <span
              className={`
                inline-block h-1.5 w-1.5 rounded-full
                ${
                  card.sentiment === "Bullish"
                    ? "bg-emerald-500"
                    : card.sentiment === "Bearish"
                      ? "bg-red-500"
                      : "bg-amber-500"
                }
              `}
            />
            {card.sentiment}
          </span>
        </div>

        {/* Sparkline */}
        {card.sparkline.length > 1 && (
          <div className="mt-3.5">
            <SparklineSVG
              data={card.sparkline}
              sentiment={card.sentiment}
            />
          </div>
        )}

        {/* Price */}
        <div className="mt-2">
          <div className="flex items-baseline gap-2">
            <span
              className="font-mono text-[26px] font-bold
                tracking-tighter text-gray-900
                dark:text-gray-100"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {currencySymbol(card.currency)}
              {(card.price ?? 0).toLocaleString(
                undefined,
                {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                },
              )}
            </span>
            {card.changePct != null && (
              <span
                className={`
                  inline-flex items-center rounded-full
                  px-2 py-[3px] font-mono text-[11px]
                  font-semibold
                  ${
                    card.changePct >= 0
                      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400"
                      : "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400"
                  }
                `}
              >
                {card.changePct >= 0 ? "+" : ""}
                {card.changePct.toFixed(1)}%
              </span>
            )}
          </div>
          {card.annualizedReturnPct != null && (
            <p className="mt-1 text-[13px]">
              <span
                className={`
                  font-mono font-semibold
                  ${
                    card.annualizedReturnPct >= 0
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-red-600 dark:text-red-400"
                  }
                `}
              >
                {card.annualizedReturnPct >= 0
                  ? "+"
                  : ""}
                {card.annualizedReturnPct.toFixed(1)}%
              </span>
              <span className="ml-1 text-gray-400 dark:text-gray-500">
                annualized
              </span>
            </p>
          )}
        </div>

        {/* Last updated */}
        <p className="mt-2.5 text-[11px] text-gray-400 dark:text-gray-500">
          Updated{" "}
          {card.analysisDate ?? card.lastFetchDate ?? "N/A"}
        </p>

        {/* Action row */}
        <div
          className="mt-3.5 flex items-center gap-2
            border-t border-gray-100 pt-3.5
            dark:border-gray-800"
        >
          {/* Refresh */}
          <button
            data-testid={`refresh-${card.ticker}`}
            title="Refresh data"
            disabled={refreshState === "pending"}
            onClick={onRefresh}
            className={`
              flex h-[34px] w-[34px] shrink-0
              items-center justify-center rounded-[10px]
              border transition-all duration-200
              ${
                refreshState === "success"
                  ? "border-emerald-500 text-emerald-500"
                  : refreshState === "error"
                    ? "border-red-500 text-red-500"
                    : "border-gray-200 text-gray-400 hover:border-indigo-500 hover:bg-indigo-50 hover:text-indigo-600 dark:border-gray-700 dark:text-gray-500 dark:hover:border-indigo-500 dark:hover:bg-indigo-500/10 dark:hover:text-indigo-400"
              }
              ${refreshState === "pending" ? "animate-spin" : ""}
              disabled:opacity-50
            `}
          >
            <RefreshIcon />
          </button>

          {/* Unlink */}
          <button
            data-testid={`unlink-${card.ticker}`}
            title="Unlink stock"
            disabled={linkBusy}
            onClick={onUnlink}
            className="flex h-[34px] w-[34px] shrink-0
              items-center justify-center rounded-[10px]
              border-transparent bg-indigo-50
              text-indigo-600 transition-all duration-200
              hover:bg-red-50 hover:text-red-600
              disabled:opacity-50
              dark:bg-indigo-500/12 dark:text-indigo-400
              dark:hover:bg-red-500/12 dark:hover:text-red-400"
          >
            <LinkIcon />
          </button>

          {/* Add to Portfolio */}
          <button
            data-testid={`add-portfolio-${card.ticker}`}
            title="Add to Portfolio"
            onClick={onAddToPortfolio}
            className="flex h-[34px] w-[34px] shrink-0
              items-center justify-center rounded-[10px]
              border border-emerald-500 text-emerald-600
              transition-all duration-200
              hover:bg-emerald-50 hover:text-emerald-700
              hover:scale-105
              dark:border-emerald-600
              dark:text-emerald-400
              dark:hover:bg-emerald-500/10"
          >
            <PlusIcon />
          </button>

          {/* Analyse */}
          <button
            data-testid={`analyse-${card.ticker}`}
            title="Analyse"
            onClick={onAnalyse}
            className="flex h-[34px] w-[34px] shrink-0
              items-center justify-center rounded-[10px]
              border border-indigo-600 bg-indigo-600
              text-white transition-all duration-200
              hover:bg-indigo-700 hover:border-indigo-700
              hover:scale-105
              hover:shadow-[0_2px_10px_rgba(79,70,229,0.2)]"
          >
            <AnalyseIcon />
          </button>
        </div>
      </div>

      {/* Checkbox — bottom-right, show on hover */}
      <div
        className={`
          absolute bottom-3 right-3 z-10
          transition-all duration-200
          ${
            isSelected
              ? "scale-100 opacity-100"
              : "scale-80 opacity-0 group-hover:scale-100 group-hover:opacity-100"
          }
        `}
      >
        <input
          type="checkbox"
          checked={isSelected}
          onChange={onToggleSelect}
          className="h-[18px] w-[18px] cursor-pointer
            rounded accent-indigo-600"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// StockCard — Unlinked
// ---------------------------------------------------------------

function UnlinkedStockCard({
  card,
  isSelected,
  onToggleSelect,
  onLink,
  linkBusy,
}: {
  card: UnifiedCardData;
  isSelected: boolean;
  onToggleSelect: () => void;
  onLink: () => void;
  linkBusy: boolean;
}) {
  return (
    <div
      data-testid={`stock-card-${card.ticker}`}
      className={`
        group relative flex flex-col
        overflow-hidden rounded-2xl border
        bg-white transition-all duration-300
        dark:bg-gray-900/80
        hover:shadow-[0_4px_16px_rgba(0,0,0,0.06)]
        dark:hover:shadow-[0_4px_16px_rgba(0,0,0,0.3)]
        hover:-translate-y-0.5
        ${
          isSelected
            ? "border-indigo-500 ring-2 ring-indigo-500/25"
            : "border-gray-200 dark:border-gray-800"
        }
      `}
    >
      <div className="flex flex-1 flex-col p-[18px]">
        {/* Header */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span
                className="font-mono text-[17px]
                  font-bold tracking-tight
                  text-gray-900/65 dark:text-gray-100/50
                  group-hover:text-gray-900
                  dark:group-hover:text-gray-100
                  transition-colors"
              >
                {card.ticker}
              </span>
              <span
                className={`
                  inline-flex items-center rounded-md
                  px-[7px] py-[2px] text-[10px]
                  font-bold uppercase tracking-wide
                  ${
                    card.market === "india"
                      ? "bg-orange-50 text-orange-700 dark:bg-orange-500/12 dark:text-orange-400"
                      : "bg-blue-50 text-blue-700 dark:bg-blue-500/12 dark:text-blue-400"
                  }
                `}
              >
                {card.market === "india" ? "IN" : "US"}
              </span>
            </div>
            <p
              className="mt-0.5 truncate text-xs
                text-gray-400/65 dark:text-gray-500/50
                group-hover:text-gray-400
                dark:group-hover:text-gray-500
                transition-colors"
            >
              {card.companyName ?? "\u2014"}
            </p>
          </div>
        </div>

        {/* Price */}
        <div className="mt-4">
          <span
            className="font-mono text-[26px] font-bold
              tracking-tighter
              text-gray-900/65 dark:text-gray-100/50
              group-hover:text-gray-900
              dark:group-hover:text-gray-100
              transition-colors"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {card.price != null
              ? `${currencySymbol(card.currency)}${card.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
              : "\u2014"}
          </span>
        </div>

        {/* Last fetched */}
        <p
          className="mt-3.5 text-[11px]
            text-gray-400/65 dark:text-gray-500/50
            group-hover:text-gray-400
            dark:group-hover:text-gray-500
            transition-colors"
        >
          Last fetched {card.lastFetchDate ?? "N/A"}
        </p>

        {/* Link CTA */}
        <button
          data-testid={`link-${card.ticker}`}
          disabled={linkBusy}
          onClick={onLink}
          className="mt-3.5 flex w-full items-center
            justify-center gap-2 rounded-[10px]
            bg-indigo-600 py-[11px] text-[13px]
            font-bold tracking-wide text-white
            transition-all duration-200
            hover:bg-indigo-700
            hover:scale-[1.01]
            hover:shadow-[0_4px_14px_rgba(79,70,229,0.15)]
            disabled:opacity-50"
        >
          <LinkIcon />
          {linkBusy ? "Linking..." : "Link to Watchlist"}
        </button>
      </div>

      {/* Checkbox — bottom-right, show on hover */}
      <div
        className={`
          absolute bottom-3 right-3 z-10
          transition-all duration-200
          ${
            isSelected
              ? "scale-100 opacity-100"
              : "scale-80 opacity-0 group-hover:scale-100 group-hover:opacity-100"
          }
        `}
      >
        <input
          type="checkbox"
          checked={isSelected}
          onChange={onToggleSelect}
          className="h-[18px] w-[18px] cursor-pointer
            rounded accent-indigo-600"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Skeleton card
// ---------------------------------------------------------------

function SkeletonCard() {
  return (
    <div
      className="animate-pulse overflow-hidden
        rounded-2xl border border-gray-200 bg-white
        dark:border-gray-800 dark:bg-gray-900/80"
    >
      <div className="h-[3px] bg-gray-200 dark:bg-gray-700" />
      <div className="p-[18px]">
        <div className="flex items-start justify-between">
          <div>
            <div className="h-5 w-24 rounded bg-gray-200 dark:bg-gray-700" />
            <div className="mt-2 h-3 w-32 rounded bg-gray-200 dark:bg-gray-700" />
          </div>
          <div className="h-6 w-16 rounded-full bg-gray-200 dark:bg-gray-700" />
        </div>
        <div className="mt-4 h-9 w-full rounded bg-gray-200 dark:bg-gray-700" />
        <div className="mt-3 h-7 w-28 rounded bg-gray-200 dark:bg-gray-700" />
        <div className="mt-2 h-4 w-20 rounded bg-gray-200 dark:bg-gray-700" />
        <div className="mt-3 h-3 w-24 rounded bg-gray-200 dark:bg-gray-700" />
        <div className="mt-4 h-9 w-full rounded-[10px] bg-gray-200 dark:bg-gray-700" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Main page
// ---------------------------------------------------------------

export default function AnalyticsPage() {
  const router = useRouter();

  // Data hooks
  const registryData = useRegistry();
  const watchlistData = useWatchlist();
  const analysisData = useAnalysisLatest();
  const {
    linkedSet,
    isBusy: isLinkBusy,
    linkTicker,
    unlinkTicker,
  } = useLinkUnlink();
  const { startRefresh, getState } = useTickerRefresh();
  const portfolioData = usePortfolio();

  const registry = useMemo(
    () => registryData.value?.tickers ?? [],
    [registryData.value],
  );

  // Build lookup maps
  const watchlistMap = useMemo(() => {
    const map = new Map<string, TickerPrice>();
    for (const t of watchlistData.value?.tickers ?? []) {
      map.set(t.ticker, t);
    }
    return map;
  }, [watchlistData.value]);

  const analysisMap = useMemo(() => {
    const map = new Map<string, TickerAnalysis>();
    for (const a of analysisData.value?.analyses ?? []) {
      map.set(a.ticker, a);
    }
    return map;
  }, [analysisData.value]);

  const portfolioTickerSet = useMemo(
    () =>
      new Set(
        portfolioData.holdings.map((h) => h.ticker),
      ),
    [portfolioData.holdings],
  );

  const holdingsMap = useMemo(() => {
    const map = new Map<string, PortfolioHolding>();
    for (const h of portfolioData.holdings) {
      if (!map.has(h.ticker)) map.set(h.ticker, h);
    }
    return map;
  }, [portfolioData.holdings]);

  // Build unified card data — sorted by tier
  const allCards: UnifiedCardData[] = useMemo(() => {
    const cards = registry.map(
      (reg: RegistryTicker) => {
        const isLinked = linkedSet.has(reg.ticker);
        const inPortfolio = portfolioTickerSet.has(
          reg.ticker,
        );
        const wp = watchlistMap.get(reg.ticker);
        const an = analysisMap.get(reg.ticker);

        const tier: CardTier = inPortfolio
          ? "portfolio"
          : isLinked
            ? "watchlist"
            : "unlinked";

        return {
          ticker: reg.ticker,
          companyName:
            wp?.company_name ?? reg.company_name,
          price:
            wp?.current_price ?? reg.current_price,
          currency: reg.currency,
          market: reg.market,
          isLinked: isLinked || inPortfolio,
          tier,
          change: wp?.change ?? null,
          changePct: wp?.change_pct ?? null,
          sparkline: wp?.sparkline ?? [],
          sentiment: deriveSentiment(an),
          annualizedReturnPct:
            an?.annualized_return_pct ?? null,
          analysisDate: an?.analysis_date ?? null,
          lastFetchDate: reg.last_fetch_date,
        };
      },
    );
    // Stable sort: portfolio → watchlist → unlinked
    cards.sort(
      (a, b) => TIER_WEIGHT[a.tier] - TIER_WEIGHT[b.tier],
    );
    return cards;
  }, [
    registry,
    linkedSet,
    portfolioTickerSet,
    watchlistMap,
    analysisMap,
  ]);

  // UI state
  const [search, setSearch] = useState("");
  const [market, setMarket] =
    useState<MarketFilter>("all");
  const [statusFilter, setStatusFilter] =
    useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [selectedTickers, setSelectedTickers] = useState<
    Set<string>
  >(new Set());
  const [unlinkConfirm, setUnlinkConfirm] = useState<
    string | null
  >(null);
  const [actionsOpen, setActionsOpen] = useState(false);

  // AddStockModal state
  const [addStockTicker, setAddStockTicker] = useState<
    string | null
  >(null);

  // Filter
  const filtered = useMemo(() => {
    let list = allCards;
    if (market !== "all") {
      list = list.filter((c) => c.market === market);
    }
    if (statusFilter !== "all") {
      list = list.filter(
        (c) => c.tier === statusFilter,
      );
    }
    if (search.trim()) {
      const q = search.trim().toUpperCase();
      list = list.filter(
        (c) =>
          c.ticker.toUpperCase().includes(q) ||
          (c.companyName ?? "")
            .toUpperCase()
            .includes(q),
      );
    }
    return list;
  }, [allCards, market, statusFilter, search]);

  // Counts for sub-filter pills
  const tierCounts = useMemo(
    () => {
      let base = allCards;
      if (market !== "all") {
        base = base.filter((c) => c.market === market);
      }
      if (search.trim()) {
        const q = search.trim().toUpperCase();
        base = base.filter(
          (c) =>
            c.ticker.toUpperCase().includes(q) ||
            (c.companyName ?? "")
              .toUpperCase()
              .includes(q),
        );
      }
      return {
        all: base.length,
        portfolio: base.filter(
          (c) => c.tier === "portfolio",
        ).length,
        watchlist: base.filter(
          (c) => c.tier === "watchlist",
        ).length,
        unlinked: base.filter(
          (c) => c.tier === "unlinked",
        ).length,
      };
    },
    [allCards, market, search],
  );

  // Pagination
  const totalPages = Math.max(
    1,
    Math.ceil(filtered.length / PAGE_SIZE),
  );
  const safePage = Math.min(page, totalPages);
  const paged = useMemo(
    () =>
      filtered.slice(
        (safePage - 1) * PAGE_SIZE,
        safePage * PAGE_SIZE,
      ),
    [filtered, safePage],
  );

  // Reset page on filter change
  useEffect(() => {
    setPage(1);
    setSelectedTickers(new Set());
  }, [search, market, statusFilter]);

  // Selection
  const currentPageTickers = useMemo(
    () => new Set(paged.map((c) => c.ticker)),
    [paged],
  );

  const allPageSelected =
    paged.length > 0 &&
    paged.every((c) => selectedTickers.has(c.ticker));
  const somePageSelected =
    paged.some((c) => selectedTickers.has(c.ticker)) &&
    !allPageSelected;

  const toggleSelectAll = useCallback(() => {
    setSelectedTickers((prev) => {
      const next = new Set(prev);
      if (allPageSelected) {
        for (const t of currentPageTickers) {
          next.delete(t);
        }
      } else {
        for (const t of currentPageTickers) {
          next.add(t);
        }
      }
      return next;
    });
  }, [allPageSelected, currentPageTickers]);

  const toggleSelect = useCallback((ticker: string) => {
    setSelectedTickers((prev) => {
      const next = new Set(prev);
      if (next.has(ticker)) {
        next.delete(ticker);
      } else {
        next.add(ticker);
      }
      return next;
    });
  }, []);

  // Bulk actions
  const bulkRefresh = useCallback(() => {
    for (const t of selectedTickers) {
      startRefresh(t);
    }
    setActionsOpen(false);
  }, [selectedTickers, startRefresh]);

  const bulkLink = useCallback(() => {
    for (const t of selectedTickers) {
      if (!linkedSet.has(t)) linkTicker(t);
    }
    setActionsOpen(false);
  }, [selectedTickers, linkedSet, linkTicker]);

  const bulkUnlink = useCallback(() => {
    for (const t of selectedTickers) {
      if (linkedSet.has(t)) unlinkTicker(t);
    }
    setActionsOpen(false);
  }, [selectedTickers, linkedSet, unlinkTicker]);

  // Navigation
  const navigateToAnalysis = useCallback(
    (ticker: string) => {
      router.push(
        `/analytics/analysis?ticker=${encodeURIComponent(ticker)}`,
      );
    },
    [router],
  );

  const loading = registryData.loading;
  const error = registryData.error;

  // ----------------------------------------------------------
  // Error state
  // ----------------------------------------------------------
  if (error && !loading) {
    return (
      <div
        className="rounded-xl border border-red-300
          bg-red-50 p-6 text-center text-red-700
          dark:border-red-800 dark:bg-red-950
          dark:text-red-300"
      >
        <p className="font-semibold">
          Failed to load stocks
        </p>
        <p className="mt-1 text-sm">{error}</p>
      </div>
    );
  }

  // ----------------------------------------------------------
  // Render
  // ----------------------------------------------------------
  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div
        className="flex flex-wrap items-center gap-3
          rounded-2xl border border-gray-200 bg-white
          p-4 dark:border-gray-800 dark:bg-gray-900/80"
      >
        {/* Search */}
        <div className="relative min-w-[220px] flex-1">
          <div
            className="pointer-events-none absolute
              left-3.5 top-1/2 -translate-y-1/2
              text-gray-400 dark:text-gray-500"
          >
            <SearchIcon />
          </div>
          <input
            type="text"
            data-testid="stock-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by ticker or company..."
            className="w-full rounded-xl border
              border-gray-200 bg-gray-50 py-2.5
              pl-10 pr-3 font-mono text-[13px]
              text-gray-900 placeholder-gray-400
              outline-none transition-all
              focus:border-indigo-500
              focus:ring-[3px] focus:ring-indigo-500/25
              dark:border-gray-700 dark:bg-gray-800
              dark:text-gray-100
              dark:placeholder-gray-500"
          />
        </div>

        {/* Market filter */}
        <div
          className="flex gap-0.5 rounded-[10px]
            border border-gray-200 bg-gray-50 p-[3px]
            dark:border-gray-700 dark:bg-gray-800"
        >
          {(
            [
              { key: "all", label: "All" },
              { key: "india", label: "India" },
              { key: "us", label: "US" },
            ] as { key: MarketFilter; label: string }[]
          ).map((m) => (
            <button
              key={m.key}
              data-testid={`market-filter-${m.key}`}
              onClick={() => setMarket(m.key)}
              className={`
                rounded-lg px-3.5 py-[7px] text-[13px]
                font-semibold transition-all
                ${
                  market === m.key
                    ? "bg-white text-indigo-600 shadow-sm dark:bg-gray-700 dark:text-indigo-400"
                    : "text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
                }
              `}
            >
              {m.label}
            </button>
          ))}
        </div>

        {/* Divider */}
        <div className="hidden h-7 w-px bg-gray-200 dark:bg-gray-700 sm:block" />

        {/* Select All */}
        <label
          className="flex cursor-pointer items-center
            gap-[7px] text-[13px] font-medium
            text-gray-500 select-none whitespace-nowrap
            dark:text-gray-400"
        >
          <input
            type="checkbox"
            data-testid="select-all"
            checked={allPageSelected && paged.length > 0}
            ref={(el) => {
              if (el) el.indeterminate = somePageSelected;
            }}
            onChange={toggleSelectAll}
            className="h-4 w-4 cursor-pointer rounded
              accent-indigo-600"
          />
          Select All
        </label>

        {/* Actions dropdown */}
        <div className="relative">
          <button
            data-testid="bulk-actions"
            onClick={() => setActionsOpen((p) => !p)}
            className="flex h-9 w-9 items-center
              justify-center rounded-[10px] border
              border-gray-200 bg-white text-gray-400
              transition-all hover:border-indigo-500
              hover:bg-indigo-50 hover:text-indigo-600
              dark:border-gray-700 dark:bg-gray-800
              dark:text-gray-500
              dark:hover:border-indigo-500
              dark:hover:bg-indigo-500/10
              dark:hover:text-indigo-400"
            title="Bulk actions"
          >
            <DotsIcon />
          </button>
          {actionsOpen && (
            <div
              className="absolute right-0 top-[calc(100%+8px)]
                z-50 min-w-[200px] animate-in fade-in
                slide-in-from-top-2 rounded-xl border
                border-gray-200 bg-white p-1.5
                shadow-[0_12px_48px_rgba(0,0,0,0.14)]
                dark:border-gray-700 dark:bg-gray-800
                dark:shadow-[0_12px_48px_rgba(0,0,0,0.5)]"
            >
              <button
                onClick={bulkRefresh}
                className="flex w-full items-center gap-2.5
                  rounded-[10px] px-3.5 py-2.5 text-[13px]
                  font-medium text-gray-700
                  transition-colors hover:bg-indigo-50
                  dark:text-gray-200
                  dark:hover:bg-indigo-500/10"
              >
                <span className="text-gray-400 dark:text-gray-500">
                  <RefreshIcon />
                </span>
                Refresh Selected
              </button>
              <button
                onClick={bulkLink}
                className="flex w-full items-center gap-2.5
                  rounded-[10px] px-3.5 py-2.5 text-[13px]
                  font-medium text-gray-700
                  transition-colors hover:bg-indigo-50
                  dark:text-gray-200
                  dark:hover:bg-indigo-500/10"
              >
                <span className="text-gray-400 dark:text-gray-500">
                  <LinkIcon />
                </span>
                Link Selected
              </button>
              <button
                onClick={bulkUnlink}
                className="flex w-full items-center gap-2.5
                  rounded-[10px] px-3.5 py-2.5 text-[13px]
                  font-medium text-gray-700
                  transition-colors hover:bg-indigo-50
                  dark:text-gray-200
                  dark:hover:bg-indigo-500/10"
              >
                <span className="text-gray-400 dark:text-gray-500">
                  <UnlinkIcon />
                </span>
                Unlink Selected
              </button>
            </div>
          )}
        </div>

        {/* Stats */}
        <span
          className="ml-auto whitespace-nowrap
            font-mono text-xs font-medium
            text-gray-400 dark:text-gray-500"
        >
          {filtered.length} ticker
          {filtered.length !== 1 ? "s" : ""}
          {" \u00B7 "}
          {tierCounts.portfolio} portfolio
          {" \u00B7 "}
          {tierCounts.watchlist} watchlist
        </span>
      </div>

      {/* Sub-filter pills */}
      <div className="flex flex-wrap items-center gap-2">
        {(
          [
            {
              key: "all",
              label: "All",
              count: tierCounts.all,
            },
            {
              key: "portfolio",
              label: "Portfolio",
              count: tierCounts.portfolio,
            },
            {
              key: "watchlist",
              label: "Watchlist",
              count: tierCounts.watchlist,
            },
            {
              key: "unlinked",
              label: "Unlinked",
              count: tierCounts.unlinked,
            },
          ] as {
            key: StatusFilter;
            label: string;
            count: number;
          }[]
        ).map((sf) => (
          <button
            key={sf.key}
            data-testid={`status-filter-${sf.key}`}
            onClick={() => setStatusFilter(sf.key)}
            className={`
              inline-flex items-center gap-1.5
              rounded-full border px-3.5 py-1.5
              text-xs font-semibold transition-all
              ${
                statusFilter === sf.key
                  ? "border-indigo-600 bg-indigo-600 text-white"
                  : "border-gray-200 bg-white text-gray-400 hover:border-indigo-500 hover:text-indigo-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-500 dark:hover:border-indigo-500 dark:hover:text-indigo-400"
              }
            `}
          >
            {sf.label}
            <span
              className={`
                inline-flex min-w-[18px] items-center
                justify-center rounded-full px-1.5
                text-[10px] font-bold
                ${
                  statusFilter === sf.key
                    ? "bg-white/25 text-white"
                    : "bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-500"
                }
              `}
            >
              {sf.count}
            </span>
          </button>
        ))}
      </div>

      {/* Card grid */}
      {loading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: PAGE_SIZE }).map(
            (_, i) => (
              <SkeletonCard key={i} />
            ),
          )}
        </div>
      ) : paged.length === 0 ? (
        <div
          className="rounded-2xl border border-gray-200
            bg-white p-10 text-center
            dark:border-gray-800 dark:bg-gray-900/80"
        >
          <p className="text-gray-400 dark:text-gray-500">
            No tickers found matching your filters.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {paged.map((card) =>
            card.tier === "portfolio" ? (
              <PortfolioStockCard
                key={card.ticker}
                card={card}
                holding={
                  holdingsMap.get(card.ticker)!
                }
                isSelected={selectedTickers.has(
                  card.ticker,
                )}
                onToggleSelect={() =>
                  toggleSelect(card.ticker)
                }
                refreshState={getState(card.ticker)}
                onRefresh={() =>
                  startRefresh(card.ticker)
                }
                onUnlink={() =>
                  setUnlinkConfirm(card.ticker)
                }
                linkBusy={isLinkBusy(card.ticker)}
                onAnalyse={() =>
                  navigateToAnalysis(card.ticker)
                }
                onAddToPortfolio={() =>
                  setAddStockTicker(card.ticker)
                }
              />
            ) : card.tier === "watchlist" ? (
              <LinkedStockCard
                key={card.ticker}
                card={card}
                isSelected={selectedTickers.has(
                  card.ticker,
                )}
                onToggleSelect={() =>
                  toggleSelect(card.ticker)
                }
                refreshState={getState(card.ticker)}
                onRefresh={() =>
                  startRefresh(card.ticker)
                }
                onUnlink={() =>
                  setUnlinkConfirm(card.ticker)
                }
                linkBusy={isLinkBusy(card.ticker)}
                onAnalyse={() =>
                  navigateToAnalysis(card.ticker)
                }
                onAddToPortfolio={() =>
                  setAddStockTicker(card.ticker)
                }
              />
            ) : (
              <UnlinkedStockCard
                key={card.ticker}
                card={card}
                isSelected={selectedTickers.has(
                  card.ticker,
                )}
                onToggleSelect={() =>
                  toggleSelect(card.ticker)
                }
                onLink={() => linkTicker(card.ticker)}
                linkBusy={isLinkBusy(card.ticker)}
              />
            ),
          )}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 pt-1">
          <button
            data-testid="pagination-prev"
            disabled={safePage <= 1}
            onClick={() =>
              setPage((p) => Math.max(1, p - 1))
            }
            className="flex items-center gap-1.5
              rounded-[10px] border border-gray-200
              bg-white px-4 py-2 text-[13px]
              font-semibold text-gray-500
              transition-all
              hover:border-indigo-500
              hover:text-indigo-600
              disabled:opacity-35
              dark:border-gray-700 dark:bg-gray-800
              dark:text-gray-400
              dark:hover:border-indigo-500
              dark:hover:text-indigo-400"
          >
            <ChevronIcon dir="left" />
            Previous
          </button>
          <span
            data-testid="pagination-info"
            className="font-mono text-[13px] font-medium
              text-gray-400 dark:text-gray-500"
          >
            Page {safePage} of {totalPages}
          </span>
          <button
            data-testid="pagination-next"
            disabled={safePage >= totalPages}
            onClick={() =>
              setPage((p) =>
                Math.min(totalPages, p + 1),
              )
            }
            className="flex items-center gap-1.5
              rounded-[10px] border border-gray-200
              bg-white px-4 py-2 text-[13px]
              font-semibold text-gray-500
              transition-all
              hover:border-indigo-500
              hover:text-indigo-600
              disabled:opacity-35
              dark:border-gray-700 dark:bg-gray-800
              dark:text-gray-400
              dark:hover:border-indigo-500
              dark:hover:text-indigo-400"
          >
            Next
            <ChevronIcon dir="right" />
          </button>
        </div>
      )}

      {/* Add to Portfolio modal */}
      <AddStockModal
        isOpen={addStockTicker !== null}
        tickers={
          addStockTicker
            ? [addStockTicker]
            : registry.map((r) => r.ticker)
        }
        onClose={() => setAddStockTicker(null)}
        onAdd={async (data) => {
          await portfolioData.addHolding(data);
          setAddStockTicker(null);
        }}
      />

      {/* Unlink confirmation dialog */}
      <ConfirmDialog
        open={unlinkConfirm !== null}
        title="Unlink Stock"
        message={
          unlinkConfirm
            ? `Unlink ${unlinkConfirm}? You can re-link it later.`
            : ""
        }
        confirmLabel="Unlink"
        variant="warning"
        onConfirm={() => {
          if (unlinkConfirm) unlinkTicker(unlinkConfirm);
          setUnlinkConfirm(null);
        }}
        onCancel={() => setUnlinkConfirm(null)}
      />
    </div>
  );
}
