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
import { usePortfolio } from "@/hooks/usePortfolio";
import { AddStockModal } from "@/components/widgets/AddStockModal";
import { EditStockModal } from "@/components/widgets/EditStockModal";
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
  const [editingTicker, setEditingTicker] =
    useState<string | null>(null);

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

  // Auto-select first ticker from portfolio or watchlist
  const filteredPortfolio = useMemo(
    () =>
      portfolioData.holdings.filter(
        (h) => h.market === marketFilter,
      ),
    [portfolioData.holdings, marketFilter],
  );

  useEffect(() => {
    // All available tickers (portfolio + watchlist)
    const portfolioTickers = filteredPortfolio.map(
      (h) => h.ticker,
    );
    const watchlistTickers = (
      filteredWatchlist.value?.tickers ?? []
    ).map((t) => t.ticker);
    const allTickers = [
      ...new Set([
        ...portfolioTickers,
        ...watchlistTickers,
      ]),
    ];

    if (
      allTickers.length > 0 &&
      (!selectedTicker ||
        !allTickers.includes(selectedTicker))
    ) {
      // Prefer first portfolio ticker, then watchlist
      setSelectedTicker(
        portfolioTickers[0] ??
          watchlistTickers[0] ??
          "",
      );
    }
  }, [
    filteredPortfolio,
    filteredWatchlist.value,
    selectedTicker,
  ]);

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
        portfolioTotals={portfolioData.totals}
        portfolioInvestedTotals={useMemo(() => {
          const inv: Record<string, number> = {};
          for (const h of portfolioData.holdings) {
            inv[h.currency] =
              (inv[h.currency] ?? 0) + h.invested;
          }
          return inv;
        }, [portfolioData.holdings])}
        portfolioHoldingsCount={
          filteredPortfolio.length
        }
      />

      {/* Asymmetric grid */}
      <div className="grid grid-cols-1 lg:grid-cols-[1.1fr_0.9fr] gap-4 md:gap-6">
        <WatchlistWidget
          data={filteredWatchlist}
          selectedTicker={selectedTicker}
          onSelectTicker={setSelectedTicker}
          onRefresh={refresh}
          portfolio={filteredPortfolio}
          portfolioLoading={portfolioData.loading}
          onAddStock={() => setShowAddStock(true)}
          onEditStock={(ticker) =>
            setEditingTicker(ticker)
          }
          onDeleteStock={(ticker) => {
            const holding = portfolioData.holdings.find(
              (h) => h.ticker === ticker,
            );
            if (
              holding?.transaction_id &&
              confirm(
                `Remove ${ticker} from portfolio?`,
              )
            ) {
              portfolioData.deleteHolding(
                holding.transaction_id,
              );
            }
          }}
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
        selectedTicker={selectedTicker}
      />

      <AddStockModal
        isOpen={showAddStock}
        tickers={registryTickers}
        onClose={() => setShowAddStock(false)}
        onAdd={async (data) => {
          await portfolioData.addHolding(data);
        }}
      />

      {editingTicker && (() => {
        const h = portfolioData.holdings.find(
          (x) => x.ticker === editingTicker,
        );
        return h ? (
          <EditStockModal
            isOpen
            ticker={h.ticker}
            currentQty={h.quantity}
            currentPrice={h.avg_price}
            onClose={() => setEditingTicker(null)}
            onSave={async (data) => {
              await portfolioData.editHolding(
                h.transaction_id,
                data,
              );
              setEditingTicker(null);
            }}
          />
        ) : null;
      })()}
    </div>
  );
}
