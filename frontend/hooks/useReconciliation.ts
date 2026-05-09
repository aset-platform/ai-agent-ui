"use client";
/**
 * SWR hook for reconciliation drift state.
 *
 * Polls ``GET /v1/algo/drift`` every 60 s (modest cadence —
 * reconciliation job runs every 5 min so there is no value
 * polling faster than ~30 s).
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface DriftRow {
  symbol: string;
  first_seen_at: string | null;
  consecutive_runs: number;
  last_diff: {
    our_qty?: number;
    broker_qty?: number;
    diff?: number;
    [key: string]: unknown;
  };
  resolved_at: string | null;
}

async function fetcher(url: string): Promise<DriftRow[]> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

const KEY = `${API_URL}/algo/drift`;

export function useReconciliation() {
  const { data, error, isLoading } = useSWR<DriftRow[]>(
    KEY,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 60_000,
      dedupingInterval: 30_000,
    },
  );
  return {
    drifts: data ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load drift state"
      : null,
  };
}
