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
  /** Where `last_price` came from. `live_ltp` = Redis tick cache
   * (sub-minute fresh from WS multiplexer or runtime bar close);
   * `last_fill` = most recent order_filled event price;
   * `ohlcv_close` = end-of-day fallback from stocks.ohlcv. */
  mark_source?: "live_ltp" | "last_fill" | "ohlcv_close" | "unknown";
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
      // 5s polling keeps the live-LTP mark within ~5s of fresh
      // ticks. The backend WS multiplexer caches LTP in Redis on
      // every tick, so reads are sub-ms and the perceived
      // refresh rate is bounded by this interval, not the data.
      refreshInterval: 5_000,
      revalidateOnFocus: true,
      dedupingInterval: 2_000,
    });
  return {
    summary: data,
    error: error instanceof Error ? error : undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}
