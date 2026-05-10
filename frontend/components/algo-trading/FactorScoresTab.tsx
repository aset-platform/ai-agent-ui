"use client";
/**
 * FactorScoresTab — REGIME-2b.
 *
 * Per-ticker factor scores from stocks.daily_factors (REGIME-2a).
 * Tabular page pattern (CLAUDE.md §5.4):
 *   - useColumnSelection (localStorage-backed)
 *   - ColumnSelector popover
 *   - Locked identity column (`ticker`)
 *   - Click-header sort, client-side pagination 25/page
 *   - DownloadCsvButton next to pagination
 */

import { useMemo, useState } from "react";

import { ColumnSelector } from "@/components/insights/ColumnSelector";
import { DownloadCsvButton } from "@/components/common/DownloadCsvButton";
import { useFactorScores } from "@/hooks/useFactorScores";
import {
  DEFAULT_VISIBLE_FACTORS,
  FACTOR_CATALOG,
  FACTOR_BY_KEY,
  FACTOR_KEYS,
} from "@/lib/factorCatalog";
import { useColumnSelection } from "@/lib/useColumnSelection";
import { useWatchlist } from "@/hooks/useDashboardData";

const PAGE_SIZE = 25;

type SortDir = "asc" | "desc" | null;

interface SortState {
  key: string;
  dir: SortDir;
}

function fmt(value: number | undefined | null): string {
  if (value === undefined || value === null) return "—";
  if (Number.isNaN(value)) return "—";
  if (Math.abs(value) >= 100) return value.toFixed(0);
  if (Math.abs(value) >= 1) return value.toFixed(2);
  return value.toFixed(3);
}

function rowsToCsv(
  rows: Array<{ ticker: string; sector: string | null; values: Record<string, number> }>,
  visibleKeys: string[],
): string {
  const headers = ["ticker", "sector", ...visibleKeys];
  const lines = [headers.join(",")];
  for (const r of rows) {
    const cells = [r.ticker, r.sector ?? ""];
    for (const k of visibleKeys) {
      const v = r.values[k];
      cells.push(v === undefined || v === null || Number.isNaN(v)
        ? "" : String(v));
    }
    lines.push(cells.map((c) => {
      const s = String(c);
      return s.includes(",") || s.includes("\"")
        ? `"${s.replace(/"/g, "\"\"")}"`
        : s;
    }).join(","));
  }
  return lines.join("\n");
}

