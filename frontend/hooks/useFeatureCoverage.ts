"use client";
/**
 * SWR hook for GET /v1/admin/feature-coverage
 * (ASETPLTFRM-416 / FE-14).
 *
 * Returns the per-feature coverage matrix for the configured
 * (interval_sec, period_start, period_end, feature_set_version)
 * window. 2-min dedup + no focus-revalidate (per CLAUDE.md §5.3).
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface FeatureCoverageRow {
  feature_name: string;
  coverage_pct: number;
  rows: number;
  tickers_seen: number;
}

export interface FeatureCoverageResponse {
  interval_sec: number;
  period_start: string;
  period_end: string;
  feature_set_version: string;
  total_unique_bars: number;
  tickers_total: number;
  rows_total: number;
  coverage: FeatureCoverageRow[];
  computed_at: string;
}

export interface UseFeatureCoverageArgs {
  intervalSec: number;
  periodStart: string;
  periodEnd: string;
  featureSetVersion?: string;
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
};

export function useFeatureCoverage(
  args: UseFeatureCoverageArgs | null,
) {
  const key = args
    ? `${API_URL}/admin/feature-coverage` +
      `?interval_sec=${args.intervalSec}` +
      `&period_start=${args.periodStart}` +
      `&period_end=${args.periodEnd}` +
      (args.featureSetVersion
        ? `&feature_set_version=${args.featureSetVersion}`
        : "")
    : null;
  const { data, error, isLoading, mutate } = useSWR<
    FeatureCoverageResponse
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
