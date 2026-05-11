"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface LiveDashboardSummary {
  today_pnl_inr: string;
  open_pnl_inr: string;
  realised_pnl_inr: string;
  cash_inr: string;
  open_position_count: number;
  mode: "live" | "dry_run";
  ws_age_seconds: number | null;
  kill_switch_active: boolean;
}

async function fetchSummary(
  url: string,
): Promise<LiveDashboardSummary> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useLiveDashboardSummary() {
  const { data, error, isLoading, mutate } = useSWR(
    `${API_URL}/algo/live/dashboard-summary`,
    fetchSummary,
    {
      revalidateOnFocus: false,
      refreshInterval: 15_000,
      dedupingInterval: 5_000,
    },
  );
  return {
    summary: data,
    error,
    loading: isLoading,
    refresh: mutate,
  };
}
