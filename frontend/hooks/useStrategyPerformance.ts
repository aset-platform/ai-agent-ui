"use client";
/**
 * SWR hook for GET /v1/algo/performance/summary.
 *
 * Powers the rebuilt Strategies -> Performance page: mode-aware
 * (backtest/walkforward/paper/live), strategy-scoped, trade-level
 * metrics.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { TradeRow } from "@/hooks/useBacktestRuns";

export type PerformanceMode =
  | "backtest"
  | "walkforward"
  | "paper"
  | "live";

export type LookbackPreset = "7d" | "30d" | "90d" | "all";

export interface TradeExtreme {
  ticker: string;
  pnl_inr: number;
  closed_at: string;
}

export interface StrategyPerfRow {
  strategy_id: string;
  strategy_name: string;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  total_pnl_inr: number;
  biggest_win: TradeExtreme | null;
  biggest_loss: TradeExtreme | null;
  avg_win_inr: number | null;
  avg_loss_inr: number | null;
  profit_factor: number | null;
  max_drawdown_pct: number | null;
}

export interface PerformanceSummaryResponse {
  mode: PerformanceMode;
  window: { start: string; end: string };
  strategies: StrategyPerfRow[];
  trades: TradeRow[];
}

export interface UseStrategyPerformanceParams {
  mode: PerformanceMode;
  strategyId?: string | null;
  lookback?: LookbackPreset | null;
  start?: string | null;
  end?: string | null;
}

async function fetcher(url: string): Promise<PerformanceSummaryResponse> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useStrategyPerformance(
  params: UseStrategyPerformanceParams,
) {
  const { mode, strategyId, lookback, start, end } = params;
  const qs = new URLSearchParams({ mode });
  if (strategyId) qs.set("strategy_id", strategyId);
  if (lookback && !start && !end) qs.set("lookback", lookback);
  if (start && end) {
    qs.set("start", start);
    qs.set("end", end);
  }
  const key = `${API_URL}/algo/performance/summary?${qs.toString()}`;

  const { data, error, isLoading } = useSWR<PerformanceSummaryResponse>(
    key,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );

  return {
    strategies: data?.strategies ?? [],
    trades: data?.trades ?? [],
    window: data?.window ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load performance data"
      : null,
  };
}
