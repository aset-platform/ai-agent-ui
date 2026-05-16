"use client";
/**
 * Universe Snapshot admin tab (ASETPLTFRM-423).
 *
 * Read-only operator view of ``stocks.universe_snapshot`` —
 * top-200 + ADTV + sector + liquidity bucket per rebalance.
 *
 * Powers downstream paths:
 *   - LiveRuntime liquidity buckets
 *   - SimBroker slippage caps
 *   - PIT universe resolver (backtests)
 *
 * UX follows §5.4 tabular-page pattern: localStorage column
 * selection, ColumnSelector + DownloadCsvButton co-located with
 * pagination footer (NOT header), client-side filter / sort /
 * paginate over the bounded ~700-row payload, locked identity
 * column (ticker).
 */

import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ColumnSelector } from "@/components/insights/ColumnSelector";
import type { ColumnSpec } from "@/components/insights/ColumnSelector";
import { DownloadCsvButton } from "@/components/common/DownloadCsvButton";
import { useColumnSelection } from "@/lib/useColumnSelection";
import { downloadCsv } from "@/lib/downloadCsv";
import type { CsvColumn } from "@/lib/downloadCsv";
import {
  useUniverseRebalances,
  useUniverseSnapshot,
  type UniverseSnapshotRow,
} from "@/hooks/useUniverseSnapshot";

const STORAGE_KEY = "universe-snapshot:cols";

const COL_CATALOG: ColumnSpec[] = [
  { key: "ticker",              label: "Ticker",         category: "Identity" },
  { key: "sector",              label: "Sector",         category: "Identity" },
  { key: "market_cap_inr",      label: "Market Cap",     category: "Liquidity" },
  { key: "adtv_inr_60d",        label: "60d ADTV",       category: "Liquidity" },
  { key: "liquidity_bucket",    label: "Bucket",         category: "Liquidity" },
  { key: "included_in_top_200", label: "Top 200",        category: "Cohort" },
  { key: "is_top100_mcap",      label: "Top 100 (Mcap)", category: "Cohort" },
];

const DEFAULT_COLS = [
  "ticker",
  "sector",
  "market_cap_inr",
  "adtv_inr_60d",
  "liquidity_bucket",
  "included_in_top_200",
  "is_top100_mcap",
];

const VALID_KEYS = COL_CATALOG.map((c) => c.key);

type SortKey = keyof UniverseSnapshotRow;
type SortDir = "asc" | "desc";

interface SortState {
  col: SortKey;
  dir: SortDir;
}

/**
 * INR compact formatter — ₹X.XX Cr / Lakh, matching the rest
 * of the Indian-market UI.  Universe market caps span 10^9
 * to 10^14; ADTV spans 10^6 to 10^10.
 */
function formatINRCompact(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `₹${(v / 1e5).toFixed(2)} L`;
  return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function formatBool(v: boolean | null): string {
  if (v == null) return "—";
  return v ? "Yes" : "No";
}

function BoolBadge({ v }: { v: boolean | null }) {
  if (v == null) {
    return (
      <span className="text-gray-400 dark:text-gray-500">—</span>
    );
  }
  const cls = v
    ? "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300"
    : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}
    >
      {v ? "Yes" : "No"}
    </span>
  );
}

