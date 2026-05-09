"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface PerformanceRunRow {
  run_id: string;
  strategy_id: string;
  strategy_name: string;
  mode: string;
  status: string;
  period_start: string | null;
  period_end: string | null;
  started_at: string;
  completed_at: string | null;
  total_pnl_inr: string | null;
  total_pnl_pct: string | null;
  total_trades: number | null;
  win_rate_pct: string | null;
  max_drawdown_pct: string | null;
}

async function fetcher(url: string): Promise<PerformanceRunRow[]> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function usePerformanceRuns(limit = 50) {
  const key = `${API_URL}/algo/performance/runs?limit=${limit}`;
  const { data, error, isLoading } = useSWR<PerformanceRunRow[]>(
    key,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );
  return {
    runs: data ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load runs"
      : null,
  };
}
