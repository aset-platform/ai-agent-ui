"use client";
/**
 * Native dashboard page — Variant C asymmetric grid layout.
 *
 * A global country filter (India / US) in the hero card
 * filters all dashboard sections. Default: India.
 */

import { useState, useEffect, useCallback, useMemo } from "react";
import { usePreferences } from "@/hooks/usePreferences";
import type { UserProfile } from "@/hooks/useEditProfile";
import type {
  WatchlistResponse,
  ForecastsResponse,
  AnalysisResponse,
} from "@/lib/types";
import { useChatContext } from "@/providers/ChatProvider";
import { usePortfolio } from "@/hooks/usePortfolio";
import { AddStockModal } from "@/components/widgets/AddStockModal";
import { useRegistry } from "@/hooks/useDashboardData";
import {
  useDashboardHome,
  useProfile,
  type DashboardData,
} from "@/hooks/useDashboardData";
import { HeroSection } from "@/components/widgets/HeroSection";
import { WatchlistWidget } from "@/components/widgets/WatchlistWidget";
import { AnalysisSignalsWidget } from "@/components/widgets/AnalysisSignalsWidget";
import { LLMUsageWidget } from "@/components/widgets/LLMUsageWidget";
import { ForecastChartWidget } from "@/components/widgets/ForecastChartWidget";

export type MarketFilter = "india" | "us";

