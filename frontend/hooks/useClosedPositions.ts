"use client";

import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface ClosedPosition {
  id: string;
  ticker: string;
  quantity: number;
  buy_price: number;
  sell_price: number;
  sell_date: string;
  fees: number;
  realized_pnl: number;
  realized_pnl_pct: number | null;
  currency: string;
  market: string;
}

interface ClosedTotals {
  realized_pnl_by_currency: Record<string, number>;
}

interface ClosedResponse {
  closed: ClosedPosition[];
  totals: ClosedTotals;
}

async function fetcher(url: string): Promise<ClosedResponse> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useClosedPositions() {
  const { data, error, isLoading, mutate } = useSWR<ClosedResponse>(
    `${API_URL}/users/me/portfolio/closed`,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 120_000,
    },
  );

  return {
    closed: data?.closed ?? [],
    totals: data?.totals ?? { realized_pnl_by_currency: {} },
    loading: isLoading,
    error: error ? "Failed to load" : null,
    refresh: () => mutate(),
  };
}
