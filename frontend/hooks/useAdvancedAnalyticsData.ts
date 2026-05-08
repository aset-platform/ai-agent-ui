"use client";
/**
 * SWR data-fetching hook for the Advanced Analytics
 * 7-tab page (Sprint 9 AA-10).
 *
 * Backend is `/v1/advanced-analytics/<report>` — one
 * GET per tab, server-side sort + pagination via
 * query params. Mirrors :func:`useInsightsFetch`
 * conventions: `dedupingInterval: 120000`,
 * `revalidateOnFocus: false`, `apiFetch` for JWT
 * auto-refresh (§4.2 #14).
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  AdvancedReportName,
  AdvancedReportResponse,
  MarketFilter,
  TickerTypeFilter,
} from "@/lib/types/advancedAnalytics";

export interface AdvancedAnalyticsData {
  value: AdvancedReportResponse | null;
  loading: boolean;
  error: string | null;
}

async function fetcher(
  url: string,
): Promise<AdvancedReportResponse> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useAdvancedAnalyticsReport(
  report: AdvancedReportName,
  page: number,
  pageSize: number,
  sortKey: string | null,
  sortDir: "asc" | "desc",
  market: MarketFilter,
  tickerType: TickerTypeFilter,
  search: string,
  tech: string[],
  fund: string[],
  fallbackData?: AdvancedReportResponse,
): AdvancedAnalyticsData {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    sort_dir: sortDir,
    market,
    ticker_type: tickerType,
  });
  if (sortKey) params.set("sort_key", sortKey);
  if (search) params.set("search", search);
  // Sorted joined CSV → cache stability across param order.
  if (tech.length > 0) params.set("tech", [...tech].sort().join(","));
  if (fund.length > 0) params.set("fund", [...fund].sort().join(","));

  const key = `${API_URL}/advanced-analytics/${report}?${params.toString()}`;

  const { data, error, isLoading } = useSWR<AdvancedReportResponse>(
    key,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 120_000,
      fallbackData,
    },
  );

  return {
    value: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load report"
      : null,
  };
}
