"use client";

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export type BacktestStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export interface EquityPoint {
  bar_date: string;
  equity_inr: string;
  // ASETPLTFRM-400 slice 5 — intraday backtests emit one point
  // per bar within the trading day. Daily runs leave it null.
  bar_open_ts_ns?: number | null;
}

export interface TradeRow {
  ticker: string;
  qty: number;
  avg_price: string;
  fill_price: string;
  opened_at: string;
  closed_at: string;
  holding_days: number;
  realised_pnl_inr: string;
  return_pct: string;
}

export interface BacktestSummary {
  run_id: string;
  strategy_id: string;
  status: BacktestStatus;
  period_start: string;
  period_end: string;
  initial_capital_inr: string;
  final_equity_inr: string;
  total_pnl_inr: string;
  total_pnl_pct: string;
  total_fees_inr: string;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: string;
  max_drawdown_pct: string;
  started_at: string;
  completed_at: string;
  fee_rates_version: string;
  // ASETPLTFRM-400 slice 7 — backtest cadence (seconds). 86400 =
  // daily; 60/300/900 = 1m/5m/15m. Default 86400 on the wire so
  // pre-slice-7 runs deserialise cleanly.
  interval_sec?: number;
  equity_curve: EquityPoint[];
  trade_list: TradeRow[];
  error_text: string | null;
}

export interface BacktestRunListItem {
  run_id: string;
  strategy_id: string;
  status: BacktestStatus;
  period_start: string;
  period_end: string;
  started_at: string;
  completed_at: string | null;
  total_pnl_inr: string | null;
  total_pnl_pct: string | null;
  error_text: string | null;
}

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useBacktestRuns() {
  const key = `${API_URL}/algo/backtest/runs?limit=50`;
  const { data, error, isLoading } = useSWR<BacktestRunListItem[]>(
    key,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );
  return {
    rows: data ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load runs"
      : null,
  };
}

export function useBacktestRun(runId: string | null) {
  const key = runId
    ? `${API_URL}/algo/backtest/runs/${runId}`
    : null;
  const { data, error, isLoading } = useSWR<BacktestSummary>(
    key,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) => {
        if (!latest) return 2_000;
        return latest.status === "pending" ||
          latest.status === "running"
          ? 2_000
          : 0;
      },
    },
  );
  return {
    run: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load run"
      : null,
  };
}

export async function startBacktestRun(
  strategyId: string,
  periodStart: string,
  periodEnd: string,
  initialCapitalInr: string,
): Promise<string> {
  const r = await apiFetch(`${API_URL}/algo/backtest/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      strategy_id: strategyId,
      period_start: periodStart,
      period_end: periodEnd,
      initial_capital_inr: initialCapitalInr,
    }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = (await r.json()) as { run_id: string };
  await mutate(
    (k) =>
      typeof k === "string" &&
      k.startsWith(`${API_URL}/algo/backtest/runs`),
  );
  return body.run_id;
}
