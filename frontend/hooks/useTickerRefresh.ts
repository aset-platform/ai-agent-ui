"use client";

import { useState, useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export type RefreshState =
  | "idle"
  | "pending"
  | "success"
  | "error";

/**
 * Hook for triggering background data refresh on tickers.
 *
 * Encapsulates the POST-start + poll-status pattern used by
 * the dashboard refresh endpoint.
 */
export function useTickerRefresh(
  onSuccess?: () => void,
) {
  const [states, setStates] = useState<
    Record<string, RefreshState>
  >({});

  const set = (ticker: string, s: RefreshState) =>
    setStates((prev) => ({ ...prev, [ticker]: s }));

  const startRefresh = useCallback(
    async (ticker: string) => {
      set(ticker, "pending");
      try {
        const enc = encodeURIComponent(ticker);
        const r = await apiFetch(
          `${API_URL}/dashboard/refresh/${enc}`,
          { method: "POST" },
        );
        if (!r.ok) throw new Error(`HTTP ${r.status}`);

        // Poll for completion (max 3 min)
        const poll = async () => {
          for (let i = 0; i < 90; i++) {
            await new Promise((ok) =>
              setTimeout(ok, 2000),
            );
            const sr = await apiFetch(
              `${API_URL}/dashboard/refresh/${enc}/status`,
            );
            if (!sr.ok) break;
            const s = await sr.json();
            if (s.status === "success") {
              set(ticker, "success");
              onSuccess?.();
              setTimeout(
                () => set(ticker, "idle"),
                3000,
              );
              return;
            }
            if (s.status === "error") {
              set(ticker, "error");
              setTimeout(
                () => set(ticker, "idle"),
                5000,
              );
              return;
            }
          }
        };
        poll();
      } catch {
        set(ticker, "error");
        setTimeout(() => set(ticker, "idle"), 5000);
      }
    },
    [onSuccess],
  );

  const getState = useCallback(
    (ticker: string): RefreshState =>
      states[ticker] ?? "idle",
    [states],
  );

  return { refreshStates: states, startRefresh, getState };
}
