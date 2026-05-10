"use client";

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface WindowSummary {
  window_index: number;
  run_id: string;
  train_start: string;
  train_end: string;
  test_start: string;
  test_end: string;
  status: string;
  total_pnl_pct: string | null;
  win_rate_pct: string | null;
  max_drawdown_pct: string | null;
  equity_curve: Array<{ bar_date: string; equity_inr: string }>;
  error_text: string | null;
}

export interface PerRegimeRow {
  regime: string;       // BULL | SIDEWAYS | BEAR
  n_days: number;
  cum_return_pct: string;
  sharpe: string;
  sortino: string;
  max_dd_pct: string;
  hit_rate: string;
}

export interface WalkForwardAggregate {
  avg_win_rate_pct: string;
  avg_pnl_pct: string;
  avg_max_drawdown_pct: string;
  std_pnl_pct: string;
  window_count: number;
  completed_count: number;
  // REGIME-5 additions — all default-empty so v2 summaries deserialise
  per_regime?: PerRegimeRow[];
  dsr?: string;
  pbo?: string | null;
  recovery_months?: number;
  gates_passed?: Record<string, boolean>;
  regime_stratified?: boolean;
}

export interface WalkForwardResult {
  walkforward_run_id: string;
  strategy_id: string;
  status: string;
  period_start: string;
  period_end: string;
  train_days: number;
  test_days: number;
  step_days: number;
  window_summaries: WindowSummary[];
  aggregate: WalkForwardAggregate | null;
  error_text: string | null;
}

export interface WalkForwardListItem {
  run_id: string;
  strategy_id: string;
  status: string;
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

export function useWalkForwardRuns() {
  const key = `${API_URL}/algo/walkforward/runs?limit=50`;
  const { data, error, isLoading } = useSWR<WalkForwardListItem[]>(
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
        : "Failed to load walk-forward runs"
      : null,
  };
}

export function useWalkForwardRun(runId: string | null) {
  const key = runId
    ? `${API_URL}/algo/walkforward/runs/${runId}`
    : null;
  const { data, error, isLoading } = useSWR<WalkForwardResult>(
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
        : "Failed to load walk-forward run"
      : null,
  };
}

export async function startWalkForwardRun(
  strategyId: string,
  periodStart: string,
  periodEnd: string,
  trainDays: number,
  testDays: number,
  stepDays: number,
  initialCapitalInr: string,
): Promise<string> {
  const r = await apiFetch(`${API_URL}/algo/walkforward/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      strategy_id: strategyId,
      period_start: periodStart,
      period_end: periodEnd,
      train_days: trainDays,
      test_days: testDays,
      step_days: stepDays,
      initial_capital_inr: initialCapitalInr,
    }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = (await r.json()) as { walkforward_run_id: string };
  await mutate(
    (k) =>
      typeof k === "string" &&
      k.startsWith(`${API_URL}/algo/walkforward/runs`),
  );
  return body.walkforward_run_id;
}
