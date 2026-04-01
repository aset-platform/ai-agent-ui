"use client";

import { useState, useMemo, useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { DashboardData } from "@/hooks/useDashboardData";
import type { WatchlistResponse } from "@/lib/types";
import type { PortfolioHolding } from "@/hooks/usePortfolio";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";

const PAGE_SIZE = 18;

interface WatchlistWidgetProps {
  data: DashboardData<WatchlistResponse>;
  selectedTicker?: string | null;
  onSelectTicker?: (ticker: string) => void;
  onRefresh?: () => void;
  /** Portfolio holdings for the Portfolio tab. */
  portfolio?: PortfolioHolding[];
  portfolioLoading?: boolean;
  onAddStock?: () => void;
  onEditStock?: (ticker: string) => void;
  onDeleteStock?: (ticker: string) => void;
}

/** Map ISO currency code to display symbol. */
function currencySymbol(code: string): string {
  const map: Record<string, string> = {
    USD: "$",
    INR: "₹",
    EUR: "€",
    GBP: "£",
    JPY: "¥",
  };
  return map[code?.toUpperCase()] ?? code ?? "$";
}

function Sparkline({
  data,
  positive,
}: {
  data: number[];
  positive: boolean;
}) {
  if (!data.length) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 80;
  const h = 30;
  const points = data
    .map(
      (v, i) =>
        `${(i / (data.length - 1)) * w},${
          h - ((v - min) / range) * h
        }`,
    )
    .join(" ");
  return (
    <svg width={w} height={h} className="shrink-0">
      <polyline
        points={points}
        fill="none"
        stroke={positive ? "#34d399" : "#fb7185"}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

type RefreshState = "idle" | "pending" | "success" | "error";

type WidgetTab = "portfolio" | "watchlist";

export function WatchlistWidget({
  data,
  selectedTicker,
  onSelectTicker,
  onRefresh,
  portfolio = [],
  portfolioLoading = false,
  onAddStock,
  onEditStock,
  onDeleteStock,
}: WatchlistWidgetProps) {
  const [activeTab, setActiveTab] =
    useState<WidgetTab>("portfolio");
  const [page, setPage] = useState(1);

  // Per-ticker refresh state
  const [refreshing, setRefreshing] = useState<
    Record<string, RefreshState>
  >({});

  const startRefresh = useCallback(
    async (ticker: string, e: React.MouseEvent) => {
      e.stopPropagation();
      setRefreshing((prev) => ({
        ...prev,
        [ticker]: "pending",
      }));
      try {
        const r = await apiFetch(
          `${API_URL}/dashboard/refresh/${encodeURIComponent(ticker)}`,
          { method: "POST" },
        );
        if (!r.ok) throw new Error(`HTTP ${r.status}`);

        // Poll for completion
        const poll = async () => {
          for (let i = 0; i < 90; i++) {
            await new Promise((ok) =>
              setTimeout(ok, 2000),
            );
            const sr = await apiFetch(
              `${API_URL}/dashboard/refresh/${encodeURIComponent(ticker)}/status`,
            );
            if (!sr.ok) break;
            const s = await sr.json();
            if (s.status === "success") {
              setRefreshing((prev) => ({
                ...prev,
                [ticker]: "success",
              }));
              onRefresh?.();
              setTimeout(
                () =>
                  setRefreshing((prev) => ({
                    ...prev,
                    [ticker]: "idle",
                  })),
                3000,
              );
              return;
            }
            if (s.status === "error") {
              setRefreshing((prev) => ({
                ...prev,
                [ticker]: "error",
              }));
              setTimeout(
                () =>
                  setRefreshing((prev) => ({
                    ...prev,
                    [ticker]: "idle",
                  })),
                5000,
              );
              return;
            }
          }
        };
        poll();
      } catch {
        setRefreshing((prev) => ({
          ...prev,
          [ticker]: "error",
        }));
        setTimeout(
          () =>
            setRefreshing((prev) => ({
              ...prev,
              [ticker]: "idle",
            })),
          5000,
        );
      }
    },
    [onRefresh],
  );

  const tickers = data.value?.tickers ?? [];
  const maxPages = Math.max(
    1,
    Math.ceil(tickers.length / PAGE_SIZE),
  );
  const paginated = useMemo(
    () =>
      tickers.slice(
        (page - 1) * PAGE_SIZE,
        page * PAGE_SIZE,
      ),
    [tickers, page],
  );

  if (data.loading) {
    return <WidgetSkeleton className="h-72" />;
  }

  if (data.error) {
    return <WidgetError message={data.error} />;
  }

  return (
    <div
      data-testid="dashboard-watchlist-table"
      className="
        rounded-xl
        bg-white dark:bg-gray-900
        border border-gray-200 dark:border-gray-800
        overflow-hidden
      "
    >
      {/* Header with tabs */}
      <div
        className="
          px-5 py-3 flex items-center justify-between
          border-b border-gray-100 dark:border-gray-800
        "
      >
        <div className="inline-flex rounded-lg bg-gray-100 dark:bg-gray-800 p-0.5">
          <button
            onClick={() => {
              setActiveTab("portfolio");
              // Auto-select first portfolio ticker
              if (portfolio.length > 0) {
                onSelectTicker?.(
                  portfolio[0].ticker,
                );
              }
            }}
            className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
              activeTab === "portfolio"
                ? "bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm"
                : "text-gray-500 dark:text-gray-400"
            }`}
          >
            Portfolio
          </button>
          <button
            onClick={() => {
              setActiveTab("watchlist");
              // Auto-select first watchlist ticker
              if (tickers.length > 0) {
                onSelectTicker?.(
                  tickers[0].ticker,
                );
              }
            }}
            className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
              activeTab === "watchlist"
                ? "bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm"
                : "text-gray-500 dark:text-gray-400"
            }`}
          >
            Watchlist
          </button>
        </div>
        <div className="flex items-center gap-2">
          {activeTab === "portfolio" && onAddStock && (
            <button
              data-testid="dashboard-add-stock-btn"
              onClick={onAddStock}
              title="Add stock to portfolio"
              className="p-1 rounded-md text-gray-400 hover:text-indigo-600 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>
          )}
          <span className="text-xs text-gray-400 dark:text-gray-500">
            {activeTab === "portfolio"
              ? `${portfolio.length} stock${portfolio.length !== 1 ? "s" : ""}`
              : `${tickers.length} ticker${tickers.length !== 1 ? "s" : ""}`}
          </span>
        </div>
      </div>

      {/* Portfolio tab */}
      {activeTab === "portfolio" && (
        portfolioLoading ? (
          <div className="px-5 py-10 text-center">
            <div className="animate-spin h-6 w-6 border-2 border-indigo-500 border-t-transparent rounded-full mx-auto" />
          </div>
        ) : portfolio.length === 0 ? (
          <div className="px-5 py-10 text-center">
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-3">
              No stocks in your portfolio
            </p>
            {onAddStock && (
              <button
                onClick={onAddStock}
                className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
                Add Stock
              </button>
            )}
          </div>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-800">
            {portfolio.map((h) => {
              const sym = currencySymbol(h.currency);
              const gain = h.gain_loss_pct ?? 0;
              const positive = gain >= 0;
              return (
                <div
                  key={h.ticker}
                  data-testid={`dashboard-watchlist-row-${h.ticker}`}
                  onClick={() =>
                    onSelectTicker?.(h.ticker)
                  }
                  className={`flex items-center gap-3 px-5 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 ${
                    selectedTicker === h.ticker
                      ? "bg-indigo-50/50 dark:bg-indigo-900/20 border-l-2 border-l-indigo-500"
                      : ""
                  }`}
                >
                  <span className={`h-2 w-2 shrink-0 rounded-full ${positive ? "bg-emerald-500" : "bg-red-500"}`} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-gray-900 dark:text-white truncate">
                      {h.ticker}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {h.quantity} shares @ {sym}{h.avg_price.toFixed(2)}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-medium text-gray-900 dark:text-white font-mono">
                      {sym}{(h.current_value ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 font-mono">
                      Inv: {sym}{h.invested.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </p>
                  </div>
                  <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums ${positive ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400" : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"}`}>
                    {positive ? "+" : ""}{gain.toFixed(2)}%
                  </span>
                  {/* Refresh / Edit / Delete actions */}
                  <div className="flex items-center gap-0.5 shrink-0">
                    <button
                      data-testid={`dashboard-watchlist-refresh-${h.ticker}`}
                      onClick={(e) =>
                        startRefresh(h.ticker, e)
                      }
                      disabled={
                        refreshing[h.ticker] === "pending"
                      }
                      title={
                        refreshing[h.ticker] === "pending"
                          ? "Refreshing..."
                          : refreshing[h.ticker] === "success"
                            ? "Updated!"
                            : refreshing[h.ticker] === "error"
                              ? "Refresh failed"
                              : "Refresh ticker data"
                      }
                      className="p-1 rounded text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-50"
                    >
                      {refreshing[h.ticker] ===
                      "pending" ? (
                        <svg
                          className="w-3.5 h-3.5 animate-spin"
                          viewBox="0 0 24 24"
                          fill="none"
                        >
                          <circle
                            cx="12" cy="12" r="10"
                            stroke="currentColor"
                            strokeWidth="3"
                            strokeDasharray="50 20"
                          />
                        </svg>
                      ) : refreshing[h.ticker] ===
                        "success" ? (
                        <svg
                          className="w-3.5 h-3.5 text-emerald-500"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.5"
                        >
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      ) : refreshing[h.ticker] ===
                        "error" ? (
                        <svg
                          className="w-3.5 h-3.5 text-red-500"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.5"
                        >
                          <line x1="18" y1="6" x2="6" y2="18" />
                          <line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                      ) : (
                        <svg
                          className="w-3.5 h-3.5"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
                          <path d="M21 3v5h-5" />
                        </svg>
                      )}
                    </button>
                    {onEditStock && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onEditStock(h.ticker);
                        }}
                        title="Edit holding"
                        className="p-1 rounded text-gray-400 hover:text-indigo-600 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                      >
                        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                        </svg>
                      </button>
                    )}
                    {onDeleteStock && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onDeleteStock(h.ticker);
                        }}
                        title="Remove from portfolio"
                        className="p-1 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                      >
                        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
                        </svg>
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )
      )}

      {/* Watchlist tab */}
      {activeTab === "watchlist" && (tickers.length === 0 ? (
        <div className="px-5 py-10 text-center">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            No stocks tracked for this market. Link a
            ticker to get started.
          </p>
        </div>
      ) : (
        <>
        <div className="divide-y divide-gray-100 dark:divide-gray-800">
          {paginated.map((t, idx) => {
            const price = t.current_price ?? 0;
            const change = t.change ?? 0;
            const changePct = t.change_pct ?? 0;
            const positive = change >= 0;
            const sym = currencySymbol(t.currency);
            return (
              <div
                key={t.ticker}
                data-testid={`dashboard-watchlist-row-${t.ticker}`}
                onClick={() =>
                  onSelectTicker?.(t.ticker)
                }
                className={`
                  flex items-center gap-3 px-5 py-3
                  cursor-pointer
                  transition-colors duration-150
                  ${
                    selectedTicker === t.ticker
                      ? "bg-indigo-50/50 dark:bg-indigo-900/20 border-l-2 border-l-indigo-500"
                      : idx % 2 === 1
                        ? "bg-gray-50/50 dark:bg-gray-800/30"
                        : ""
                  }
                  hover:bg-gray-50 dark:hover:bg-gray-800/50
                `}
              >
                {/* Color dot */}
                <span
                  className={`
                    h-2 w-2 shrink-0 rounded-full
                    ${
                      positive
                        ? "bg-emerald-500"
                        : "bg-red-500"
                    }
                  `}
                />

                {/* Ticker + company name */}
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-gray-900 dark:text-white truncate">
                    {t.ticker}
                  </p>
                  {t.company_name && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                      {t.company_name}
                    </p>
                  )}
                </div>

                {/* Current price with currency */}
                <span
                  className="text-sm font-medium text-gray-900 dark:text-white tabular-nums"
                  style={{
                    fontFamily:
                      "'IBM Plex Mono', monospace",
                  }}
                >
                  {sym}
                  {price.toLocaleString(
                    "en-US",
                    {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    },
                  )}
                </span>

                {/* Change pill */}
                <span
                  className={`
                    inline-flex rounded-full
                    px-2 py-0.5 text-xs font-semibold
                    tabular-nums
                    ${
                      positive
                        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                        : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                    }
                  `}
                  style={{
                    fontFamily:
                      "'IBM Plex Mono', monospace",
                  }}
                >
                  {positive ? "+" : ""}
                  {changePct.toFixed(2)}%
                </span>

                {/* Sparkline */}
                <Sparkline
                  data={t.sparkline}
                  positive={positive}
                />

                {/* Per-ticker refresh */}
                <button
                  data-testid={`dashboard-watchlist-refresh-${t.ticker}`}
                  onClick={(e) =>
                    startRefresh(t.ticker, e)
                  }
                  disabled={
                    refreshing[t.ticker] === "pending"
                  }
                  title={
                    refreshing[t.ticker] === "pending"
                      ? "Refreshing..."
                      : refreshing[t.ticker] === "success"
                        ? "Updated!"
                        : refreshing[t.ticker] === "error"
                          ? "Refresh failed"
                          : "Refresh ticker data"
                  }
                  className="
                    p-1 rounded-md shrink-0
                    text-gray-400 dark:text-gray-500
                    hover:text-gray-600
                    dark:hover:text-gray-300
                    hover:bg-gray-100
                    dark:hover:bg-gray-800
                    transition-colors
                    disabled:opacity-50
                  "
                >
                  {refreshing[t.ticker] ===
                  "pending" ? (
                    <svg
                      className="w-3.5 h-3.5 animate-spin"
                      viewBox="0 0 24 24"
                      fill="none"
                    >
                      <circle
                        cx="12" cy="12" r="10"
                        stroke="currentColor"
                        strokeWidth="3"
                        strokeDasharray="50 20"
                      />
                    </svg>
                  ) : refreshing[t.ticker] ===
                    "success" ? (
                    <svg
                      className="w-3.5 h-3.5 text-emerald-500"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.5"
                    >
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  ) : refreshing[t.ticker] ===
                    "error" ? (
                    <svg
                      className="w-3.5 h-3.5 text-red-500"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.5"
                    >
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  ) : (
                    <svg
                      className="w-3.5 h-3.5"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
                      <path d="M21 3v5h-5" />
                    </svg>
                  )}
                </button>
              </div>
            );
          })}
        </div>

        {/* Pagination */}
        {maxPages > 1 && (
          <div className="flex items-center justify-between px-5 py-2.5 border-t border-gray-100 dark:border-gray-800 text-xs text-gray-500 dark:text-gray-400">
            <span>
              {tickers.length} ticker
              {tickers.length !== 1 ? "s" : ""}
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() =>
                  setPage((p) => Math.max(1, p - 1))
                }
                disabled={page <= 1}
                className="rounded px-2 py-1 hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Prev
              </button>
              <span>
                {page} / {maxPages}
              </span>
              <button
                onClick={() =>
                  setPage((p) =>
                    Math.min(maxPages, p + 1),
                  )
                }
                disabled={page >= maxPages}
                className="rounded px-2 py-1 hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Next
              </button>
            </div>
          </div>
        )}
        </>
      ))}
    </div>
  );
}
