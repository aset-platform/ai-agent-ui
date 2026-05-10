"use client";
/**
 * Hook for live-trading gate status.
 * Returns per-gate booleans used to drive the 4-gate toggle UI.
 * V2-5 — Live Order Placement.
 */

import useSWR, { mutate as globalMutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface GatesStatus {
  kite_connected: boolean;
  caps_set: boolean;
  kill_switch_disarmed: boolean;
  walkforward_recent: boolean;
  drift_within_limit: boolean;
  all_pass: boolean;
  live_orders_enabled: boolean;
  /** True when ALGO_LIVE_DRY_RUN=true in backend env. */
  dry_run: boolean;
}

function statusKey(strategyId: string): string {
  return `${API_URL}/algo/live/status/${strategyId}`;
}

async function fetcher(url: string): Promise<GatesStatus> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useLiveStatus(strategyId: string | null) {
  const key = strategyId ? statusKey(strategyId) : null;
  const { data, error, isLoading } = useSWR<GatesStatus>(
    key,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 15_000 },
  );
  return {
    gates: data ?? null,
    loading: isLoading,
    error: error instanceof Error ? error.message : null,
    revalidate: () =>
      strategyId
        ? globalMutate(statusKey(strategyId))
        : Promise.resolve(undefined),
  };
}
