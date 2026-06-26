"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

/** Minimal shape we need from /algo/portfolio/positions. */
export interface PortfolioPositionRow {
  tradingsymbol: string;
  internal_ticker: string;
  quantity: number;
}

async function fetcher(url: string): Promise<{ rows: PortfolioPositionRow[] }> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/**
 * Queries /algo/portfolio/positions which combines BOTH kc.positions()
 * and kc.holdings() (Kite CNC net + settled). Returns all open algo
 * positions regardless of settlement status, with internal_ticker
 * already in ".NS" / ".BO" format.
 *
 * Use this when you need a reliable set of currently-held tickers
 * without having to worry about Kite's settlement timing (positions
 * vs holdings split).
 */
export function useAlgoPortfolioPositions() {
  const { data, error, isLoading } = useSWR(
    `${API_URL}/algo/portfolio/positions`,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 30_000,
      dedupingInterval: 10_000,
    },
  );
  return {
    rows: data?.rows,
    error,
    loading: isLoading,
  };
}
