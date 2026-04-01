"use client";

import { useState, useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { useUserTickers } from "@/hooks/useDashboardData";

/**
 * Hook for linking / unlinking tickers to the user's
 * watchlist with optimistic SWR cache updates.
 */
export function useLinkUnlink() {
  const userTickers = useUserTickers();
  const [busyTickers, setBusyTickers] = useState<
    Set<string>
  >(new Set());

  const markBusy = (ticker: string) =>
    setBusyTickers((prev) => new Set(prev).add(ticker));
  const clearBusy = (ticker: string) =>
    setBusyTickers((prev) => {
      const next = new Set(prev);
      next.delete(ticker);
      return next;
    });

  const linkTicker = useCallback(
    async (ticker: string) => {
      markBusy(ticker);
      try {
        const r = await apiFetch(
          `${API_URL}/users/me/tickers`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ ticker }),
          },
        );
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        userTickers.mutate(
          (prev) => ({
            tickers: [
              ...(prev?.tickers ?? []),
              ticker,
            ],
          }),
          { revalidate: false },
        );
      } catch {
        /* allow retry */
      } finally {
        clearBusy(ticker);
      }
    },
    [userTickers],
  );

  const unlinkTicker = useCallback(
    async (ticker: string) => {
      markBusy(ticker);
      try {
        const enc = encodeURIComponent(ticker);
        const r = await apiFetch(
          `${API_URL}/users/me/tickers/${enc}`,
          { method: "DELETE" },
        );
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
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
        clearBusy(ticker);
      }
    },
    [userTickers],
  );

  const isBusy = useCallback(
    (ticker: string) => busyTickers.has(ticker),
    [busyTickers],
  );

  return {
    linkedSet: new Set(
      userTickers.value?.tickers ?? [],
    ),
    busyTickers,
    isBusy,
    linkTicker,
    unlinkTicker,
    loading: userTickers.loading,
  };
}
