"use client";
/**
 * SWR hook for GET /v1/admin/daily-factor-coverage.
 *
 * Sibling of {@link useFeatureCoverage}, but reads
 * ``stocks.daily_factors`` (WIDE table — one column per factor)
 * instead of ``stocks.intraday_features`` (LONG). Returns the
 * per-factor non-null-row coverage matrix for the configured
 * window. 2-min dedup + no focus-revalidate (per CLAUDE.md §5.3).
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface DailyFactorCoverageRow {
  factor_name: string;
  coverage_pct: number;
  non_null_rows: number;
  tickers_seen: number;
}

export interface DailyFactorCoverageResponse {
  period_start: string;
  period_end: string;
  total_rows: number;
  tickers_total: number;
  coverage: DailyFactorCoverageRow[];
  computed_at: string;
}

export interface UseDailyFactorCoverageArgs {
  periodStart: string;
  periodEnd: string;
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
};

export function useDailyFactorCoverage(
  args: UseDailyFactorCoverageArgs | null,
) {
  const key = args
    ? `${API_URL}/admin/daily-factor-coverage` +
      `?period_start=${args.periodStart}` +
      `&period_end=${args.periodEnd}`
    : null;
  const { data, error, isLoading, mutate } = useSWR<
    DailyFactorCoverageResponse
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
