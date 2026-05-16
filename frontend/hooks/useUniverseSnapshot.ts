"use client";
/**
 * SWR hooks for GET /v1/admin/universe-snapshot
 * (ASETPLTFRM-423).
 *
 * Three sibling hooks:
 *   - useUniverseRebalances() — distinct rebalance_dates (date
 *     picker)
 *   - useUniverseSnapshot({ rebalanceDate? }) — full per-ticker
 *     snapshot + summary aggregates for one rebalance
 *   - useUniverseDiff({ fromDate, toDate }) — entries / exits
 *     between two rebalance_dates
 *
 * 2-min dedup + no focus-revalidate (per CLAUDE.md §5.3).
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface UniverseSnapshotRow {
  ticker: string;
  sector: string | null;
  market_cap_inr: number | null;
  adtv_inr_60d: number | null;
  liquidity_bucket: string | null;
  included_in_top_200: boolean;
  is_top100_mcap: boolean | null;
}

export interface SectorBreakdown {
  sector: string;
  count: number;
  top200_count: number;
}

export interface BucketBreakdown {
  bucket: string;
  count: number;
}

export interface UniverseSnapshotResponse {
  rebalance_date: string;
  total_rows: number;
  top200_count: number;
  avg_adtv_inr: number | null;
  sectors: SectorBreakdown[];
  buckets: BucketBreakdown[];
  rows: UniverseSnapshotRow[];
  computed_at: string;
}

export interface RebalanceList {
  rebalances: string[];
  computed_at: string;
}

export interface DiffEntry {
  ticker: string;
  sector: string | null;
  adtv_inr_60d: number | null;
}

export interface UniverseDiffResponse {
  from_date: string;
  to_date: string;
  entries: DiffEntry[];
  exits: DiffEntry[];
  computed_at: string;
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
};

export function useUniverseRebalances() {
  const key = `${API_URL}/admin/universe-snapshot/rebalances`;
  const { data, error, isLoading, mutate } = useSWR<RebalanceList>(
    key,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 2 * 60_000,
    },
  );
  return {
    data,
    error: error as Error | undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}

export interface UseUniverseSnapshotArgs {
  rebalanceDate?: string;
}

export function useUniverseSnapshot(
  args: UseUniverseSnapshotArgs | null,
) {
  const key = args
    ? `${API_URL}/admin/universe-snapshot` +
      (args.rebalanceDate
        ? `?rebalance_date=${args.rebalanceDate}`
        : "")
    : null;
  const { data, error, isLoading, mutate } = useSWR<
    UniverseSnapshotResponse
  >(key, fetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 2 * 60_000,
  });
  return {
    data,
    error: error as Error | undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}

export interface UseUniverseDiffArgs {
  fromDate: string;
  toDate: string;
}

export function useUniverseDiff(args: UseUniverseDiffArgs | null) {
  const key =
    args && args.fromDate && args.toDate && args.fromDate !== args.toDate
      ? `${API_URL}/admin/universe-snapshot/diff` +
        `?from=${args.fromDate}&to=${args.toDate}`
      : null;
  const { data, error, isLoading, mutate } = useSWR<
    UniverseDiffResponse
  >(key, fetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 2 * 60_000,
  });
  return {
    data,
    error: error as Error | undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}
