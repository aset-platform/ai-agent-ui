"use client";
/**
 * Daily-factor-coverage admin dashboard — sibling of
 * FeatureCoverageTab for the WIDE ``stocks.daily_factors`` table
 * (one column per factor; coverage = % of (ticker, bar_date)
 * rows non-null per column).
 *
 * UX intentionally mirrors FeatureCoverageTab so an admin
 * familiar with one immediately understands the other:
 *   - Pickers: period_start / period_end (default = last 30 days).
 *     No interval picker (daily-only) and no version override
 *     (the wide-table schema is its own version contract).
 *   - Tabular page with ColumnSelector + DownloadCsvButton per
 *     CLAUDE.md §5.4.
 *   - Stale-data chip per §5.5 when any factor < 95 %.
 *
 * Auth: superuser-only — mounts under the admin tab strip which
 * is itself role-filtered.
 */

import { useMemo, useState } from "react";

import { ColumnSelector } from "@/components/insights/ColumnSelector";
import type { ColumnSpec } from "@/components/insights/ColumnSelector";
import { DownloadCsvButton } from "@/components/common/DownloadCsvButton";
import { useColumnSelection } from "@/lib/useColumnSelection";
import { downloadCsv } from "@/lib/downloadCsv";
import type { CsvColumn } from "@/lib/downloadCsv";
import {
  useDailyFactorCoverage,
  type DailyFactorCoverageRow,
} from "@/hooks/useDailyFactorCoverage";

const STORAGE_KEY = "daily-factor-coverage:cols";
const STALE_THRESHOLD_PCT = 95;

const COL_CATALOG: ColumnSpec[] = [
  { key: "factor_name", label: "Factor", category: "Identity" },
  {
    key: "coverage_pct",
    label: "Coverage %",
    category: "Coverage",
  },
  {
    key: "non_null_rows",
    label: "Non-null Rows",
    category: "Coverage",
  },
  {
    key: "tickers_seen",
    label: "Tickers Seen",
    category: "Coverage",
  },
  {
    key: "sample_chart",
    label: "Coverage Bar",
    category: "Visual",
  },
];

const DEFAULT_COLS = [
  "factor_name",
  "coverage_pct",
  "non_null_rows",
  "tickers_seen",
  "sample_chart",
];

const VALID_KEYS = COL_CATALOG.map((c) => c.key);

type SortDir = "asc" | "desc";

interface SortState {
  col: keyof DailyFactorCoverageRow | "coverage_pct";
  dir: SortDir;
}

function isoDaysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

