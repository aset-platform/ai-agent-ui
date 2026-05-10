"use client";
/**
 * Hook for in-flight live orders list.
 * Also exports cancelInFlightOrders action.
 * V2-5 — Live Order Placement.
 */

import useSWR, { mutate as globalMutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface InFlightOrder {
  kite_order_id: string;
  internal_order_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  submitted_at: string;
  status: string;
}

function ordersKey(strategyId: string): string {
  return `${API_URL}/algo/live/orders/${strategyId}`;
}

async function fetcher(url: string): Promise<InFlightOrder[]> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useLiveOrders(strategyId: string | null) {
  const key = strategyId ? ordersKey(strategyId) : null;
  const { data, error, isLoading } = useSWR<InFlightOrder[]>(
    key,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 10_000,  // poll every 10s when live
    },
  );
  return {
    orders: data ?? [],
    loading: isLoading,
    error: error instanceof Error ? error.message : null,
    revalidate: () =>
      strategyId
        ? globalMutate(ordersKey(strategyId))
        : Promise.resolve(undefined),
  };
}
