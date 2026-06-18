"use client";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  AlgoPositionView,
  AlgoPositionsResponse,
} from "@/lib/types/algoPortfolio";

const fetcher = async (url: string) => {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export function useAlgoPositions() {
  const { data, error, isLoading, mutate } = useSWR<
    AlgoPositionsResponse
  >(
    `${API_URL}/algo/portfolio/positions`,
    fetcher,
    {
      revalidateOnFocus: false,
      // 5s during market hours, 60s off-hours — matches the
      // existing useLivePortfolioTotals cadence.
      refreshInterval: (latest) =>
        latest?.market_open ? 5_000 : 60_000,
    },
  );

  // The watchlist widget surfaces LIVE algo positions only. Paper-mode
  // rows (``source === "paper"``, synthesized from mode='paper'
  // algo.events fills) are excluded so the dashboard reflects real
  // Kite holdings. ``source`` defaults to "live" when absent.
  const positions: AlgoPositionView[] = (data?.positions ?? []).filter(
    (p) => p.source !== "paper",
  );

  return {
    positions,
    asOf: data?.as_of ?? null,
    marketOpen: data?.market_open ?? false,
    isLoading,
    error,
    mutate,
  };
}
