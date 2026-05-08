"use client";
/**
 * Shared table for the Advanced Analytics 7-tab page
 * (Sprint 9 AA-11). Parameterised by report name + a
 * column catalog (``columnCatalogs.ts``).
 *
 * Mirrors §5.4 tabular-page-pattern:
 * - ``useColumnSelection`` (localStorage-backed) +
 *   ``<ColumnSelector />`` popover for visible cols.
 * - ``visibleCols`` is the single source of truth — table
 *   header/body + ``<DownloadCsvButton />`` consume the
 *   exact same set, never diverge.
 * - Server-side sort + pagination via the SWR hook
 *   (``useAdvancedAnalyticsReport``). Default page size
 *   25; column-header click toggles sort direction +
 *   re-keys SWR.
 * - Stale-ticker chip in the panel-title row (§5.5)
 *   hidden when ``stale_tickers`` is empty.
 *
 * Locked identity column: ``ticker`` (always visible in
 * the column selector).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { ColumnSelector } from "@/components/insights/ColumnSelector";
import {
  DownloadCsvButton,
} from "@/components/common/DownloadCsvButton";
import {
  StaleTickerChip,
  type StaleChipItem,
} from "@/components/common/StaleTickerChip";
import { useAdvancedAnalyticsReport } from "@/hooks/useAdvancedAnalyticsData";
import { useFilterParams } from "@/hooks/useFilterParams";
import { triggerCsvDownload } from "@/lib/triggerCsvDownload";
import { useColumnSelection } from "@/lib/useColumnSelection";
import { API_URL } from "@/lib/config";
import {
  ADVANCED_REPORT_LABELS,
  FILTER_EXPORT_ROW_CAP,
  MARKET_FILTER_OPTIONS,
  TICKER_TYPE_FILTER_OPTIONS,
  type AdvancedReportName,
  type AdvancedReportResponse,
  type AdvancedRow,
  type MarketFilter,
  type StaleReason,
  type TickerTypeFilter,
} from "@/lib/types/advancedAnalytics";

import { ActiveFilterChips } from "./ActiveFilterChips";
import { FilterDropdown } from "./FilterDropdown";
import {
  FUND_FILTER_CATALOG,
  TECH_FILTER_CATALOG,
} from "./filterCatalogs";
import {
  ALL_VALID_KEYS,
  COLUMN_MAP,
  getCatalog,
  type AdvancedColumnKey,
  type AdvancedColumnSpec,
} from "./columnCatalogs";

const DEFAULT_PAGE_SIZE = 25;
const LOCKED_KEYS: string[] = ["ticker"];

/** Classify a row's golden-cross state.
 *  "recent"      — cross happened ≤ 10 trading days ago (amber).
 *  "established" — SMA 50 > SMA 200 but cross is older (light green).
 *  null          — no golden cross. */
function goldenCrossState(
  row: AdvancedRow,
): "recent" | "established" | null {
  if (row.golden_cross_days_ago == null) return null;
  return row.golden_cross_days_ago <= 10 ? "recent" : "established";
}

function stockAnalysisUrl(ticker: string): string {
  return `/analytics/analysis?ticker=${encodeURIComponent(ticker)}&tab=analysis`;
}

function ChartIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-3.5 w-3.5 flex-shrink-0"
      aria-hidden="true"
    >
      <polyline points="1,12 5,7 8,9 12,4 15,6" />
      <polyline points="12,4 15,4 15,7" />
    </svg>
  );
}

const STALE_REASON_LABEL: Record<StaleReason, string> = {
  nan_close: "missing close",
  missing_delivery: "no delivery feed",
  missing_quarterly: "no quarterly data",
  missing_promoter: "no promoter data",
};

interface Props {
  report: AdvancedReportName;
  /** Optional fallbackData passed by the RSC for the
   *  first tab (avoids client-side waterfall on initial
   *  paint). */
  initialData?: AdvancedReportResponse;
}

