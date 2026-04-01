"use client";
/**
 * Data-fetching hooks for the Insights page tabs.
 *
 * Uses SWR for caching, deduplication, and background
 * revalidation.  Query params are part of the key so
 * SWR re-fetches when filters change.
 */

import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  ScreenerResponse,
  TargetsResponse,
  DividendsResponse,
  RiskResponse,
  SectorsResponse,
  CorrelationResponse,
  QuarterlyResponse,
} from "@/lib/types";

export interface InsightsData<T> {
  value: T | null;
  loading: boolean;
  error: string | null;
}

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function useInsightsFetch<T>(
  endpoint: string,
): InsightsData<T> {
  const { data, error, isLoading } = useSWR<T>(
    `${API_URL}${endpoint}`,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 120_000,
    },
  );

  return {
    value: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load"
      : null,
  };
}

// ---------------------------------------------------------
// Typed wrappers
// ---------------------------------------------------------

export function useScreener(): InsightsData<ScreenerResponse> {
  return useInsightsFetch<ScreenerResponse>(
    "/insights/screener",
  );
}

export function useTargets(): InsightsData<TargetsResponse> {
  return useInsightsFetch<TargetsResponse>(
    "/insights/targets",
  );
}

export function useDividends(): InsightsData<DividendsResponse> {
  return useInsightsFetch<DividendsResponse>(
    "/insights/dividends",
  );
}

export function useRisk(): InsightsData<RiskResponse> {
  return useInsightsFetch<RiskResponse>(
    "/insights/risk",
  );
}

export function useSectors(
  market: string = "all",
): InsightsData<SectorsResponse> {
  return useInsightsFetch<SectorsResponse>(
    `/insights/sectors?market=${market}`,
  );
}

export function useCorrelation(
  period: string = "1y",
  market: string = "all",
  source: string = "portfolio",
): InsightsData<CorrelationResponse> {
  return useInsightsFetch<CorrelationResponse>(
    `/insights/correlation?period=${period}&market=${market}&source=${source}`,
  );
}

export function useQuarterly(
  statementType: string = "income",
): InsightsData<QuarterlyResponse> {
  return useInsightsFetch<QuarterlyResponse>(
    `/insights/quarterly?statement_type=${statementType}`,
  );
}
