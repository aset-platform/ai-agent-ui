"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface HoldingRow {
  tradingsymbol: string;
  exchange: string;
  quantity: number;
  average_price: string;
  last_price: string;
  pnl_inr: string;
  pnl_pct: string;
  days_held: number | null;
  strategy_id: string | null;
  strategy_name: string | null;
  /** SEBI T+1 settlement: shares from a CNC BUY are owned but not
   *  yet settled (quantity=0, t1_quantity>0 on the Kite side).
   *  Sellable today via a regular CNC sell order; we chip the row
   *  so the user can see at a glance which holdings are pending. */
  t1_pending: boolean;
}

interface HoldingsResponse {
  rows: HoldingRow[];
  ledger_drift: boolean;
}

async function fetcher(url: string): Promise<HoldingsResponse> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

interface UseLiveHoldingsResult {
  rows: HoldingRow[] | undefined;
  ledger_drift: boolean;
  error: unknown;
  loading: boolean;
  refresh: () => Promise<unknown>;
}

export function useLiveHoldings(): UseLiveHoldingsResult {
  const { data, error, isLoading, mutate } = useSWR(
    `${API_URL}/algo/live/holdings`,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 30_000,
      dedupingInterval: 10_000,
    },
  );
  return {
    rows: data?.rows,
    ledger_drift: data?.ledger_drift ?? false,
    error,
    loading: isLoading,
    refresh: mutate,
  };
}