function CoverageBar({ pct }: { pct: number }) {
  const clamped = Math.max(0, Math.min(100, pct));
  let cls = "bg-green-500 dark:bg-green-400";
  if (clamped < 70) {
    cls = "bg-red-500 dark:bg-red-400";
  } else if (clamped < STALE_THRESHOLD_PCT) {
    cls = "bg-amber-500 dark:bg-amber-400";
  }
  return (
    <div
      className="h-2 w-24 overflow-hidden rounded bg-gray-200 dark:bg-gray-700"
      data-testid="coverage-bar"
      aria-label={`Coverage ${clamped.toFixed(1)} percent`}
    >
      <div
        className={`h-full ${cls}`}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

function StaleCoverageChip({ staleCount }: { staleCount: number }) {
  if (staleCount === 0) return null;
  return (
    <span
      data-testid="stale-coverage-chip"
      title={
        `${staleCount} factor(s) have coverage below ` +
        `${STALE_THRESHOLD_PCT}% — investigate the daily factor ` +
        `compute job or upstream OHLCV gaps.`
      }
      className={
        "ml-2 inline-flex items-center rounded-full " +
        "bg-amber-100 dark:bg-amber-900/40 " +
        "px-2 py-0.5 text-xs font-medium " +
        "text-amber-700 dark:text-amber-300"
      }
    >
      {staleCount} below {STALE_THRESHOLD_PCT}%
    </span>
  );
}

export function DailyFactorCoverageTab() {
  const [periodStart, setPeriodStart] = useState<string>(() =>
    isoDaysAgo(30),
  );
  const [periodEnd, setPeriodEnd] = useState<string>(() =>
    isoDaysAgo(0),
  );

  const [selectedCols, setSelectedCols, resetCols] =
    useColumnSelection(STORAGE_KEY, DEFAULT_COLS, VALID_KEYS);

  const [sort, setSort] = useState<SortState>({
    col: "coverage_pct",
    dir: "desc",
  });

  const queryArgs = useMemo(
    () =>
      periodStart && periodEnd
        ? { periodStart, periodEnd }
        : null,
    [periodStart, periodEnd],
  );

  const { data, error, loading } = useDailyFactorCoverage(queryArgs);

  const rows = useMemo(() => data?.coverage ?? [], [data]);

  const sortedRows = useMemo(() => {
    const sorted = [...rows];
    sorted.sort((a, b) => {
      const av = a[sort.col as keyof DailyFactorCoverageRow];
      const bv = b[sort.col as keyof DailyFactorCoverageRow];
      if (typeof av === "number" && typeof bv === "number") {
        return sort.dir === "asc" ? av - bv : bv - av;
      }
      const as = String(av);
      const bs = String(bv);
      return sort.dir === "asc"
        ? as.localeCompare(bs)
        : bs.localeCompare(as);
    });
    return sorted;
  }, [rows, sort]);

  const staleCount = useMemo(
    () => rows.filter((r) => r.coverage_pct < STALE_THRESHOLD_PCT).length,
    [rows],
  );

  const visibleCols = useMemo(
    () => COL_CATALOG.filter((c) => selectedCols.includes(c.key)),
    [selectedCols],
  );

  const handleSort = (
    col: keyof DailyFactorCoverageRow | "coverage_pct",
  ) => {
    setSort((prev) =>
      prev.col === col
        ? { col, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { col, dir: "desc" },
    );
  };

  const handleDownload = () => {
    const csvCols: CsvColumn<DailyFactorCoverageRow>[] = visibleCols
      .filter((c) => c.key !== "sample_chart")
      .map((c) => ({
        key: c.key as keyof DailyFactorCoverageRow,
        header: c.label,
      }));
    downloadCsv(sortedRows, csvCols, "daily-factor-coverage");
  };

  return (
    <div
      data-testid="daily-factor-coverage-tab"
      className="space-y-4"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Daily Factor Coverage
        </h2>
        <StaleCoverageChip staleCount={staleCount} />
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col text-xs text-gray-600 dark:text-gray-400">
          From
          <input
            data-testid="dfc-period-start"
            type="date"
            value={periodStart}
            onChange={(e) => setPeriodStart(e.target.value)}
            className="mt-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col text-xs text-gray-600 dark:text-gray-400">
          To
          <input
            data-testid="dfc-period-end"
            type="date"
            value={periodEnd}
            onChange={(e) => setPeriodEnd(e.target.value)}
            className="mt-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100"
          />
        </label>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-gray-600 dark:text-gray-400">
          {data && (
            <span data-testid="dfc-summary">
              {data.coverage.length} factors ·{" "}
              {data.total_rows.toLocaleString()} rows ·{" "}
              {data.tickers_total} tickers
            </span>
          )}
        </div>
        <ColumnSelector
          catalog={COL_CATALOG}
          selected={selectedCols}
          onChange={setSelectedCols}
          onReset={resetCols}
          lockedKeys={["factor_name"]}
        />
      </div>

      {error && (
        <div
          data-testid="dfc-error"
          className="rounded-md border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/30 p-3 text-sm text-red-700 dark:text-red-300"
        >
          Failed to load coverage: {error.message}
        </div>
      )}

      {loading && !data && (
        <div
          data-testid="dfc-loading"
          className="space-y-2"
          aria-busy="true"
        >
          {[0, 1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="h-6 w-full animate-pulse rounded bg-gray-100 dark:bg-gray-800"
            />
          ))}
        </div>
      )}

      {!loading && rows.length === 0 && !error && (
        <div
          data-testid="dfc-empty"
          className="rounded-md border border-gray-200 dark:border-gray-700 p-6 text-center text-sm text-gray-500 dark:text-gray-400"
        >
          No factor rows in the selected window. Try widening the
          date range or check that ``compute_daily_factors`` has
          run.
        </div>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-gray-200 dark:border-gray-700">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700 text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800">
              <tr>
                {visibleCols.map((c) => (
                  <th
                    key={c.key}
                    scope="col"
                    onClick={() =>
                      c.key === "sample_chart"
                        ? undefined
                        : handleSort(
                            c.key as keyof DailyFactorCoverageRow,
                          )
                    }
                    className={
                      "px-3 py-2 text-left text-xs font-medium " +
                      "text-gray-500 dark:text-gray-400 uppercase " +
                      (c.key === "sample_chart"
                        ? ""
                        : "cursor-pointer select-none")
                    }
                    data-testid={`dfc-header-${c.key}`}
                  >
                    {c.label}
                    {sort.col === c.key && (
                      <span aria-hidden>
                        {" "}
                        {sort.dir === "asc" ? "▲" : "▼"}
                      </span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800 bg-white dark:bg-gray-900">
              {sortedRows.map((r) => (
                <tr
                  key={r.factor_name}
                  data-testid={`dfc-row-${r.factor_name}`}
                >
                  {visibleCols.map((c) => {
                    if (c.key === "sample_chart") {
                      return (
                        <td key={c.key} className="px-3 py-2">
                          <CoverageBar pct={r.coverage_pct} />
                        </td>
                      );
                    }
                    if (c.key === "coverage_pct") {
                      return (
                        <td
                          key={c.key}
                          className="px-3 py-2 font-mono text-gray-900 dark:text-gray-100"
                        >
                          {r.coverage_pct.toFixed(2)}%
                        </td>
                      );
                    }
                    if (c.key === "factor_name") {
                      return (
                        <td
                          key={c.key}
                          className="px-3 py-2 font-medium text-gray-900 dark:text-gray-100"
                        >
                          {r.factor_name}
                        </td>
                      );
                    }
                    const val =
                      r[c.key as keyof DailyFactorCoverageRow];
                    return (
                      <td
                        key={c.key}
                        className="px-3 py-2 text-gray-700 dark:text-gray-300"
                      >
                        {typeof val === "number"
                          ? val.toLocaleString()
                          : String(val)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex justify-end border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 px-3 py-2">
            <DownloadCsvButton
              onClick={handleDownload}
              disabled={sortedRows.length === 0}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default DailyFactorCoverageTab;
