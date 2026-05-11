"use client";
/**
 * SWR hook for /v1/algo/paper/strategies/{id}/summary.
 *
 * Reads the FIFO-aggregated paper-mode P&L for a strategy:
 * realised P&L per closed ticker + open positions marked to
 * the latest stocks.ohlcv close. Backed by algo.events; no
 * algo.runs row needed (paper sessions don't write one).
 *
 * Polls every 10s while the user is on the Paper tab so an
 * active session's totals tick up without a manual refresh.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface OpenPosition {
  ticker: string;
  qty: number;
  avg_price: number;
  last_price: number | null;
  unrealised_pnl_inr: number;
  unrealised_pnl_pct: number;
}

export interface ClosedPosition {
  ticker: string;
  realised_pnl_inr: number;
  round_trips: number;
}

export interface PaperSessionSummary {
  strategy_id: string;
  first_event_ts_ns: number | null;
  last_event_ts_ns: number | null;
  n_signals_generated: number;
  n_signals_rejected: number;
  n_fills: number;
  rejection_reasons: Record<string, number>;
  open_positions: OpenPosition[];
  closed_positions: ClosedPosition[];
  total_realised_pnl_inr: number;
  total_unrealised_pnl_inr: number;
  total_pnl_inr: number;
}

async function fetcher(url: string): Promise<PaperSessionSummary> {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

export function usePaperSessionSummary(
  strategyId: string | null | undefined,
) {
  const key = strategyId
    ? `${API_URL}/algo/paper/strategies/${strategyId}/summary`
    : null;
  const { data, error, isLoading, mutate } =
    useSWR<PaperSessionSummary>(key, fetcher, {
      refreshInterval: 10_000,
      revalidateOnFocus: true,
      dedupingInterval: 5_000,
    });
  return {
    summary: data,
    error: error instanceof Error ? error : undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}