export function FactorScoresTab() {
  const watchlist = useWatchlist();
  const tickers = useMemo(
    () => (watchlist.value?.tickers ?? []).map((t) => t.ticker),
    [watchlist.value],
  );
  const { rows, loading, error } = useFactorScores(tickers);

  const [selectedCols, setSelectedCols, resetCols] =
    useColumnSelection(
      "insights.columns.factors",
      DEFAULT_VISIBLE_FACTORS,
      FACTOR_KEYS,
    );

  const visibleKeys = useMemo(
    () => FACTOR_KEYS.filter((k) => selectedCols.includes(k)),
    [selectedCols],
  );

  const [sort, setSort] = useState<SortState>(
    { key: "ticker", dir: "asc" },
  );
  const [page, setPage] = useState(0);

  const sortedRows = useMemo(() => {
    const arr = [...rows];
    if (sort.dir === null) return arr;
    arr.sort((a, b) => {
      let av: number | string;
      let bv: number | string;
      if (sort.key === "ticker") {
        av = a.ticker;
        bv = b.ticker;
      } else {
        av = a.values[sort.key];
        bv = b.values[sort.key];
        if (av === undefined || Number.isNaN(av)) av = -Infinity;
        if (bv === undefined || Number.isNaN(bv)) bv = -Infinity;
      }
      if (av < bv) return sort.dir === "asc" ? -1 : 1;
      if (av > bv) return sort.dir === "asc" ? 1 : -1;
      return 0;
    });
    return arr;
  }, [rows, sort]);

  const pagedRows = useMemo(
    () => sortedRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [sortedRows, page],
  );

  const totalPages = Math.max(
    1,
    Math.ceil(sortedRows.length / PAGE_SIZE),
  );

  function toggleSort(key: string) {
    setPage(0);
    setSort((s) => {
      if (s.key !== key) return { key, dir: "asc" };
      if (s.dir === "asc") return { key, dir: "desc" };
      return { key: "ticker", dir: "asc" };
    });
  }

  function handleDownload() {
    const csv = rowsToCsv(sortedRows, visibleKeys);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `factor-scores-${
      new Date().toISOString().slice(0, 10)
    }.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div
      className="space-y-3"
      data-testid="factor-scores-tab"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          Factor Scores
        </h2>
        <ColumnSelector
          catalog={FACTOR_CATALOG.map((f) => ({
            key: f.key,
            label: f.label,
            category: f.category,
          }))}
          selected={selectedCols}
          onChange={(next) => {
            setSelectedCols(next);
            setPage(0);
          }}
          onReset={resetCols}
          lockedKeys={["ticker"]}
        />
      </div>

      {error && (
        <p
          className="text-xs text-rose-600"
          data-testid="factor-scores-error"
        >
          Failed to load factor scores.
        </p>
      )}

      {loading && rows.length === 0 && (
        <p
          className="text-xs text-slate-500"
          data-testid="factor-scores-loading"
        >
          Loading factor scores…
        </p>
      )}

      {!loading && rows.length === 0 && !error && (
        <p
          className="text-xs text-slate-500"
          data-testid="factor-scores-empty"
        >
          No tickers with factor data. Add stocks to your watchlist
          and wait for the nightly factor compute job to populate.
        </p>
      )}

      {rows.length > 0 && (
        <>
          <div
            className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700"
            data-testid="factor-scores-table"
          >
            <table className="min-w-full text-xs">
              <thead className="bg-slate-100 dark:bg-slate-800">
                <tr>
                  <th
                    className="cursor-pointer px-2 py-1.5 text-left font-medium"
                    onClick={() => toggleSort("ticker")}
                  >
                    Ticker
                    {sort.key === "ticker"
                      && (sort.dir === "asc" ? " ▲" : " ▼")}
                  </th>
                  <th className="px-2 py-1.5 text-left font-medium">
                    Sector
                  </th>
                  {visibleKeys.map((k) => {
                    const def = FACTOR_BY_KEY[k];
                    return (
                      <th
                        key={k}
                        className="cursor-pointer px-2 py-1.5 text-right font-medium"
                        onClick={() => toggleSort(k)}
                        title={def?.description}
                      >
                        {def?.label ?? k}
                        {sort.key === k
                          && (sort.dir === "asc" ? " ▲" : " ▼")}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {pagedRows.map((r) => (
                  <tr
                    key={r.ticker}
                    className="border-t border-slate-200 dark:border-slate-700"
                  >
                    <td className="px-2 py-1.5 font-semibold">
                      {r.ticker}
                    </td>
                    <td className="px-2 py-1.5 text-slate-600 dark:text-slate-400">
                      {r.sector ?? "—"}
                    </td>
                    {visibleKeys.map((k) => (
                      <td
                        key={k}
                        className="px-2 py-1.5 text-right tabular-nums"
                      >
                        {fmt(r.values[k])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between gap-3">
            <span className="text-[11px] text-slate-500">
              Showing {page * PAGE_SIZE + 1}–
              {Math.min((page + 1) * PAGE_SIZE, sortedRows.length)} of{" "}
              {sortedRows.length}
            </span>
            <div className="flex items-center gap-2">
              <DownloadCsvButton onClick={handleDownload} />
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                className="rounded border border-slate-300 dark:border-slate-600 px-2 py-0.5 text-xs disabled:opacity-40"
                data-testid="factor-scores-prev"
              >
                ← Prev
              </button>
              <span className="text-[11px] text-slate-500">
                {page + 1} / {totalPages}
              </span>
              <button
                type="button"
                disabled={page + 1 >= totalPages}
                onClick={() => setPage((p) =>
                  Math.min(totalPages - 1, p + 1))}
                className="rounded border border-slate-300 dark:border-slate-600 px-2 py-0.5 text-xs disabled:opacity-40"
                data-testid="factor-scores-next"
              >
                Next →
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