export function UniverseSnapshotTab() {
  const { data: rebalancesData, loading: rebLoading } =
    useUniverseRebalances();

  // Default to latest rebalance once loaded.
  const [rebalanceDate, setRebalanceDate] = useState<string>("");
  const effectiveDate = useMemo(() => {
    if (rebalanceDate) return rebalanceDate;
    return rebalancesData?.rebalances[0] ?? "";
  }, [rebalanceDate, rebalancesData]);

  const { data, error, loading } = useUniverseSnapshot(
    effectiveDate
      ? { rebalanceDate: effectiveDate }
      : rebalancesData
        ? {} // fall back to "latest" if list ready but empty
        : null,
  );

  // Filters
  const [search, setSearch] = useState("");
  const [sectorFilter, setSectorFilter] = useState<string>("");
  const [bucketFilter, setBucketFilter] = useState<string>("");
  const [top200Only, setTop200Only] = useState(false);

  // Column visibility
  const [selectedCols, setSelectedCols, resetCols] =
    useColumnSelection(STORAGE_KEY, DEFAULT_COLS, VALID_KEYS);

  // Sort
  const [sort, setSort] = useState<SortState>({
    col: "adtv_inr_60d",
    dir: "desc",
  });

  // Pagination
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);

  const allRows = useMemo(() => data?.rows ?? [], [data]);

  const sectorOptions = useMemo(() => {
    const set = new Set<string>();
    for (const r of allRows) {
      if (r.sector) set.add(r.sector);
    }
    return Array.from(set).sort();
  }, [allRows]);

  const bucketOptions = useMemo(() => {
    const set = new Set<string>();
    for (const r of allRows) {
      if (r.liquidity_bucket) set.add(r.liquidity_bucket);
    }
    return Array.from(set).sort();
  }, [allRows]);

  const filteredRows = useMemo(() => {
    const q = search.trim().toUpperCase();
    return allRows.filter((r) => {
      if (q && !r.ticker.toUpperCase().includes(q)) return false;
      if (sectorFilter && r.sector !== sectorFilter) return false;
      if (bucketFilter && r.liquidity_bucket !== bucketFilter) {
        return false;
      }
      if (top200Only && !r.included_in_top_200) return false;
      return true;
    });
  }, [allRows, search, sectorFilter, bucketFilter, top200Only]);

  const sortedRows = useMemo(() => {
    const out = [...filteredRows];
    const { col, dir } = sort;
    out.sort((a, b) => {
      const av = a[col];
      const bv = b[col];
      const an = av == null;
      const bn = bv == null;
      if (an && bn) return 0;
      if (an) return 1;
      if (bn) return -1;
      if (typeof av === "number" && typeof bv === "number") {
        return dir === "asc" ? av - bv : bv - av;
      }
      if (typeof av === "boolean" && typeof bv === "boolean") {
        const an2 = av ? 1 : 0;
        const bn2 = bv ? 1 : 0;
        return dir === "asc" ? an2 - bn2 : bn2 - an2;
      }
      const as = String(av);
      const bs = String(bv);
      return dir === "asc"
        ? as.localeCompare(bs)
        : bs.localeCompare(as);
    });
    return out;
  }, [filteredRows, sort]);

  const maxPages = Math.max(
    1,
    Math.ceil(sortedRows.length / pageSize),
  );
  const safePage = Math.min(Math.max(1, page), maxPages);
  const pagedRows = useMemo(
    () =>
      sortedRows.slice(
        (safePage - 1) * pageSize,
        safePage * pageSize,
      ),
    [sortedRows, safePage, pageSize],
  );

  // Reset to page 1 when any filter / sort / dataset changes.
  // The dependency vector here is the smallest set that should
  // invalidate the current page index.
  useMemoResetPage(
    () => setPage(1),
    [
      search,
      sectorFilter,
      bucketFilter,
      top200Only,
      sort.col,
      sort.dir,
      effectiveDate,
    ],
  );

  const visibleCols = useMemo(
    () => COL_CATALOG.filter((c) => selectedCols.includes(c.key)),
    [selectedCols],
  );

  const handleSort = (col: SortKey) => {
    setSort((prev) =>
      prev.col === col
        ? { col, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { col, dir: "desc" },
    );
  };

  const handleDownload = () => {
    const csvCols: CsvColumn<UniverseSnapshotRow>[] = visibleCols.map(
      (c) => ({
        key: c.key as keyof UniverseSnapshotRow & string,
        header: c.label,
        format: (raw) => {
          if (raw == null) return "";
          if (typeof raw === "boolean") return raw ? "Yes" : "No";
          return String(raw);
        },
      }),
    );
    downloadCsv(
      sortedRows,
      csvCols,
      `universe-snapshot-${effectiveDate || "latest"}`,
    );
  };

  return (
    <div
      data-testid="universe-snapshot-tab"
      className="space-y-4"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Universe Snapshot
        </h2>
        {data && (
          <span
            className="text-xs text-gray-500 dark:text-gray-400"
            data-testid="us-rebalance-label"
          >
            Rebalance: {data.rebalance_date}
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col text-xs text-gray-600 dark:text-gray-400">
          Rebalance Date
          <select
            data-testid="us-rebalance-select"
            value={effectiveDate}
            onChange={(e) => setRebalanceDate(e.target.value)}
            disabled={rebLoading || !rebalancesData}
            className="mt-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100"
          >
            {(rebalancesData?.rebalances ?? []).map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-gray-600 dark:text-gray-400">
          Search Ticker
          <input
            data-testid="us-search"
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="RELIANCE"
            className="mt-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col text-xs text-gray-600 dark:text-gray-400">
          Sector
          <select
            data-testid="us-sector-filter"
            value={sectorFilter}
            onChange={(e) => setSectorFilter(e.target.value)}
            className="mt-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100"
          >
            <option value="">All sectors</option>
            {sectorOptions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-gray-600 dark:text-gray-400">
          Bucket
          <select
            data-testid="us-bucket-filter"
            value={bucketFilter}
            onChange={(e) => setBucketFilter(e.target.value)}
            className="mt-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100"
          >
            <option value="">All buckets</option>
            {bucketOptions.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300">
          <input
            data-testid="us-top200-only"
            type="checkbox"
            checked={top200Only}
            onChange={(e) => setTop200Only(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300 dark:border-gray-600"
          />
          Top 200 only
        </label>
      </div>

      {data && (
        <div
          data-testid="us-summary-chips"
          className="flex flex-wrap items-center gap-2 text-xs"
        >
          <Chip label="Tickers" value={data.total_rows.toLocaleString()} />
          <Chip label="Top 200" value={data.top200_count.toLocaleString()} />
          <Chip
            label="Sectors"
            value={data.sectors.length.toLocaleString()}
          />
          <Chip
            label="Avg ADTV"
            value={formatINRCompact(data.avg_adtv_inr)}
          />
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-gray-600 dark:text-gray-400">
          Showing{" "}
          <span data-testid="us-filtered-count" className="tabular-nums">
            {sortedRows.length.toLocaleString()}
          </span>{" "}
          / {allRows.length.toLocaleString()} rows
        </div>
        <ColumnSelector
          catalog={COL_CATALOG}
          selected={selectedCols}
          onChange={setSelectedCols}
          onReset={resetCols}
          lockedKeys={["ticker"]}
        />
      </div>

      {error && (
        <div
          data-testid="us-error"
          className="rounded-md border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/30 p-3 text-sm text-red-700 dark:text-red-300"
        >
          Failed to load snapshot: {error.message}
        </div>
      )}

      {loading && !data && (
        <div
          data-testid="us-loading"
          className="space-y-2"
          aria-busy="true"
        >
          {[0, 1, 2, 3, 4, 5, 6].map((i) => (
            <div
              key={i}
              className="h-6 w-full animate-pulse rounded bg-gray-100 dark:bg-gray-800"
            />
          ))}
        </div>
      )}

      {!loading && allRows.length === 0 && !error && (
        <div
          data-testid="us-empty"
          className="rounded-md border border-gray-200 dark:border-gray-700 p-6 text-center text-sm text-gray-500 dark:text-gray-400"
        >
          No snapshot rows for the selected rebalance.  Run the
          monthly universe-snapshot job, or pick a different
          rebalance date.
        </div>
      )}

      {sortedRows.length === 0 && allRows.length > 0 && (
        <div
          data-testid="us-no-match"
          className="rounded-md border border-gray-200 dark:border-gray-700 p-6 text-center text-sm text-gray-500 dark:text-gray-400"
        >
          No rows match the current filters.
        </div>
      )}

      {pagedRows.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-gray-200 dark:border-gray-700">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700 text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800">
              <tr>
                {visibleCols.map((c) => (
                  <th
                    key={c.key}
                    scope="col"
                    onClick={() => handleSort(c.key as SortKey)}
                    className="cursor-pointer select-none px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase"
                    data-testid={`us-header-${c.key}`}
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
              {pagedRows.map((r) => (
                <tr
                  key={r.ticker}
                  data-testid={`us-row-${r.ticker}`}
                >
                  {visibleCols.map((c) => (
                    <td
                      key={c.key}
                      className="px-3 py-2 text-gray-700 dark:text-gray-300"
                    >
                      {renderCell(r, c.key)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-xs text-gray-600 dark:text-gray-400">
            <div className="flex items-center gap-2">
              <span data-testid="us-page-count" className="tabular-nums">
                {sortedRows.length.toLocaleString()} rows
              </span>
              <select
                data-testid="us-page-size"
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                }}
                className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
              >
                {[10, 25, 50, 100].map((n) => (
                  <option key={n} value={n}>
                    {n}/page
                  </option>
                ))}
              </select>
              <DownloadCsvButton
                onClick={handleDownload}
                disabled={sortedRows.length === 0}
                aria-label="Download CSV"
                title="Download CSV"
              />
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                data-testid="us-prev"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={safePage <= 1}
                className="rounded-md border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
              >
                Prev
              </button>
              <span className="tabular-nums" data-testid="us-page-indicator">
                {safePage} / {maxPages}
              </span>
              <button
                type="button"
                data-testid="us-next"
                onClick={() =>
                  setPage((p) => Math.min(maxPages, p + 1))
                }
                disabled={safePage >= maxPages}
                className="rounded-md border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
              >
                Next
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center rounded-full bg-gray-100 dark:bg-gray-800 px-2 py-0.5 text-gray-700 dark:text-gray-300">
      <span className="font-medium">{label}:</span>
      <span className="ml-1 tabular-nums">{value}</span>
    </span>
  );
}

function renderCell(
  r: UniverseSnapshotRow,
  key: string,
): ReactNode {
  switch (key) {
    case "ticker":
      return (
        <span className="font-medium text-gray-900 dark:text-gray-100">
          {r.ticker}
        </span>
      );
    case "sector":
      return r.sector ?? "—";
    case "market_cap_inr":
      return (
        <span className="font-mono tabular-nums">
          {formatINRCompact(r.market_cap_inr)}
        </span>
      );
    case "adtv_inr_60d":
      return (
        <span className="font-mono tabular-nums">
          {formatINRCompact(r.adtv_inr_60d)}
        </span>
      );
    case "liquidity_bucket":
      return r.liquidity_bucket ?? "—";
    case "included_in_top_200":
      return <BoolBadge v={r.included_in_top_200} />;
    case "is_top100_mcap":
      return <BoolBadge v={r.is_top100_mcap} />;
    default:
      // Belt-and-braces fallback for unknown keys
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return formatBool((r as any)[key] ?? null);
  }
}

function useMemoResetPage(
  fn: () => void,
  deps: ReadonlyArray<unknown>,
): void {
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    fn();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export default UniverseSnapshotTab;
