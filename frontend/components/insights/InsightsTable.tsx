"use client";
/**
 * Generic sortable + paginated table for Insights tabs.
 *
 * Handles client-side sorting, pagination, and optional
 * market/sector/ticker filtering via props.
 */

import { useState, useMemo, useCallback } from "react";
import {
  KpiTooltip,
  KPI_TIPS,
} from "@/components/KpiTooltip";

export interface Column<T> {
  key: keyof T & string;
  label: string;
  /** Render override — falls back to String(value). */
  render?: (row: T) => React.ReactNode;
  sortable?: boolean;
  /** Right-align numeric columns. */
  numeric?: boolean;
  /** Tooltip text (auto-looked up from KPI_TIPS if omitted). */
  tooltip?: string;
}

interface SortState {
  col: string;
  dir: "asc" | "desc";
}

interface InsightsTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  pageSize?: number;
  defaultSort?: SortState;
}

export function InsightsTable<
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  T extends Record<string, any>,
>({
  columns,
  rows,
  pageSize: initialPageSize = 10,
  defaultSort,
}: InsightsTableProps<T>) {
  const [sort, setSort] = useState<SortState | null>(
    defaultSort ?? null,
  );
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(
    initialPageSize,
  );

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const { col, dir } = sort;
    return [...rows].sort((a, b) => {
      const av = a[col];
      const bv = b[col];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") {
        return dir === "asc" ? av - bv : bv - av;
      }
      const sa = String(av);
      const sb = String(bv);
      return dir === "asc"
        ? sa.localeCompare(sb)
        : sb.localeCompare(sa);
    });
  }, [rows, sort]);

  const maxPages = Math.max(
    1,
    Math.ceil(sorted.length / pageSize),
  );
  const paginated = useMemo(
    () =>
      sorted.slice(
        (page - 1) * pageSize,
        page * pageSize,
      ),
    [sorted, page, pageSize],
  );

  const toggleSort = useCallback(
    (col: string) => {
      setSort((prev) => {
        if (!prev || prev.col !== col) {
          return { col, dir: "asc" };
        }
        if (prev.dir === "asc") {
          return { col, dir: "desc" };
        }
        return null;
      });
      setPage(1);
    },
    [],
  );

  const handlePageSize = useCallback(
    (size: number) => {
      setPageSize(size);
      setPage(1);
    },
    [],
  );

  return (
    <div className="space-y-3">
      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 dark:bg-gray-800/50">
              {columns.map((col) => (
                <th
                  key={col.key}
                  className={`
                    px-3 py-2.5 font-medium relative
                    text-gray-600 dark:text-gray-300
                    whitespace-nowrap
                    ${col.numeric ? "text-right" : "text-left"}
                    ${col.sortable !== false ? "cursor-pointer select-none hover:text-gray-900 dark:hover:text-gray-100" : ""}
                  `}
                  onClick={() =>
                    col.sortable !== false &&
                    toggleSort(col.key)
                  }
                >
                  <span className="inline-flex items-center gap-1">
                    {col.tooltip ||
                    KPI_TIPS[col.label] ? (
                      <KpiTooltip
                        label={col.label}
                        tip={col.tooltip}
                      />
                    ) : (
                      col.label
                    )}
                    {sort?.col === col.key && (
                      <span className="text-xs">
                        {sort.dir === "asc"
                          ? "\u25B2"
                          : "\u25BC"}
                      </span>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {paginated.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-3 py-8 text-center text-gray-400"
                >
                  No data available
                </td>
              </tr>
            ) : (
              paginated.map((row, i) => (
                <tr
                  key={i}
                  className="hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors"
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={`
                        px-3 py-2
                        text-gray-700 dark:text-gray-200
                        whitespace-nowrap
                        ${col.numeric ? "text-right tabular-nums" : "text-left"}
                      `}
                    >
                      {col.render
                        ? col.render(row)
                        : row[col.key] != null
                          ? String(row[col.key])
                          : "\u2014"}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination controls */}
      <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
        <div className="flex items-center gap-2">
          <span>
            {sorted.length} row
            {sorted.length !== 1 ? "s" : ""}
          </span>
          <select
            value={pageSize}
            onChange={(e) =>
              handlePageSize(Number(e.target.value))
            }
            className="rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-1.5 py-0.5 text-xs"
          >
            {[10, 25, 50].map((n) => (
              <option key={n} value={n}>
                {n}/page
              </option>
            ))}
          </select>
        </div>

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
    </div>
  );
}
