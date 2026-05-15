"use client";
/**
 * Feature-coverage admin dashboard (ASETPLTFRM-416 / FE-14).
 *
 * Renders the per-feature coverage matrix for the centralised
 * intraday feature store (``stocks.intraday_features``).
 * Coverage = % of ``(ticker, bar_open_ts_ns)`` bar slots in the
 * window for which a given ``feature_name`` produced a non-null
 * row.
 *
 * UX:
 *   - Pickers: interval_sec (15m / 5m / 1m), period_start /
 *     period_end (default = last 30 days),
 *     feature_set_version (optional override).
 *   - Tabular page with ColumnSelector + DownloadCsvButton per
 *     CLAUDE.md §5.4.
 *   - Stale-data chip per §5.5: amber chip if any feature has
 *     coverage_pct < 95.
 *
 * Auth: superuser-only — page mounts under the admin tab strip
 * which is itself role-filtered; no extra client-side check
 * needed.
 */

import { useMemo, useState } from "react";

import { ColumnSelector } from "@/components/insights/ColumnSelector";
import type { ColumnSpec } from "@/components/insights/ColumnSelector";
import { DownloadCsvButton } from "@/components/common/DownloadCsvButton";
import { useColumnSelection } from "@/lib/useColumnSelection";
import { downloadCsv } from "@/lib/downloadCsv";
import type { CsvColumn } from "@/lib/downloadCsv";
import {
  useFeatureCoverage,
  type FeatureCoverageRow,
} from "@/hooks/useFeatureCoverage";

const STORAGE_KEY = "fe14:feature-coverage:cols";
const STALE_THRESHOLD_PCT = 95;

const COL_CATALOG: ColumnSpec[] = [
  {
    key: "feature_name",
    label: "Feature",
    category: "Identity",
  },
  {
    key: "coverage_pct",
    label: "Coverage %",
    category: "Coverage",
  },
  { key: "rows", label: "Rows", category: "Coverage" },
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
  "feature_name",
  "coverage_pct",
  "rows",
  "tickers_seen",
  "sample_chart",
];

const VALID_KEYS = COL_CATALOG.map((c) => c.key);

type SortDir = "asc" | "desc";

interface SortState {
  col: keyof FeatureCoverageRow | "coverage_pct";
  dir: SortDir;
}

function isoDaysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