export default function DashboardPage() {
  const [userPrefs, updatePrefs] = usePreferences();
  const [marketFilter, setMarketFilter] =
    useState<MarketFilter>(
      () =>
        (userPrefs.dashboard?.marketFilter as
          | MarketFilter
          | undefined) ?? "india",
    );
  const [selectedTicker, setSelectedTicker] =
    useState<string | null>(null);
  const { openPanel } = useChatContext();
  const portfolioData = usePortfolio();
  const registryData = useRegistry();
  const registryTickers = useMemo(
    () =>
      registryData.value?.tickers?.map(
        (t) => t.ticker,
      ) ?? [],
    [registryData.value],
  );
  const [showAddStock, setShowAddStock] =
    useState(false);

  // Single request for all widget data
  const {
    watchlist,
    forecasts,
    analysis,
    llmUsage,
    refresh,
  } = useDashboardHome();
  const profileData = useProfile<UserProfile>();
  const profile = profileData.value;

  // -------------------------------------------------------
  // Filter all data by selected market
  // -------------------------------------------------------

  const filteredWatchlist = useMemo<
    DashboardData<WatchlistResponse>
  >(() => {
    if (!watchlist.value) return watchlist;
    const tickers = watchlist.value.tickers.filter(
      (t) => t.market === marketFilter,
    );
    const totalValue = tickers.reduce(
      (sum, t) => sum + t.current_price,
      0,
    );
    const totalPrev = tickers.reduce(
      (sum, t) => sum + t.previous_close,
      0,
    );
    const dailyChg = totalValue - totalPrev;
    const dailyPct = totalPrev
      ? (dailyChg / totalPrev) * 100
      : 0;
    return {
      ...watchlist,
      value: {
        tickers,
        portfolio_value: Math.round(totalValue * 100) / 100,
        daily_change: Math.round(dailyChg * 100) / 100,
        daily_change_pct: Math.round(dailyPct * 100) / 100,
      },
    };
  }, [watchlist, marketFilter]);

  const filteredForecasts = useMemo<
    DashboardData<ForecastsResponse>
  >(() => {
    if (!forecasts.value) return forecasts;
    const filtered = forecasts.value.forecasts.filter(
      (f) => {
        const isIndia =
          f.ticker.endsWith(".NS") ||
          f.ticker.endsWith(".BO");
        return marketFilter === "india"
          ? isIndia
          : !isIndia;
      },
    );
    return {
      ...forecasts,
      value: { forecasts: filtered },
    };
  }, [forecasts, marketFilter]);

  const filteredAnalysis = useMemo<
    DashboardData<AnalysisResponse>
  >(() => {
    if (!analysis.value) return analysis;
    const filtered = analysis.value.analyses.filter(
      (a) => {
        const isIndia =
          a.ticker.endsWith(".NS") ||
          a.ticker.endsWith(".BO");
        return marketFilter === "india"
          ? isIndia
          : !isIndia;
      },
    );
    return {
      ...analysis,
      value: { analyses: filtered },
    };
  }, [analysis, marketFilter]);

  // Auto-select first ticker when filtered list changes
  useEffect(() => {
    const tickers =
      filteredWatchlist.value?.tickers ?? [];
    if (
      tickers.length > 0 &&
      (!selectedTicker ||
        !tickers.find(
          (t) => t.ticker === selectedTicker,
        ))
    ) {
      setSelectedTicker(tickers[0].ticker);
    }
  }, [filteredWatchlist.value, selectedTicker]);

  // Filter analysis to only the selected ticker
  const selectedAnalysis = useMemo<
    DashboardData<AnalysisResponse>
  >(() => {
    if (!filteredAnalysis.value || !selectedTicker)
      return filteredAnalysis;
    const analyses =
      filteredAnalysis.value.analyses.filter(
        (a) => a.ticker === selectedTicker,
      );
    return {
      ...filteredAnalysis,
      value: { analyses },
    };
  }, [filteredAnalysis, selectedTicker]);

  // Quick action: open chat with pre-filled prompt
  const handleQuickAction = useCallback(
    (prompt: string) => {
      openPanel();
      setTimeout(() => {
        const input = document.querySelector(
          '[data-testid="chat-message-input"]',
        ) as HTMLTextAreaElement | null;
        if (input) {
          const nativeSetter =
            Object.getOwnPropertyDescriptor(
              window.HTMLTextAreaElement.prototype,
              "value",
            )?.set;
          nativeSetter?.call(input, prompt);
          input.dispatchEvent(
            new Event("input", { bubbles: true }),
          );
          input.focus();
        }
      }, 350);
    },
    [openPanel],
  );

  return (
    <div className="p-4 md:p-6 space-y-4 md:space-y-6 max-w-[1600px] mx-auto">
      {/* Hero — full width, owns the global filter */}
      <HeroSection
        watchlist={filteredWatchlist}
        profile={profile}
        marketFilter={marketFilter}
        onMarketFilterChange={(f) => {
          setMarketFilter(f);
          updatePrefs("dashboard", {
            marketFilter: f,
          });
        }}
        onQuickAction={handleQuickAction}
        portfolioTotals={portfolioData.totals}
        portfolioHoldingsCount={
          portfolioData.holdings.filter(
            (h) => h.market === marketFilter,
          ).length
        }
      />

      {/* Asymmetric grid */}
      <div className="grid grid-cols-1 lg:grid-cols-[1.1fr_0.9fr] gap-4 md:gap-6">
        <WatchlistWidget
          data={filteredWatchlist}
          selectedTicker={selectedTicker}
          onSelectTicker={setSelectedTicker}
          onRefresh={refresh}
          portfolio={portfolioData.holdings.filter(
            (h) => h.market === marketFilter,
          )}
          portfolioLoading={portfolioData.loading}
          onAddStock={() => setShowAddStock(true)}
        />

        <div className="space-y-4 md:space-y-6">
          <AnalysisSignalsWidget
            data={selectedAnalysis}
          />
          <LLMUsageWidget data={llmUsage} />
        </div>
      </div>

      {/* Forecast — full width */}
      <ForecastChartWidget
        data={filteredForecasts}
        marketFilter={marketFilter}
      />

      <AddStockModal
        isOpen={showAddStock}
        tickers={registryTickers}
        onClose={() => setShowAddStock(false)}
        onAdd={async (data) => {
          await portfolioData.addHolding(data);
        }}
      />
    </div>
  );
}
