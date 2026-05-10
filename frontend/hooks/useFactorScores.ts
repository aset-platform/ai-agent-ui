"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface FactorScoreRow {
  ticker: string;
  bar_date: string;
  sector: string | null;
  values: Record<string, number>;
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
};

export function useFactorScores(tickers: string[]) {
  // Stable cache key — sorted + deduped
  const sorted = Array.from(new Set(tickers)).sort();
  const key = sorted.length
    ? `${API_URL}/algo/factors?tickers=${sorted.join(",")}`
    : null;
  const { data, error, isLoading, mutate } = useSWR<FactorScoreRow[]>(
    key,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 2 * 60_000,
    },
  );
  return {
    rows: data ?? [],
    error: error as Error | undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}