export function AdvancedAnalyticsTable({ report, initialData }: Props) {
  const { catalog, defaults, storageKey } = getCatalog(report);
  const [selected, setSelected, resetCols] = useColumnSelection(
    storageKey,
    defaults,
    ALL_VALID_KEYS,
  );
  const [page, setPage] = useState(1);
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  // Default scope: NSE-bhavcopy reports are India-only
  // (delivery feed is NSE) and most users care about
  // tradable stocks, not ETFs. Users can flip either
  // dropdown to broaden the view.
  const [market, setMarket] = useState<MarketFilter>("india");
  const [tickerType, setTickerType] =
    useState<TickerTypeFilter>("stock");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");

  const { tech, fund, setTech, setFund, resetAll } = useFilterParams();

  const removeTech = useCallback(
    (key: string) => setTech(tech.filter((k) => k !== key)),
    [setTech, tech],
  );
  const removeFund = useCallback(
    (key: string) => setFund(fund.filter((k) => k !== key)),
    [setFund, fund],
  );

  // Filters change the result set — reset pagination at the
  // setter site so a stale ?page=4 can't render an empty
  // body. (Avoids the eslint set-state-in-effect cascade.)
  const handleMarketChange = useCallback((next: MarketFilter) => {
    setMarket(next);
    setPage(1);
  }, []);
  const handleTickerTypeChange = useCallback(
    (next: TickerTypeFilter) => {
      setTickerType(next);
      setPage(1);
    },
    [],
  );

  // Debounce ticker search 300 ms — avoids one fetch per
  // keystroke while user is typing (e.g. "RELIANCE").
  useEffect(() => {
    const id = window.setTimeout(() => {
      setSearch((prev) => {
        const next = searchInput.trim().toUpperCase();
        if (next !== prev) setPage(1);
        return next;
      });
    }, 300);
    return () => window.clearTimeout(id);
  }, [searchInput]);

  const { value, loading, error } = useAdvancedAnalyticsReport(
    report,
    page,
    DEFAULT_PAGE_SIZE,
    sortKey,
    sortDir,
    market,
    tickerType,
    search,
    tech,
    fund,
    initialData,
  );

  const visibleCols = useMemo<AdvancedColumnSpec[]>(() => {
    const seen = new Set<string>();
    const order: string[] = [];
    for (const k of LOCKED_KEYS) {
      if (!seen.has(k)) {
        seen.add(k);
        order.push(k);
      }
    }
    for (const k of selected) {
      if (!seen.has(k)) {
        seen.add(k);
        order.push(k);
      }
    }
    return order
      .map((k) => COLUMN_MAP.get(k as AdvancedColumnKey))
      .filter((c): c is AdvancedColumnSpec => c !== undefined);
  }, [selected]);

  const handleSort = useCallback(
    (key: AdvancedColumnKey) => {
      if (sortKey === key) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setSortKey(key);
        setSortDir("desc");
      }
      setPage(1);
    },
    [sortKey],
  );

  const handleCsv = useCallback(async () => {
    if (!value || value.rows.length === 0) return;
    const params = new URLSearchParams({
      sort_dir: sortDir,
      market,
      ticker_type: tickerType,
    });
    if (sortKey) params.set("sort_key", sortKey);
    if (search) params.set("search", search);
    if (tech.length > 0) params.set("tech", [...tech].sort().join(","));
    if (fund.length > 0) params.set("fund", [...fund].sort().join(","));
    params.set("columns", visibleCols.map((c) => c.key).join(","));
    const url = `${API_URL}/advanced-analytics/${report}/export?${params.toString()}`;
    try {
      await triggerCsvDownload(url);
    } catch (err) {
      console.error("CSV export failed", err);
    }
  }, [
    value,
    sortDir,
    sortKey,
    market,
    tickerType,
    search,
    tech,
    fund,
    visibleCols,
    report,
  ]);

  const csvDisabled =
    !value ||
    value.rows.length === 0 ||
    value.total > FILTER_EXPORT_ROW_CAP;
  const csvTooltip =
    value && value.total > FILTER_EXPORT_ROW_CAP
      ? `Export exceeds ${FILTER_EXPORT_ROW_CAP.toLocaleString("en-IN")} rows; tighten filters`
      : undefined;

  const totalPages = value
    ? Math.max(1, Math.ceil(value.total / DEFAULT_PAGE_SIZE))
    : 1;

  const staleItems: StaleChipItem[] = useMemo(() => {
    if (!value) return [];
    return value.stale_tickers.map((s) => ({
      key: s.ticker,
      primary: s.ticker,
      secondary: STALE_REASON_LABEL[s.reason] ?? s.reason,
    }));
  }, [value]);

  const emptyMsg =
    tech.length || fund.length
      ? "No rows match your current filters. Try removing one or clicking 'Clear all'."
      : "No rows match this report's filter today.";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            {ADVANCED_REPORT_LABELS[report]}
          </h2>
          <StaleTickerChip
            items={staleItems}
            summaryLabel={
              staleItems.length === 1
                ? "ticker w/ stale inputs"
                : "tickers w/ stale inputs"
            }
            tooltipTitle="Tickers omitted from / partially in this report:"
            tooltipFooter="Stale rows are skipped from sort & filter; counts auto-clear when upstream data lands."
            testId={`advanced-analytics-stale-${report}`}
          />
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search ticker…"
            maxLength={20}
            data-testid={`advanced-analytics-search-${report}`}
            aria-label="Search by ticker"
            className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs text-gray-700 dark:text-gray-200 placeholder:text-gray-400 dark:placeholder:text-gray-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 w-32 sm:w-40"
          />
          <select
            value={market}
            onChange={(e) =>
              handleMarketChange(e.target.value as MarketFilter)
            }
            data-testid={`advanced-analytics-market-${report}`}
            aria-label="Filter by market"
            className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            {MARKET_FILTER_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <select
            value={tickerType}
            onChange={(e) =>
              handleTickerTypeChange(e.target.value as TickerTypeFilter)
            }
            data-testid={`advanced-analytics-ticker-type-${report}`}
            aria-label="Filter by ticker type"
            className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            {TICKER_TYPE_FILTER_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <FilterDropdown
            bundleId="tech"
            bundleLabel="Technical"
            catalog={TECH_FILTER_CATALOG}
            selected={tech}
            onChange={setTech}
            onReset={() => setTech([])}
          />
          <FilterDropdown
            bundleId="fund"
            bundleLabel="Fundamentals"
            catalog={FUND_FILTER_CATALOG}
            selected={fund}
            onChange={setFund}
            onReset={() => setFund([])}
          />
          <ColumnSelector
            catalog={catalog}
            selected={selected}
            onChange={setSelected}
            onReset={resetCols}
            lockedKeys={LOCKED_KEYS}
          />
          <DownloadCsvButton
            onClick={handleCsv}
            disabled={csvDisabled}
            title={csvTooltip}
          />
        </div>
      </div>

      <ActiveFilterChips
        tech={tech}
        fund={fund}
        onRemoveTech={removeTech}
        onRemoveFund={removeFund}
        onClearAll={resetAll}
      />

      {error && (
        <div
          className="rounded-md border border-red-200 bg-red-50 dark:border-red-900/50 dark:bg-red-900/20 px-3 py-2 text-sm text-red-700 dark:text-red-400"
          role="alert"
        >
          Failed to load: {error}
        </div>
      )}

      <div
        className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700"
        data-testid={`advanced-analytics-table-${report}`}
      >
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800/50">
            <tr>
              {visibleCols.map((col) => {
                const active = sortKey === col.key;
                const arrow = active ? (sortDir === "desc" ? "▼" : "▲") : "";
                return (
                  <th
                    key={col.key}
                    scope="col"
                    className={`whitespace-nowrap px-3 py-2 text-xs font-medium text-gray-600 dark:text-gray-300 ${
                      col.numeric ? "text-right" : "text-left"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => handleSort(col.key)}
                      data-testid={`advanced-analytics-sort-${col.key}`}
                      className="inline-flex items-center gap-1 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors"
                    >
                      {col.label}
                      {arrow && (
                        <span className="text-[10px]">{arrow}</span>
                      )}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800 bg-white dark:bg-gray-900">
            {loading && !value ? (
              <tr>
                <td
                  colSpan={visibleCols.length}
                  className="px-3 py-6 text-center text-xs text-gray-500"
                >
                  Loading {ADVANCED_REPORT_LABELS[report]}…
                </td>
              </tr>
            ) : value && value.rows.length > 0 ? (
              value.rows.map((row) => {
                const gcState = goldenCrossState(row);
                const rowTitle =
                  gcState === "recent"
                    ? `Golden Cross (${row.golden_cross_days_ago}d ago): SMA 50 just crossed above SMA 200`
                    : gcState === "established"
                      ? "Bullish: SMA 50 has been above SMA 200 for an extended period"
                      : undefined;
                const rowClass =
                  gcState === "recent"
                    ? "bg-amber-50 dark:bg-amber-900/20 hover:bg-amber-100 dark:hover:bg-amber-900/30"
                    : gcState === "established"
                      ? "bg-green-50 dark:bg-green-900/20 hover:bg-green-100 dark:hover:bg-green-900/30"
                      : "hover:bg-gray-50 dark:hover:bg-gray-800/50";
                return (
                  <tr
                    key={row.ticker}
                    title={rowTitle}
                    className={rowClass}
                  >
                    {visibleCols.map((col) => {
                      const raw = row[col.key];
                      const text = col.format
                        ? col.format(raw)
                        : raw == null
                          ? "—"
                          : String(raw);
                      return (
                        <td
                          key={col.key}
                          className={`whitespace-nowrap px-3 py-2 ${
                            col.numeric
                              ? "text-right tabular-nums text-gray-700 dark:text-gray-200"
                              : "text-gray-700 dark:text-gray-200"
                          }`}
                        >
                          {col.key === "ticker" ? (
                            <span className="inline-flex items-center gap-1.5">
                              <a
                                href={stockAnalysisUrl(row.ticker)}
                                target="_blank"
                                rel="noopener noreferrer"
                                title="Open stock analysis chart"
                                data-testid={`aa-chart-link-${row.ticker}`}
                                className="text-indigo-500 hover:text-indigo-700 dark:text-indigo-400 dark:hover:text-indigo-300 transition-colors"
                              >
                                <ChartIcon />
                              </a>
                              <span className="font-mono">{text}</span>
                              {gcState === "recent" && (
                                <span
                                  title={`Golden Cross ${row.golden_cross_days_ago}d ago`}
                                  aria-label="Recent golden cross"
                                  className="text-amber-500 text-[10px] font-bold leading-none select-none"
                                >
                                  ✦
                                </span>
                              )}
                              {gcState === "established" && (
                                <span
                                  title="Extended bullish: SMA 50 > SMA 200"
                                  aria-label="Established bullish"
                                  className="text-green-600 dark:text-green-400 text-[10px] font-bold leading-none select-none"
                                >
                                  ▲
                                </span>
                              )}
                            </span>
                          ) : (
                            text
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })
            ) : (
              <tr>
                <td
                  colSpan={visibleCols.length}
                  className="px-3 py-8 text-center text-xs text-gray-500"
                >
                  {emptyMsg}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {value && value.total > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500 dark:text-gray-400">
          <span>
            Showing {(value.page - 1) * value.page_size + 1}–
            {Math.min(
              value.page * value.page_size,
              value.total,
            )}{" "}
            of {value.total.toLocaleString("en-IN")} rows
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="rounded-md border border-gray-300 dark:border-gray-700 px-2 py-1 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-100 dark:hover:bg-gray-800"
              data-testid={`advanced-analytics-prev-${report}`}
            >
              Prev
            </button>
            <span className="px-2">
              Page {value.page} / {totalPages}
            </span>
            <button
              type="button"
              onClick={() =>
                setPage((p) => Math.min(totalPages, p + 1))
              }
              disabled={page >= totalPages}
              className="rounded-md border border-gray-300 dark:border-gray-700 px-2 py-1 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-100 dark:hover:bg-gray-800"
              data-testid={`advanced-analytics-next-${report}`}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
