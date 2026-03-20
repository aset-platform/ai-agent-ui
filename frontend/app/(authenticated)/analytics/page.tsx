"use client";

import { useState, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import type {
  TickerPrice,
  TickerAnalysis,
} from "@/lib/types";
import {
  useWatchlist,
  useAnalysisLatest,
  useRegistry,
} from "@/hooks/useDashboardData";

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

type MarketFilter = "india" | "us";

type Sentiment = "Bullish" | "Bearish" | "Neutral";

interface StockCardData {
  ticker: string;
  companyName: string | null;
  price: number;
  currency: string;
  market: string;
  sentiment: Sentiment;
  annualizedReturnPct: number | null;
  lastUpdated: string | null;
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
    } else if (sig.includes("bear") || sig.includes("sell")) {
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

function sentimentColor(s: Sentiment): string {
  switch (s) {
    case "Bullish":
      return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
    case "Bearish":
      return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
    default:
      return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400";
  }
}

function sentimentDot(s: Sentiment): string {
  switch (s) {
    case "Bullish":
      return "\u{1F7E2}";
    case "Bearish":
      return "\u{1F534}";
    default:
      return "\u{1F7E1}";
  }
}

// ---------------------------------------------------------------
// StockCard component
// ---------------------------------------------------------------

function StockCard({
  card,
  onAnalyse,
}: {
  card: StockCardData;
  onAnalyse: (ticker: string) => void;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 transition hover:shadow-md dark:border-gray-700 dark:bg-gray-900">
      {/* Header: ticker + sentiment */}
      <div className="flex items-start justify-between">
        <div>
          <p className="font-mono text-lg font-bold text-gray-900 dark:text-gray-100">
            {card.ticker}
          </p>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400 line-clamp-1">
            {card.companyName ?? "\u2014"}
          </p>
        </div>
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${sentimentColor(card.sentiment)}`}
        >
          {sentimentDot(card.sentiment)} {card.sentiment}
        </span>
      </div>

      {/* Price */}
      <p className="mt-3 font-mono text-2xl font-semibold text-gray-900 dark:text-gray-100">
        {currencySymbol(card.currency)}
        {(card.price ?? 0).toLocaleString(undefined, {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })}
      </p>

      {/* 10Y return */}
      {card.annualizedReturnPct != null && (
        <p className="mt-1 text-sm">
          <span
            className={
              card.annualizedReturnPct >= 0
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-red-600 dark:text-red-400"
            }
          >
            {card.annualizedReturnPct >= 0 ? "+" : ""}
            {card.annualizedReturnPct.toFixed(1)}%
          </span>
          <span className="ml-1 text-gray-400 dark:text-gray-500">
            annualized
          </span>
        </p>
      )}

      {/* Last updated */}
      <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
        Updated {card.lastUpdated ?? "N/A"}
      </p>

      {/* Analyse link */}
      <button
        onClick={() => onAnalyse(card.ticker)}
        className="mt-3 w-full rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-indigo-700"
      >
        Analyse
      </button>
    </div>
  );
}

// ---------------------------------------------------------------
// Skeleton card
// ---------------------------------------------------------------

function SkeletonCard() {
  return (
    <div className="animate-pulse rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
      <div className="flex items-start justify-between">
        <div>
          <div className="h-5 w-24 rounded bg-gray-200 dark:bg-gray-700" />
          <div className="mt-2 h-3 w-32 rounded bg-gray-200 dark:bg-gray-700" />
        </div>
        <div className="h-5 w-16 rounded-full bg-gray-200 dark:bg-gray-700" />
      </div>
      <div className="mt-4 h-7 w-28 rounded bg-gray-200 dark:bg-gray-700" />
      <div className="mt-2 h-4 w-20 rounded bg-gray-200 dark:bg-gray-700" />
      <div className="mt-2 h-3 w-24 rounded bg-gray-200 dark:bg-gray-700" />
      <div className="mt-3 h-8 w-full rounded-lg bg-gray-200 dark:bg-gray-700" />
    </div>
  );
}

// ---------------------------------------------------------------
// Main page
// ---------------------------------------------------------------

export default function AnalyticsPage() {
  const router = useRouter();

  // SWR-cached data (shared with dashboard page)
  const watchlistData = useWatchlist();
  const analysisData = useAnalysisLatest();
  const registryData = useRegistry();

  const watchlist = watchlistData.value;
  const analysis = analysisData.value;
  const registry = useMemo(
    () => registryData.value?.tickers ?? [],
    [registryData.value],
  );
  // Cards render as soon as watchlist arrives;
  // analysis + registry load independently.
  const cardsLoading = watchlistData.loading;
  const error = watchlistData.error;

  // UI state
  const [market, setMarket] = useState<MarketFilter>("india");
  const [searchTicker, setSearchTicker] = useState("");
  const [selectedTicker, setSelectedTicker] = useState("");

  // Build analysis lookup map
  const analysisMap = useMemo(() => {
    const map = new Map<string, TickerAnalysis>();
    if (analysis) {
      for (const a of analysis.analyses) {
        map.set(a.ticker, a);
      }
    }
    return map;
  }, [analysis]);

  // Build card data from watchlist + analysis
  const cards: StockCardData[] = useMemo(() => {
    if (!watchlist) return [];
    return watchlist.tickers.map((tp: TickerPrice) => {
      const ta = analysisMap.get(tp.ticker);
      return {
        ticker: tp.ticker,
        companyName: tp.company_name,
        price: tp.current_price,
        currency: tp.currency,
        market: tp.market,
        sentiment: deriveSentiment(ta),
        annualizedReturnPct: ta?.annualized_return_pct ?? null,
        lastUpdated: ta?.analysis_date ?? null,
      };
    });
  }, [watchlist, analysisMap]);

  // Filter by market
  const filteredCards = useMemo(
    () => cards.filter((c) => c.market === market),
    [cards, market],
  );

  // Registry for dropdown (filtered by market)
  const registryFiltered = useMemo(
    () => registry.filter((r) => r.market === market),
    [registry, market],
  );

  // Navigation
  const navigateToAnalysis = useCallback(
    (ticker: string) => {
      if (ticker.trim()) {
        router.push(
          `/analytics/analysis?ticker=${encodeURIComponent(ticker.trim())}`,
        );
      }
    },
    [router],
  );

  const handleSearchAnalyse = useCallback(() => {
    navigateToAnalysis(searchTicker);
  }, [searchTicker, navigateToAnalysis]);

  const handleDropdownAnalyse = useCallback(() => {
    navigateToAnalysis(selectedTicker);
  }, [selectedTicker, navigateToAnalysis]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        handleSearchAnalyse();
      }
    },
    [handleSearchAnalyse],
  );

  // ----------------------------------------------------------
  // Error state
  // ----------------------------------------------------------
  if (error && !cardsLoading) {
    return (
      <div className="rounded-xl border border-red-300 bg-red-50 p-6 text-center text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
        <p className="font-semibold">
          Failed to load dashboard
        </p>
        <p className="mt-1 text-sm">{error}</p>
      </div>
    );
  }

  // ----------------------------------------------------------
  // Render
  // ----------------------------------------------------------
  return (
    <div className="space-y-5">
      {/* Search + Analyse bar */}
      <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          {/* Search input */}
          <div className="flex-1">
            <label className="mb-1 block text-xs font-medium text-gray-500 dark:text-gray-400">
              Search ticker
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={searchTicker}
                onChange={(e) =>
                  setSearchTicker(e.target.value.toUpperCase())
                }
                onKeyDown={handleKeyDown}
                placeholder="Enter ticker symbol..."
                className="flex-1 rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-sm font-mono text-gray-900 placeholder-gray-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-500"
              />
              <button
                onClick={handleSearchAnalyse}
                disabled={!searchTicker.trim()}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700 disabled:opacity-40"
              >
                Analyse
              </button>
            </div>
          </div>

          {/* Dropdown select */}
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-500 dark:text-gray-400">
              Or select a stock
            </label>
            <div className="flex gap-2">
              <select
                value={selectedTicker}
                onChange={(e) => setSelectedTicker(e.target.value)}
                className="rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-sm text-gray-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
              >
                <option value="">
                  {registryData.loading
                    ? "Loading tickers..."
                    : "Choose ticker..."}
                </option>
                {registryFiltered.map((t) => (
                  <option key={t.ticker} value={t.ticker}>
                    {t.ticker}
                    {t.company_name
                      ? ` \u2014 ${t.company_name}`
                      : ""}
                  </option>
                ))}
              </select>
              <button
                onClick={handleDropdownAnalyse}
                disabled={!selectedTicker}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700 disabled:opacity-40"
              >
                Go
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Market filter */}
      <div className="flex gap-2">
        {(
          [
            { key: "india", label: "India" },
            { key: "us", label: "US" },
          ] as { key: MarketFilter; label: string }[]
        ).map((m) => (
          <button
            key={m.key}
            onClick={() => setMarket(m.key)}
            className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              market === m.key
                ? "bg-indigo-600 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
            }`}
          >
            {m.label}
          </button>
        ))}
        <span className="ml-2 flex items-center text-sm text-gray-400 dark:text-gray-500">
          {filteredCards.length} stock
          {filteredCards.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Card grid */}
      {cardsLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : filteredCards.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-8 text-center dark:border-gray-700 dark:bg-gray-900">
          <p className="text-gray-400 dark:text-gray-500">
            No stocks found for this market. Link tickers
            in the{" "}
            <button
              onClick={() => router.push("/analytics/marketplace")}
              className="text-indigo-600 underline hover:text-indigo-700 dark:text-indigo-400"
            >
              Marketplace
            </button>
            .
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filteredCards.map((card) => (
            <StockCard
              key={card.ticker}
              card={card}
              onAnalyse={navigateToAnalysis}
            />
          ))}
        </div>
      )}
    </div>
  );
}
