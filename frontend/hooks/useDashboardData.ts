"use client";
/**
 * Generic data-fetching hook for dashboard widgets.
 *
 * Uses SWR for automatic caching, deduplication, and
 * background revalidation.  Navigating away and back
 * returns cached data instantly.
 */

import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  WatchlistResponse,
  ForecastsResponse,
  AnalysisResponse,
  LLMUsageResponse,
  RegistryResponse,
  DashboardHomeResponse,
} from "@/lib/types";

export interface DashboardData<T> {
  value: T | null;
  loading: boolean;
  error: string | null;
}

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function useDashboardData<T>(
  endpoint: string,
): DashboardData<T> {
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

// ---------------------------------------------------------------
// Typed wrappers
// ---------------------------------------------------------------

export function useWatchlist(): DashboardData<WatchlistResponse> {
  return useDashboardData<WatchlistResponse>(
    "/dashboard/watchlist",
  );
}

export function useForecastSummary(): DashboardData<ForecastsResponse> {
  return useDashboardData<ForecastsResponse>(
    "/dashboard/forecasts/summary",
  );
}

export function useAnalysisLatest(): DashboardData<AnalysisResponse> {
  return useDashboardData<AnalysisResponse>(
    "/dashboard/analysis/latest",
  );
}

export function useLLMUsage(): DashboardData<LLMUsageResponse> {
  return useDashboardData<LLMUsageResponse>(
    "/dashboard/llm-usage",
  );
}

/**
 * Aggregate hook — fetches all dashboard widget data
 * in a single request via ``/dashboard/home``.
 * Returns individual ``DashboardData`` wrappers so the
 * page can pass them to widgets unchanged.
 */
export function useDashboardHome() {
  const { data, error, isLoading, mutate } =
    useSWR<DashboardHomeResponse>(
      `${API_URL}/dashboard/home`,
      fetcher,
      {
        revalidateOnFocus: false,
        dedupingInterval: 120_000,
      },
    );

  const errMsg = error
    ? error instanceof Error
      ? error.message
      : "Failed to load"
    : null;

  return {
    watchlist: {
      value: data?.watchlist ?? null,
      loading: isLoading,
      error: errMsg,
    } as DashboardData<WatchlistResponse>,
    forecasts: {
      value: data?.forecasts ?? null,
      loading: isLoading,
      error: errMsg,
    } as DashboardData<ForecastsResponse>,
    analysis: {
      value: data?.analysis ?? null,
      loading: isLoading,
      error: errMsg,
    } as DashboardData<AnalysisResponse>,
    llmUsage: {
      value: data?.llm_usage ?? null,
      loading: isLoading,
      error: errMsg,
    } as DashboardData<LLMUsageResponse>,
    /** Force re-fetch all dashboard data. */
    refresh: () => {
      mutate();
    },
  };
}

export function useRegistry(): DashboardData<RegistryResponse> {
  return useDashboardData<RegistryResponse>(
    "/dashboard/registry",
  );
}

interface UserTickersResponse {
  tickers: string[];
}

export function useUserTickers() {
  const { data, error, isLoading, mutate } =
    useSWR<UserTickersResponse>(
      `${API_URL}/users/me/tickers`,
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
    mutate,
  };
}

export function useProfile<T = Record<string, unknown>>(): DashboardData<T> {
  return useDashboardData<T>("/auth/me");
}
