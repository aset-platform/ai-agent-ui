"use client";
/**
 * REGIME-3 — strategy↔regime binding metadata hook.
 *
 * GET /v1/algo/strategies/:id returns a wrapper response shape
 * `{ strategy, applicable_regimes }`. This hook pulls just the
 * `applicable_regimes` slice and provides an `upsertStrategyMetadata`
 * helper for editor save flows.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import {
  REGIME_LABELS,
  type RegimeLabel,
  type StrategyResponse,
} from "@/lib/types/algoStrategy";

async function fetcher<T>(url: string): Promise<T> {
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export function useStrategyMetadata(strategyId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<StrategyResponse>(
    strategyId
      ? `${API_URL}/algo/strategies/${strategyId}`
      : null,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );
  const applicableRegimes: RegimeLabel[] =
    (data?.applicable_regimes as RegimeLabel[] | undefined) ??
    REGIME_LABELS;
  return {
    applicableRegimes,
    error: error as Error | undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}

/**
 * PUT-based upsert that pipes `applicable_regimes` through the
 * existing strategy-update route. Caller supplies the AST payload
 * so the backend update_strategy() doesn't reject the request — the
 * AST is the only required field on the wire format.
 */
export async function upsertStrategyMetadata(
  strategyId: string,
  applicableRegimes: RegimeLabel[],
  payload: Record<string, unknown>,
): Promise<void> {
  const res = await apiFetch(
    `${API_URL}/algo/strategies/${strategyId}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        payload,
        applicable_regimes: applicableRegimes,
      }),
    },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}
