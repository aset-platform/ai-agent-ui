"use client";
/**
 * Instruments tab — Slice 3 of the Algo Trading epic.
 *
 * Searchable, filterable, paginated table over the Kite-derived
 * algo.instruments master. Manual refresh button triggers a
 * synchronous Kite /instruments pull (also runs daily at 07:00 IST
 * via the scheduler).
 */

import { useCallback, useEffect, useState } from "react";

import {
  refreshInstruments,
  useInstruments,
} from "@/hooks/useInstruments";

const EXCHANGES = ["", "NSE", "BSE", "NFO", "BFO", "MCX", "CDS"] as const;

export function InstrumentsTab() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [exchange, setExchange] = useState<string>("");
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);

  // Debounce search 300 ms.
  useEffect(() => {
    const id = window.setTimeout(() => {
      setSearch((prev) => {
        const next = searchInput.trim();
        if (next !== prev) setPage(1);
        return next;
      });
    }, 300);
    return () => window.clearTimeout(id);
  }, [searchInput]);

  const { value, loading, error } = useInstruments({
    search,
    exchange,
    page,
    pageSize,
  });

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setRefreshNote(null);
    try {
      const n = await refreshInstruments();
      setRefreshNote(`Refreshed ${n.toLocaleString("en-IN")} instruments`);
    } catch (e) {
      setRefreshNote(`Refresh failed: ${(e as Error).message}`);
    } finally {
      setRefreshing(false);
    }
  }, []);

  const totalPages = value
    ? Math.max(1, Math.ceil(value.total / pageSize))
    : 1;

  return (
    <div className="space-y-4" data-testid="algo-instruments-tab">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Instruments
        </h2>
        <div className="flex items-center gap-2 flex-wrap">
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search symbol…"
            data-testid="algo-instruments-search"
            className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs text-gray-700 dark:text-gray-200 w-32 sm:w-40"
          />
          <select
            value={exchange}
            onChange={(e) => {
              setExchange(e.target.value);
              setPage(1);
            }}
            data-testid="algo-instruments-exchange"
            className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs"
          >
            {EXCHANGES.map((x) => (
              <option key={x} value={x}>
                {x === "" ? "All exchanges" : x}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing}
            data-testid="algo-instruments-refresh"
            className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs disabled:opacity-40"
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {refreshNote && (
        <div className="text-xs text-gray-500 dark:text-gray-400">
          {refreshNote}
        </div>
      )}

      {error && (
        <div role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800/50">
            <tr>
              <Th>Symbol</Th>
              <Th>Exchange</Th>
              <Th>Segment</Th>
              <Th align="right">Lot</Th>
              <Th align="right">Tick</Th>
              <Th>Our ticker</Th>
            </tr>
          </thead>
          <tbody
            data-testid="algo-instruments-tbody"
            className="divide-y divide-gray-100 dark:divide-gray-800"
          >
            {loading && !value ? (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-xs text-gray-500">
                  Loading…
                </td>
              </tr>
            ) : value && value.rows.length > 0 ? (
              value.rows.map((r) => (
                <tr key={r.instrument_token} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                  <td className="px-3 py-2 font-mono">{r.tradingsymbol}</td>
                  <td className="px-3 py-2 text-gray-500">{r.exchange}</td>
                  <td className="px-3 py-2 text-gray-500">{r.segment}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.lot_size}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.tick_size}</td>
                  <td className="px-3 py-2 text-gray-500">
                    {r.our_ticker ?? "—"}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-xs text-gray-500">
                  No instruments. Click &ldquo;Refresh&rdquo; to pull from Kite.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {value && value.total > 0 && (
        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>
            Showing {(value.page - 1) * pageSize + 1}–
            {Math.min(value.page * pageSize, value.total)} of{" "}
            {value.total.toLocaleString("en-IN")} rows
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              data-testid="algo-instruments-prev"
              className="rounded-md border border-gray-300 dark:border-gray-700 px-2 py-1 disabled:opacity-40"
            >
              Prev
            </button>
            <span className="px-2">Page {value.page} / {totalPages}</span>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              data-testid="algo-instruments-next"
              className="rounded-md border border-gray-300 dark:border-gray-700 px-2 py-1 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Th({
  children, align = "left",
}: { children: React.ReactNode; align?: "left" | "right" }) {
  return (
    <th
      scope="col"
      className={`whitespace-nowrap px-3 py-2 text-${align} text-xs font-medium text-gray-600 dark:text-gray-300`}
    >
      {children}
    </th>
  );
}
