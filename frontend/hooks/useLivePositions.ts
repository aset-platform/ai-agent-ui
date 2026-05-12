"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface PositionRow {
  tradingsymbol: string;
  exchange: string;
  quantity: number;
  average_price: string;
  last_price: string;
  pnl_inr: string;
  pnl_pct: string;
  product: string;
  strategy_id: string | null;
  strategy_name: string | null;
  entry_ts_utc: string | null;
  entry_reason: string | null;
}

interface PositionsResponse {
  rows: PositionRow[];
  ledger_drift: boolean;
}

async function fetcher(url: string): Promise<PositionsResponse> {
  const r = await apiFetch(url);
  if (!r.ok) {
    let detail = "";
    try {
      const body = await r.json();
      detail = body?.detail ?? "";
    } catch {
      // body wasn't JSON — fall back to plain status.
    }
    throw new Error(
      `HTTP ${r.status}${detail ? ` — ${detail}` : ""}`,
    );
  }
  return r.json();
}

interface UseLivePositionsResult {
  rows: PositionRow[] | undefined;
  ledger_drift: boolean;
  error: unknown;
  loading: boolean;
  refresh: () => Promise<unknown>;
}

export function useLivePositions(): UseLivePositionsResult {
  const { data, error, isLoading, mutate } = useSWR(
    `${API_URL}/algo/live/positions`,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 10_000,
      dedupingInterval: 3_000,
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