function CoverageBar({ pct }: { pct: number }) {
  const clamped = Math.max(0, Math.min(100, pct));
  // Colour ramp: red <70, amber 70–95, green ≥95.
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

function StaleCoverageChip({
  staleCount,
}: {
  staleCount: number;
}) {
  if (staleCount === 0) return null;
  return (
    <span
      data-testid="stale-coverage-chip"
      title={
        `${staleCount} feature(s) have coverage below ` +
        `${STALE_THRESHOLD_PCT}% — investigate warm-up or ` +
        `upstream source gaps.`
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

export function FeatureCoverageTab() {
  const [intervalSec, setIntervalSec] = useState<number>(900);
  const [periodStart, setPeriodStart] = useState<string>(
    () => isoDaysAgo(30),
  );
  const [periodEnd, setPeriodEnd] = useState<string>(() =>
    isoDaysAgo(0),
  );
  const [versionOverride, setVersionOverride] =
    useState<string>("");

  const [selectedCols, setSelectedCols, resetCols] =
    useColumnSelection(STORAGE_KEY, DEFAULT_COLS, VALID_KEYS);

  const [sort, setSort] = useState<SortState>({
    col: "coverage_pct",
    dir: "desc",
  });

  const queryArgs = useMemo(
    () =>
      periodStart && periodEnd
        ? {
            intervalSec,
            periodStart,
            periodEnd,
            featureSetVersion:
              versionOverride.trim() || undefined,
          }
        : null,
    [intervalSec, periodStart, periodEnd, versionOverride],
  );

  const { data, error, loading } = useFeatureCoverage(queryArgs);

  const rows = useMemo(
    () => data?.coverage ?? [],
    [data],
  );

  const sortedRows = useMemo(() => {
    const sorted = [...rows];
    sorted.sort((a, b) => {
      const av = a[sort.col as keyof FeatureCoverageRow];
      const bv = b[sort.col as keyof FeatureCoverageRow];
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
    () =>
      rows.filter(
        (r) => r.coverage_pct < STALE_THRESHOLD_PCT,
      ).length,
    [rows],
  );

  const visibleCols = useMemo(
    () =>
      COL_CATALOG.filter((c) => selectedCols.includes(c.key)),
    [selectedCols],
  );

  const handleSort = (
    col: keyof FeatureCoverageRow | "coverage_pct",
  ) => {
    setSort((prev) =>
      prev.col === col
        ? { col, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { col, dir: "desc" },
    );
  };

  const handleDownload = () => {
    const csvCols: CsvColumn<FeatureCoverageRow>[] = visibleCols
      .filter((c) => c.key !== "sample_chart")
      .map((c) => ({
        key: c.key as keyof FeatureCoverageRow,
        header: c.label,
      }));
    downloadCsv(sortedRows, csvCols, "feature-coverage");
  };

  return (
    <div
      data-testid="feature-coverage-tab"
      className="space-y-4"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Feature Coverage
        </h2>
        <StaleCoverageChip staleCount={staleCount} />
      </div>

      {/* Filter row */}
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col text-xs text-gray-600 dark:text-gray-400">
          Interval
          <select
            data-testid="fc-interval-select"
            value={intervalSec}
            onChange={(e) =>
              setIntervalSec(Number(e.target.value))
            }
            className="mt-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100"
          >
            <option value={900}>15 min</option>
            <option value={300}>5 min</option>
            <option value={60}>1 min</option>
          </select>
        </label>
        <label className="flex flex-col text-xs text-gray-600 dark:text-gray-400">
          From
          <input
            data-testid="fc-period-start"
            type="date"
            value={periodStart}
            onChange={(e) => setPeriodStart(e.target.value)}
            className="mt-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col text-xs text-gray-600 dark:text-gray-400">
          To
          <input
            data-testid="fc-period-end"
            type="date"
            value={periodEnd}
            onChange={(e) => setPeriodEnd(e.target.value)}
            className="mt-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col text-xs text-gray-600 dark:text-gray-400">
          Version (optional)
          <input
            data-testid="fc-version-input"
            type="text"
            value={versionOverride}
            placeholder="default: current"
            onChange={(e) =>
              setVersionOverride(e.target.value)
            }
            className="mt-1 w-36 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100"
          />
        </label>
      </div>

      {/* Header strip — column selector + summary */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-gray-600 dark:text-gray-400">
          {data && (
            <>
              <span data-testid="fc-summary">
                {data.coverage.length} features ·{" "}
                {data.total_unique_bars.toLocaleString()} bars ·{" "}
                {data.tickers_total} tickers · version{" "}
                {data.feature_set_version}
              </span>
            </>
          )}
        </div>
        <ColumnSelector
          catalog={COL_CATALOG}
          selected={selectedCols}
          onChange={setSelectedCols}
          onReset={resetCols}
          lockedKeys={["feature_name"]}
        />
      </div>

      {error && (
        <div
          data-testid="fc-error"
          className="rounded-md border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/30 p-3 text-sm text-red-700 dark:text-red-300"
        >
          Failed to load coverage: {error.message}
        </div>
      )}

      {loading && !data && (
        <div
          data-testid="fc-loading"
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
          data-testid="fc-empty"
          className="rounded-md border border-gray-200 dark:border-gray-700 p-6 text-center text-sm text-gray-500 dark:text-gray-400"
        >
          No feature rows in the selected window. Try widening
          the date range or check that the feature backfill job
          has run.
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
                            c.key as keyof FeatureCoverageRow,
                          )
                    }
                    className={
                      "px-3 py-2 text-left text-xs font-medium " +
                      "text-gray-500 dark:text-gray-400 uppercase " +
                      (c.key === "sample_chart"
                        ? ""
                        : "cursor-pointer select-none")
                    }
                    data-testid={`fc-header-${c.key}`}
                  >
                    {c.label}
                    {sort.col === c.key && (
                      <span aria-hidden> {sort.dir === "asc" ? "▲" : "▼"}</span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800 bg-white dark:bg-gray-900">
              {sortedRows.map((r) => (
                <tr
                  key={r.feature_name}
                  data-testid={`fc-row-${r.feature_name}`}
                >
                  {visibleCols.map((c) => {
                    if (c.key === "sample_chart") {
                      return (
                        <td
                          key={c.key}
                          className="px-3 py-2"
                        >
                          <CoverageBar
                            pct={r.coverage_pct}
                          />
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
                    if (c.key === "feature_name") {
                      return (
                        <td
                          key={c.key}
                          className="px-3 py-2 font-medium text-gray-900 dark:text-gray-100"
                        >
                          {r.feature_name}
                        </td>
                      );
                    }
                    const val = r[
                      c.key as keyof FeatureCoverageRow
                    ];
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

export default FeatureCoverageTab;
