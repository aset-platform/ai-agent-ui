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

// Backend caps a single /algo/factors request at 200 tickers
// (backend/algo/routes/factors.py) as a defensive limit on the
// IN-clause query. A superuser's full-registry watchlist can
// exceed 200 (found 2026-07-03: 245 tickers, previously a hard
// 400 "Failed to load factor scores" with no partial data). Split
// into <=200-ticker chunks, fetch in parallel, and merge — the
// endpoint is a cached read (TTL_STABLE), so N parallel chunk
// calls are cheap and each one is itself cache-hit after the
// first load.
const MAX_TICKERS_PER_REQUEST = 200;

async function fetchFactorScores(
  sortedTickers: string[],
): Promise<FactorScoreRow[]> {
  const chunks: string[][] = [];
  for (let i = 0; i < sortedTickers.length; i += MAX_TICKERS_PER_REQUEST) {
    chunks.push(sortedTickers.slice(i, i + MAX_TICKERS_PER_REQUEST));
  }
  const chunkResults = await Promise.all(
    chunks.map(async (chunk) => {
      const res = await apiFetch(
        `${API_URL}/algo/factors?tickers=${chunk.join(",")}`,
      );
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      return (await res.json()) as FactorScoreRow[];
    }),
  );
  return chunkResults.flat();
}

export function useFactorScores(tickers: string[]) {
  // Stable cache key — sorted + deduped
  const sorted = Array.from(new Set(tickers)).sort();
  const key = sorted.length ? ["algo-factors", sorted.join(",")] : null;
  const { data, error, isLoading, mutate } = useSWR<FactorScoreRow[]>(
    key,
    () => fetchFactorScores(sorted),
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
