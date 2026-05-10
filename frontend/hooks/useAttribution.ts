"use client";

/**
 * useAttribution — SWR hooks for the REGIME-6 attribution panel.
 *
 * Three endpoints:
 *   GET /v1/algo/attribution/daily       — Brinson decomposition rows
 *   GET /v1/algo/attribution/trades      — synthesised per-trade reasons
 *   GET /v1/algo/attribution/regression  — latest monthly factor regression
 *
 * Auth via apiFetch (mandatory per CLAUDE.md §5.3 — auto-refreshes JWT).
 * Cache budgets per CLAUDE.md §5.13: daily polls every 60s; trades and
 * regression are heavier so use a 5-minute dedup.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

// ── Response types ───────────────────────────────────────────

export interface AttributionDailyRow {
  user_id: string;
  strategy_id: string;
  bar_date: string;
  brinson_alloc: Record<string, number>;
  brinson_select: Record<string, number>;
  brinson_interaction: Record<string, number>;
  total_active_return: number;
  created_at: string | null;
}

export interface AttributionTradeRow {
  ticker: string;
  opened_at: string | null;
  closed_at: string | null;
  qty: number;
  entry_price: number;
  exit_price: number;
  pnl_inr: number;
  pnl_pct: number;
  entry_regime: string | null;
  stress_prob: number | null;
  entry_factor_exposures: Record<string, number>;
  exit_reason: string | null;
  reason_text: string;
}

export interface AttributionRegressionRow {
  user_id: string;
  strategy_id: string;
  period_start: string | null;
  period_end: string | null;
  alpha: number;
  betas: Record<string, number>;
  r_squared: number;
  n_observations: number;
  mock_data: boolean;
  created_at: string | null;
}

interface RowsEnvelope<T> {
  rows: T[];
  total: number;
  as_of?: string;
}

// ── Fetcher ──────────────────────────────────────────────────

async function fetcher<T>(url: string): Promise<T> {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ── Hooks ────────────────────────────────────────────────────

/**
 * Daily Brinson rows for the selected strategy. Polls every 60s
 * — the daily job lands once per IST day so polling is purely
 * to pick up the new row when the user keeps the page open.
 */
export function useAttributionDaily(
  strategyId: string | null | undefined,
  days: number = 30,
) {
  const sid = strategyId || null;
  let key: string | null = null;
  if (sid) {
    const today = new Date();
    const start = new Date(today);
    start.setDate(today.getDate() - days);
    const startStr = start.toISOString().slice(0, 10);
    const endStr = today.toISOString().slice(0, 10);
    key = (
      `${API_URL}/algo/attribution/daily?strategy_id=${sid}` +
      `&start=${startStr}&end=${endStr}`
    );
  }
  const { data, error, isLoading, mutate } = useSWR<
    RowsEnvelope<AttributionDailyRow>
  >(key, fetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 60_000,
    refreshInterval: 60_000,
  });
  return {
    rows: data?.rows ?? [],
    total: data?.total ?? 0,
    error: error as Error | undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}

/**
 * Per-trade reasons synthesised from today's signal_generated +
 * order_filled events. Backend caches for 5 min so the client-
 * side dedup can match.
 */
export function useAttributionTrades(
  strategyId: string | null | undefined,
  // `days` reserved for the future when the route accepts a
  // window; today the backend builds the log for `as_of` only,
  // so this argument is currently a stable cache-key salt.
  days: number = 1,
) {
  const sid = strategyId || null;
  const key = sid
    ? (
      `${API_URL}/algo/attribution/trades?strategy_id=${sid}` +
      `&days=${days}`
    )
    : null;
  const { data, error, isLoading, mutate } = useSWR<
    RowsEnvelope<AttributionTradeRow>
  >(key, fetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 5 * 60_000,
  });
  return {
    rows: data?.rows ?? [],
    total: data?.total ?? 0,
    asOf: data?.as_of ?? null,
    error: error as Error | undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}

/**
 * Latest monthly factor regression. Returns the most recent row
 * (or undefined if none exist yet — the route returns an empty
 * envelope, NOT 404, when there are no rows).
 */
export function useAttributionRegression(
  strategyId: string | null | undefined,
) {
  const sid = strategyId || null;
  const key = sid
    ? `${API_URL}/algo/attribution/regression?strategy_id=${sid}&limit=1`
    : null;
  const { data, error, isLoading, mutate } = useSWR<
    RowsEnvelope<AttributionRegressionRow>
  >(key, fetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 5 * 60_000,
  });
  return {
    latest: data?.rows?.[0],
    rows: data?.rows ?? [],
    error: error as Error | undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}
