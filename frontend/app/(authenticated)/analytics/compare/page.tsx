"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { CompareChart, COMPARE_COLORS } from "@/components/charts/CompareChart";
import { useTheme } from "@/hooks/useTheme";
import type {
  CompareResponse,
  CompareMetric,
  WatchlistResponse,
} from "@/lib/types";

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

function metricBadge(val: number | null): string {
  if (val == null) return "";
  return val >= 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-red-600 dark:text-red-400";
}

function fmt(val: number | null, decimals = 2): string {
  if (val == null) return "\u2014";
  return val.toFixed(decimals);
}

// ---------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------

function Skeleton() {
  return (
    <div className="animate-pulse space-y-6">
      <div className="h-80 rounded-xl bg-gray-200 dark:bg-gray-800" />
      <div className="h-64 rounded-xl bg-gray-200 dark:bg-gray-800" />
      <div className="h-40 rounded-xl bg-gray-200 dark:bg-gray-800" />
    </div>
  );
}

// ---------------------------------------------------------------
// Main page
// ---------------------------------------------------------------

/** Reusable compare component — used both as a
 *  standalone page and embedded in the Analysis tab. */
export function CompareContent() {
  // User tickers from watchlist
  const [allTickers, setAllTickers] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [data, setData] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [tickersLoading, setTickersLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch user's linked tickers
  useEffect(() => {
    const controller = new AbortController();
    apiFetch(`${API_URL}/dashboard/watchlist`, {
      signal: controller.signal,
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((wl: WatchlistResponse) => {
        setAllTickers(wl.tickers.map((t) => t.ticker));
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
      })
      .finally(() => setTickersLoading(false));
    return () => controller.abort();
  }, []);

  const toggle = useCallback((ticker: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(ticker)) {
        next.delete(ticker);
      } else if (next.size < 7) {
        next.add(ticker);
      }
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelected(new Set(allTickers.slice(0, 7)));
  }, [allTickers]);

  const clearAll = useCallback(() => {
    setSelected(new Set());
    setData(null);
  }, []);

  // Fetch compare data
  const fetchCompare = useCallback(async () => {
    if (selected.size < 2) return;
    setLoading(true);
    setError(null);
    try {
      const tickers = Array.from(selected).join(",");
      const r = await apiFetch(
        `${API_URL}/dashboard/compare?tickers=${encodeURIComponent(tickers)}`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const json = (await r.json()) as CompareResponse;
      setData(json);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to load compare data",
      );
    } finally {
      setLoading(false);
    }
  }, [selected]);

  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  // ----------------------------------------------------------
  // Render
  // ----------------------------------------------------------
  return (
    <div className="space-y-6">
      {/* Ticker selector */}
      <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
        <div className="flex items-center justify-between mb-3">
          <p className="text-sm font-medium text-gray-700 dark:text-gray-300">
            Your linked tickers
          </p>
          <div className="flex gap-2">
            <button
              onClick={selectAll}
              className="text-xs text-indigo-600 hover:text-indigo-700 dark:text-indigo-400"
            >
              Select all
            </button>
            <button
              onClick={clearAll}
              className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            >
              Clear
            </button>
          </div>
        </div>

        {tickersLoading ? (
          <div className="flex gap-2 flex-wrap">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="h-8 w-20 animate-pulse rounded-lg bg-gray-200 dark:bg-gray-700"
              />
            ))}
          </div>
        ) : allTickers.length === 0 ? (
          <p className="text-sm text-gray-400 dark:text-gray-500">
            No tickers linked. Add tickers in the Marketplace first.
          </p>
        ) : (
          <div className="flex gap-2 flex-wrap">
            {allTickers.map((t) => {
              const isSelected = selected.has(t);
              return (
                <button
                  key={t}
                  onClick={() => toggle(t)}
                  className={`rounded-lg px-3 py-1.5 text-sm font-mono font-medium transition-colors ${
                    isSelected
                      ? "bg-indigo-600 text-white"
                      : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
                  }`}
                >
                  {t}
                </button>
              );
            })}
          </div>
        )}

        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={fetchCompare}
            disabled={selected.size < 2 || loading}
            className="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700 disabled:opacity-40"
          >
            {loading ? "Loading..." : "Compare"}
          </button>
          <span className="text-xs text-gray-400 dark:text-gray-500">
            {selected.size} selected
            {selected.size < 2 ? " (min 2)" : ""}
          </span>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && <Skeleton />}

      {/* Results */}
      {data && !loading && (
        <>
          {/* Normalized price chart */}
          <div className="rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900 overflow-hidden">
            <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-100 dark:border-gray-800">
              <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
                Normalized Price (base = 100)
              </h2>
              <div className="flex items-center gap-3 text-[10px] text-gray-400 dark:text-gray-500">
                {data.series.map((s, i) => (
                  <span
                    key={s.ticker}
                    className="flex items-center gap-1"
                  >
                    <span
                      className="inline-block w-3 h-0.5"
                      style={{
                        backgroundColor:
                          COMPARE_COLORS[
                            i % COMPARE_COLORS.length
                          ],
                      }}
                    />
                    {s.ticker}
                  </span>
                ))}
              </div>
            </div>
            <CompareChart
              series={data.series}
              isDark={isDark}
              height={400}
            />
          </div>

          {/* Metrics table */}
          {data.metrics.length > 0 && (
            <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
              <h2 className="mb-3 text-sm font-semibold text-gray-700 dark:text-gray-300">
                Comparison Metrics
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-200 dark:border-gray-700">
                      <th className="py-2 pr-3 text-left font-medium text-gray-500 dark:text-gray-400">
                        Ticker
                      </th>
                      <th className="py-2 pr-3 text-right font-medium text-gray-500 dark:text-gray-400">
                        Price
                      </th>
                      <th className="py-2 pr-3 text-right font-medium text-gray-500 dark:text-gray-400">
                        Ann. Return
                      </th>
                      <th className="py-2 pr-3 text-right font-medium text-gray-500 dark:text-gray-400">
                        Volatility
                      </th>
                      <th className="py-2 pr-3 text-right font-medium text-gray-500 dark:text-gray-400">
                        Sharpe
                      </th>
                      <th className="py-2 pr-3 text-right font-medium text-gray-500 dark:text-gray-400">
                        Max DD
                      </th>
                      <th className="py-2 pr-3 text-right font-medium text-gray-500 dark:text-gray-400" title="Relative Strength Index (14-day)">
                        RSI
                      </th>
                      <th className="py-2 pr-3 text-center font-medium text-gray-500 dark:text-gray-400" title="MACD crossover signal">
                        MACD
                      </th>
                      <th className="py-2 text-center font-medium text-gray-500 dark:text-gray-400">
                        Sentiment
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.metrics.map((m: CompareMetric) => {
                      const bestReturn = Math.max(
                        ...data.metrics
                          .map((x: CompareMetric) => x.annualized_return_pct ?? -Infinity),
                      );
                      const isBest =
                        m.annualized_return_pct != null &&
                        m.annualized_return_pct === bestReturn;
                      return (
                      <tr
                        key={m.ticker}
                        className="border-b border-gray-100 dark:border-gray-800"
                      >
                        <td className="py-2 pr-3 font-mono font-bold text-gray-900 dark:text-gray-100">
                          {isBest && <span title="Best performer">{"\u{1F3C6}"} </span>}
                          {m.ticker}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono text-gray-700 dark:text-gray-300">
                          {m.current_price != null
                            ? `${m.currency === "INR" ? "\u20B9" : "$"}${m.current_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                            : "\u2014"}
                        </td>
                        <td
                          className={`py-2 pr-3 text-right font-mono ${metricBadge(m.annualized_return_pct)}`}
                        >
                          {fmt(m.annualized_return_pct, 1)}%
                        </td>
                        <td className="py-2 pr-3 text-right font-mono text-gray-700 dark:text-gray-300">
                          {fmt(m.annualized_volatility_pct, 1)}%
                        </td>
                        <td
                          className={`py-2 pr-3 text-right font-mono ${metricBadge(m.sharpe_ratio)}`}
                        >
                          {fmt(m.sharpe_ratio)}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono text-red-600 dark:text-red-400">
                          {m.max_drawdown_pct != null
                            ? `${m.max_drawdown_pct.toFixed(1)}%`
                            : "\u2014"}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono text-gray-700 dark:text-gray-300">
                          {m.rsi_14 != null ? m.rsi_14.toFixed(1) : "\u2014"}
                        </td>
                        <td className="py-2 pr-3 text-center">
                          {m.macd_signal ? (
                            <span
                              className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                                m.macd_signal === "Bullish"
                                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                                  : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                              }`}
                            >
                              {m.macd_signal}
                            </span>
                          ) : "\u2014"}
                        </td>
                        <td className="py-2 text-center">
                          {m.sentiment ? (
                            <span
                              className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                                m.sentiment === "Bullish"
                                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                                  : m.sentiment === "Bearish"
                                    ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                                    : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                              }`}
                            >
                              {m.sentiment}
                            </span>
                          ) : "\u2014"}
                        </td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function ComparePage() {
  return <CompareContent />;
}
