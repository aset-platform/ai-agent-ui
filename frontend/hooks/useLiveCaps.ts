"use client";
/**
 * Hook for live-trading caps: fetch + upsert.
 * V2-5 — Live Order Placement.
 */

import useSWR, { mutate as globalMutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface LiveCaps {
  user_id: string;
  strategy_id: string;
  max_inr: number;
  max_orders_per_day: number;
  allowed_tickers: string[];
  live_orders_enabled: boolean;
  approved_by: string | null;
  approved_at: string | null;
  last_walkforward_run_id: string | null;
  cumulative_inr_today: number;
  orders_count_today: number;
}

export interface UpsertCapsPayload {
  max_inr: number;
  max_orders_per_day: number;
  allowed_tickers: string[];
  last_walkforward_run_id?: string | null;
}

function capsKey(strategyId: string): string {
  return `${API_URL}/algo/live/caps/${strategyId}`;
}

async function fetcher(url: string): Promise<LiveCaps> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useLiveCaps(strategyId: string | null) {
  const key = strategyId ? capsKey(strategyId) : null;
  const { data, error, isLoading } = useSWR<LiveCaps>(key, fetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 30_000,
  });
  return {
    caps: data ?? null,
    loading: isLoading,
    error: error instanceof Error ? error.message : null,
  };
}

export async function upsertLiveCaps(
  strategyId: string,
  payload: UpsertCapsPayload,
): Promise<LiveCaps> {
  const r = await apiFetch(capsKey(strategyId), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(text || `HTTP ${r.status}`);
  }
  const updated: LiveCaps = await r.json();
  await globalMutate(capsKey(strategyId), updated, false);
  return updated;
}

export async function enableLiveOrders(
  strategyId: string,
  confirmedStrategyName: string,
): Promise<LiveCaps> {
  const r = await apiFetch(
    `${API_URL}/algo/live/enable/${strategyId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        confirmed_strategy_name: confirmedStrategyName,
      }),
    },
  );
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(
      body?.detail ?? `HTTP ${r.status}`,
    );
  }
  const updated: LiveCaps = await r.json();
  await globalMutate(capsKey(strategyId), updated, false);
  return updated;
}

export async function disableLiveOrders(
  strategyId: string,
): Promise<LiveCaps> {
  const r = await apiFetch(
    `${API_URL}/algo/live/disable/${strategyId}`,
    { method: "POST" },
  );
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(
      body?.detail ?? `HTTP ${r.status}`,
    );
  }
  const updated: LiveCaps = await r.json();
  await globalMutate(capsKey(strategyId), updated, false);
  return updated;
}
