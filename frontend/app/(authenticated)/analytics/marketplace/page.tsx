"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import {
  useRegistry,
  useUserTickers,
} from "@/hooks/useDashboardData";
import { ConfirmDialog } from "@/components/ConfirmDialog";

const PAGE_SIZE = 12;

type MarketFilter = "all" | "india" | "us";

export default function MarketplacePage() {
  const registryData = useRegistry();
  const userTickers = useUserTickers();
  const registry = useMemo(
    () => registryData.value?.tickers ?? [],
    [registryData.value],
  );
  const linkedSet = useMemo(
    () => new Set(userTickers.value?.tickers ?? []),
    [userTickers.value],
  );
  const loading = registryData.loading || userTickers.loading;
  const error = registryData.error || userTickers.error;

  const [search, setSearch] = useState("");
  const [market, setMarket] = useState<MarketFilter>("all");
  const [page, setPage] = useState(1);
  const [busyTickers, setBusyTickers] = useState<Set<string>>(new Set());
  const [unlinkConfirm, setUnlinkConfirm] =
    useState<string | null>(null);

  // Filtered + searched list
  const filtered = useMemo(() => {
    let list = registry;
    if (market !== "all") {
      list = list.filter((t) => t.market === market);
    }
    if (search.trim()) {
      const q = search.trim().toUpperCase();
      list = list.filter(
        (t) =>
          t.ticker.toUpperCase().includes(q) ||
          (t.company_name ?? "").toUpperCase().includes(q),
      );
    }
    return list;
  }, [registry, market, search]);

  // Pagination
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const paged = useMemo(
    () => filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [filtered, safePage],
  );

  // Reset page when filters change
  useEffect(() => {
    setPage(1);
  }, [search, market]);

  // Link / unlink handlers
  const linkTicker = useCallback(
    async (ticker: string) => {
      setBusyTickers((prev) => new Set(prev).add(ticker));
      try {
        const r = await apiFetch(`${API_URL}/users/me/tickers`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticker }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        // Optimistic update + revalidate SWR cache
        userTickers.mutate(
          (prev) => ({
            tickers: [...(prev?.tickers ?? []), ticker],
          }),
          { revalidate: false },
        );
      } catch {
        /* allow retry */
      } finally {
        setBusyTickers((prev) => {
          const next = new Set(prev);
          next.delete(ticker);
          return next;
        });
      }
    },
    [userTickers],
  );

  const unlinkTicker = useCallback(
    async (ticker: string) => {
      setBusyTickers((prev) => new Set(prev).add(ticker));
      try {
        const r = await apiFetch(
          `${API_URL}/users/me/tickers/${encodeURIComponent(ticker)}`,
          { method: "DELETE" },
        );
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        // Optimistic update + revalidate SWR cache
        userTickers.mutate(
          (prev) => ({
            tickers: (prev?.tickers ?? []).filter(
              (t) => t !== ticker,
            ),
          }),
          { revalidate: false },
        );
      } catch {
        /* allow retry */
      } finally {
        setBusyTickers((prev) => {
          const next = new Set(prev);
          next.delete(ticker);
          return next;
        });
      }
    },
    [userTickers],
  );

  const currencySymbol = (ccy: string) =>
    ccy === "INR" ? "\u20B9" : "$";

  // ----------------------------------------------------------
  // Render
  // ----------------------------------------------------------

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin h-8 w-8 border-4 border-indigo-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950 p-6 text-center text-red-700 dark:text-red-300">
        <p className="font-semibold">Failed to load ticker registry</p>
        <p className="mt-1 text-sm">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Stats bar */}
      <div className="flex items-center justify-end">
        <p
          data-testid="marketplace-stats"
          className="text-sm text-gray-500 dark:text-gray-400"
        >
          {filtered.length} ticker{filtered.length !== 1 ? "s" : ""}
          {" \u00B7 "}
          {linkedSet.size} linked
        </p>
      </div>

      {/* Filters */}
      <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          {/* Search */}
          <input
            type="text"
            data-testid="marketplace-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by ticker or company..."
            className="flex-1 rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-500"
          />

          {/* Market filter */}
          <div className="flex gap-1">
            {(["all", "india", "us"] as MarketFilter[]).map((m) => (
              <button
                key={m}
                data-testid={`marketplace-market-${m}`}
                onClick={() => setMarket(m)}
                className={`rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  market === m
                    ? "bg-indigo-600 text-white"
                    : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
                }`}
              >
                {m === "all" ? "All" : m === "india" ? "India" : "US"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Table */}
      <div data-testid="marketplace-table" className="overflow-x-auto rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-left text-xs font-semibold uppercase tracking-wider text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400">
              <th className="px-4 py-3">Ticker</th>
              <th className="px-4 py-3">Company</th>
              <th className="px-4 py-3">Market</th>
              <th className="px-4 py-3 text-right">Price</th>
              <th className="px-4 py-3">Last Updated</th>
              <th className="px-4 py-3 text-center">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {paged.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-8 text-center text-gray-400 dark:text-gray-500"
                >
                  No tickers found
                </td>
              </tr>
            ) : (
              paged.map((t) => {
                const linked = linkedSet.has(t.ticker);
                const busy = busyTickers.has(t.ticker);
                return (
                  <tr
                    key={t.ticker}
                    data-testid={`marketplace-row-${t.ticker}`}
                    className="transition-colors hover:bg-gray-50 dark:hover:bg-gray-800/50"
                  >
                    <td className="px-4 py-3 font-mono font-semibold text-gray-900 dark:text-gray-100">
                      {t.ticker}
                    </td>
                    <td className="px-4 py-3 text-gray-700 dark:text-gray-300">
                      {t.company_name ?? "\u2014"}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                          t.market === "india"
                            ? "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400"
                            : "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                        }`}
                      >
                        {t.market === "india" ? "India" : "US"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-900 dark:text-gray-100">
                      {t.current_price != null
                        ? `${currencySymbol(t.currency)}${t.current_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                        : "\u2014"}
                    </td>
                    <td className="px-4 py-3 text-gray-500 dark:text-gray-400">
                      {t.last_fetch_date ?? "\u2014"}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {linked ? (
                        <button
                          data-testid={`marketplace-unlink-${t.ticker}`}
                          disabled={busy}
                          onClick={() => setUnlinkConfirm(t.ticker)}
                          className="rounded-lg border border-red-300 px-3 py-1 text-xs font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-700 dark:text-red-400 dark:hover:bg-red-900/20"
                        >
                          {busy ? "..." : "Unlink"}
                        </button>
                      ) : (
                        <button
                          data-testid={`marketplace-link-${t.ticker}`}
                          disabled={busy}
                          onClick={() => linkTicker(t.ticker)}
                          className="rounded-lg bg-indigo-600 px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-indigo-700 disabled:opacity-50"
                        >
                          {busy ? "..." : "Link"}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button
            data-testid="marketplace-pagination-prev"
            disabled={safePage <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-600 transition-colors hover:bg-gray-100 disabled:opacity-40 dark:border-gray-600 dark:text-gray-400 dark:hover:bg-gray-800"
          >
            Previous
          </button>
          <span
            data-testid="marketplace-page-info"
            className="text-sm text-gray-500 dark:text-gray-400"
          >
            Page {safePage} of {totalPages}
          </span>
          <button
            data-testid="marketplace-pagination-next"
            disabled={safePage >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-600 transition-colors hover:bg-gray-100 disabled:opacity-40 dark:border-gray-600 dark:text-gray-400 dark:hover:bg-gray-800"
          >
            Next
          </button>
        </div>
      )}
      <ConfirmDialog
        open={unlinkConfirm !== null}
        title="Unlink Stock"
        message={
          unlinkConfirm
            ? `Unlink ${unlinkConfirm}? You can re-link it later.`
            : ""
        }
        confirmLabel="Unlink"
        variant="warning"
        onConfirm={() => {
          if (unlinkConfirm) unlinkTicker(unlinkConfirm);
          setUnlinkConfirm(null);
        }}
        onCancel={() => setUnlinkConfirm(null)}
      />
    </div>
  );
}
