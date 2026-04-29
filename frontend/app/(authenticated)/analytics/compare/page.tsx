"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import dynamic from "next/dynamic";

const CompareChart = dynamic(
  () =>
    import("@/components/charts/CompareChart").then(
      (m) => m.CompareChart,
    ),
  {
    ssr: false,
    loading: () => (
      <div className="flex items-center justify-center h-64 bg-gray-100 dark:bg-gray-800 rounded-lg animate-pulse">
        <span className="text-sm text-gray-400">
          Loading chart...
        </span>
      </div>
    ),
  },
);

// Inline color constants to avoid pulling in
// lightweight-charts via CompareChart module.
const COMPARE_COLORS = [
  "#6366f1", "#8b5cf6", "#ec4899", "#f59e0b",
  "#10b981", "#3b82f6", "#ef4444", "#06b6d4",
];
import { useTheme } from "@/hooks/useTheme";
import type {
  CompareResponse,
  CompareMetric,
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
// ---------------------------------------------------------------
// Multi-select dropdown with search
// ---------------------------------------------------------------

function TickerMultiSelect({
  tickers,
  selected,
  onToggle,
  onSelectAll,
  onClear,
  loading: tickersLoading,
  maxSelection,
}: {
  tickers: string[];
  selected: Set<string>;
  onToggle: (ticker: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
  loading: boolean;
  maxSelection: number;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        ref.current &&
        !ref.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () =>
      document.removeEventListener(
        "mousedown", handler,
      );
  }, []);

  const filtered = useMemo(() => {
    if (!search.trim()) return tickers;
    const q = search.trim().toUpperCase();
    return tickers.filter((t) =>
      t.toUpperCase().includes(q),
    );
  }, [tickers, search]);

  // tickersLoading short-circuit hid the static "Select tickers
  // to compare" label until SWR resolved (~5 s on perf runs).
  // Render the structure always; an empty `tickers` array yields
  // the same empty state path one tick later.
  if (!tickersLoading && tickers.length === 0) {
    return (
      <div
        data-testid="compare-empty"
        className="rounded-2xl border border-gray-200
          bg-white p-5 text-center text-sm text-gray-400
          dark:border-gray-800 dark:bg-gray-900/80
          dark:text-gray-500"
      >
        No tickers linked. Link tickers on the
        Analysis page first.
      </div>
    );
  }

  return (
    <div
      ref={ref}
      data-testid="compare-ticker-select"
      className="rounded-2xl border border-gray-200
        bg-white p-4 dark:border-gray-800
        dark:bg-gray-900/80"
    >
      {/* Label row */}
      <div className="mb-2.5 flex items-center justify-between">
        <p className="text-sm font-semibold text-gray-700 dark:text-gray-300">
          Select tickers to compare
        </p>
        <div className="flex gap-3">
          <button
            onClick={onSelectAll}
            className="text-xs font-medium text-indigo-600
              hover:text-indigo-700 dark:text-indigo-400
              dark:hover:text-indigo-300"
          >
            Select all
          </button>
          <button
            onClick={onClear}
            className="text-xs font-medium text-gray-400
              hover:text-gray-600
              dark:hover:text-gray-300"
          >
            Clear
          </button>
        </div>
      </div>

      {/* Search input / trigger */}
      <div className="relative">
        <div
          onClick={() => setOpen((p) => !p)}
          className={`
            flex cursor-pointer items-center gap-2
            rounded-xl border bg-gray-50 px-3.5
            py-2.5 transition-all
            ${
              open
                ? "border-indigo-500 ring-[3px] ring-indigo-500/25"
                : "border-gray-200 dark:border-gray-700"
            }
            dark:bg-gray-800
          `}
        >
          <svg
            viewBox="0 0 24 24"
            className="h-4 w-4 shrink-0 text-gray-400
              dark:text-gray-500"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="11" cy="11" r="8" />
            <path d="m21 21-4.35-4.35" />
          </svg>
          <input
            type="text"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              if (!open) setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            placeholder={
              selected.size > 0
                ? `${selected.size} selected — search to add more...`
                : "Search tickers..."
            }
            className="flex-1 bg-transparent font-mono
              text-[13px] text-gray-900 placeholder-gray-400
              outline-none dark:text-gray-100
              dark:placeholder-gray-500"
          />
          <svg
            viewBox="0 0 24 24"
            className={`h-4 w-4 shrink-0 text-gray-400
              transition-transform duration-200
              ${open ? "rotate-180" : ""}
            `}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m6 9 6 6 6-6" />
          </svg>
        </div>

        {/* Dropdown */}
        {open && (
          <div
            className="absolute left-0 right-0
              top-[calc(100%+6px)] z-50 max-h-60
              overflow-y-auto rounded-xl border
              border-gray-200 bg-white p-1.5
              shadow-[0_12px_48px_rgba(0,0,0,0.12)]
              dark:border-gray-700 dark:bg-gray-800
              dark:shadow-[0_12px_48px_rgba(0,0,0,0.5)]"
          >
            {filtered.length === 0 ? (
              <p className="px-3 py-4 text-center text-xs text-gray-400 dark:text-gray-500">
                No tickers match &quot;{search}&quot;
              </p>
            ) : (
              filtered.map((t) => {
                const isSelected = selected.has(t);
                const atMax =
                  selected.size >= maxSelection &&
                  !isSelected;
                return (
                  <button
                    key={t}
                    disabled={atMax}
                    onClick={() => {
                      onToggle(t);
                    }}
                    className={`
                      flex w-full items-center gap-2.5
                      rounded-lg px-3 py-2 text-left
                      text-[13px] font-mono
                      transition-colors
                      ${
                        isSelected
                          ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/12 dark:text-indigo-400"
                          : atMax
                            ? "cursor-not-allowed text-gray-300 dark:text-gray-600"
                            : "text-gray-700 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-700/50"
                      }
                    `}
                  >
                    <div
                      className={`
                        flex h-4 w-4 shrink-0
                        items-center justify-center
                        rounded border transition-colors
                        ${
                          isSelected
                            ? "border-indigo-600 bg-indigo-600"
                            : "border-gray-300 dark:border-gray-600"
                        }
                      `}
                    >
                      {isSelected && (
                        <svg
                          viewBox="0 0 24 24"
                          className="h-3 w-3 text-white"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="3"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      )}
                    </div>
                    <span className="font-medium">
                      {t}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        )}
      </div>

      {/* Selected chips */}
      {selected.size > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {Array.from(selected).map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1
                rounded-full bg-indigo-50 px-2.5 py-1
                text-[11px] font-semibold text-indigo-700
                dark:bg-indigo-500/12 dark:text-indigo-400"
            >
              {t}
              <button
                onClick={() => onToggle(t)}
                className="ml-0.5 rounded-full p-0.5
                  transition-colors hover:bg-indigo-200
                  dark:hover:bg-indigo-500/25"
              >
                <svg
                  viewBox="0 0 24 24"
                  className="h-3 w-3"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Main compare content
// ---------------------------------------------------------------

export function CompareContent() {
  // User tickers from watchlist
  const [allTickers, setAllTickers] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [data, setData] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [tickersLoading, setTickersLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch user's linked tickers + registry tickers
  useEffect(() => {
    const controller = new AbortController();
    const opts = { signal: controller.signal };
    Promise.all([
      apiFetch(
        `${API_URL}/dashboard/watchlist`,
        opts,
      )
        .then((r) =>
          r.ok ? r.json() : { tickers: [] },
        )
        .catch(() => ({ tickers: [] })),
      apiFetch(
        `${API_URL}/dashboard/registry`,
        opts,
      )
        .then((r) =>
          r.ok ? r.json() : { tickers: [] },
        )
        .catch(() => ({ tickers: [] })),
    ])
      .then(([wl, reg]) => {
        const wlList = (wl.tickers ?? []).map(
          (t: { ticker: string }) => t.ticker,
        );
        const regList = (reg.tickers ?? []).map(
          (t: { ticker: string }) => t.ticker,
        );
        const seen = new Set(
          wlList.map((t: string) =>
            t.toUpperCase(),
          ),
        );
        const merged = [...wlList];
        for (const t of regList) {
          if (!seen.has(t.toUpperCase())) {
            merged.push(t);
            seen.add(t.toUpperCase());
          }
        }
        setAllTickers(merged);
      })
      .catch((err: unknown) => {
        if (
          err instanceof Error &&
          err.name === "AbortError"
        )
          return;
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
      <TickerMultiSelect
        tickers={allTickers}
        selected={selected}
        onToggle={toggle}
        onSelectAll={selectAll}
        onClear={clearAll}
        loading={tickersLoading}
        maxSelection={7}
      />

      <div className="flex items-center gap-3">
        <button
          onClick={fetchCompare}
          disabled={selected.size < 2 || loading}
          className="rounded-[10px] bg-indigo-600 px-5
            py-2.5 text-sm font-semibold text-white
            transition-all hover:bg-indigo-700
            hover:-translate-y-0.5
            hover:shadow-[0_4px_14px_rgba(79,70,229,0.2)]
            disabled:opacity-40 disabled:hover:translate-y-0"
        >
          {loading ? "Loading..." : "Compare"}
        </button>
        <span className="font-mono text-xs text-gray-400 dark:text-gray-500">
          {selected.size} selected
          {selected.size < 2 ? " (min 2)" : ""}
          {selected.size >= 7 ? " (max 7)" : ""}
        </span>
      </div>

      {/* Error */}
      {error && (
        <div data-testid="compare-error" className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && <Skeleton />}

      {/* Results */}
      {data && !loading && (
        <>
          {/* Normalized price chart */}
          <div data-testid="compare-chart" className="rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900 overflow-hidden">
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
