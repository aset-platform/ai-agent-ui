"use client";
/**
 * SWR data-fetching hooks for the new Swing Setups tab
 * under Advanced Analytics. Mirrors
 * :file:`useAdvancedAnalyticsData.ts` conventions:
 * `dedupingInterval: 120000`, `revalidateOnFocus: false`,
 * `apiFetch` for JWT auto-refresh (§4.2 #14).
 *
 * Two endpoints:
 *   GET /v1/advanced-analytics/swing-setups
 *   GET /v1/advanced-analytics/swing-setups/methodology
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  SwingMethodology,
  SwingRegime,
  SwingSetupsResponse,
} from "@/lib/types/swingSetups";

export interface UseSwingSetupsArgs {
  regime: SwingRegime;
  market: "all" | "india" | "us";
  page: number;
  pageSize: number;
  sortKey: string | null;
  sortDir: "asc" | "desc";
}

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useSwingSetups(args: UseSwingSetupsArgs) {
  const params = new URLSearchParams({
    regime: args.regime,
    market: args.market,
    page: String(args.page),
    page_size: String(args.pageSize),
    sort_dir: args.sortDir,
  });
  if (args.sortKey) params.set("sort_key", args.sortKey);
  const key = (
    `${API_URL}/advanced-analytics/swing-setups?${params.toString()}`
  );
  return useSWR<SwingSetupsResponse>(
    key,
    fetcher,
    {
      dedupingInterval: 120_000,  // 2 min — CLAUDE.md §5.3
      revalidateOnFocus: false,
    },
  );
}

export function useSwingMethodology(regime: SwingRegime) {
  const key = (
    `${API_URL}/advanced-analytics/swing-setups/methodology`
    + `?regime=${regime}`
  );
  return useSWR<SwingMethodology>(
    key,
    fetcher,
    {
      dedupingInterval: 600_000,
      revalidateOnFocus: false,
    },
  );
}
