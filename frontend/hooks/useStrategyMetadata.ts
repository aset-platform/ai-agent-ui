"use client";
/**
 * REGIME-3 — strategy↔regime binding metadata hook.
 *
 * GET /v1/algo/strategies/:id returns a wrapper response shape
 * `{ strategy, applicable_regimes }`. This hook pulls just the
 * `applicable_regimes` slice and provides an `upsertStrategyMetadata`
 * helper for editor save flows.
 */

import useSWR, { mutate } from "swr";

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
  // Use a namespaced key so this hook doesn't collide with BuilderMode's
  // useSWR which uses the bare URL and returns a StrategyAst (not
  // StrategyResponse). Sharing the same key with a different fetcher
  // causes whichever SWR re-validates last to overwrite the cache with
  // a different shape, corrupting the other hook's data.
  const url = strategyId
    ? `${API_URL}/algo/strategies/${strategyId}`
    : null;
  // Use a "#meta" suffix so this hook's SWR cache slot is separate
  // from BuilderMode's useSWR(bare URL → StrategyAst). Sharing the
  // same key with different fetchers causes whichever re-validates
  // last to overwrite the cache with a different shape.
  const { data, error, isLoading, mutate } = useSWR<StrategyResponse>(
    url ? `${url}#meta` : null,
    url ? () => fetcher<StrategyResponse>(url) : null,
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
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (Array.isArray(body.detail)) {
        detail = body.detail
          .map((e: { loc?: unknown[]; msg?: string }) =>
            `${(e.loc ?? []).join(".")}: ${e.msg ?? ""}`.replace(/^\./, ""),
          )
          .join("; ");
      } else if (typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // JSON parse failed — keep the "HTTP NNN" fallback.
    }
    throw new Error(detail);
  }
  // Invalidate the individual strategy SWR cache slots so the editor
  // immediately shows the saved AST when re-opened for the same id.
  await Promise.all([
    mutate(`${API_URL}/algo/strategies/${strategyId}`),
    mutate(`${API_URL}/algo/strategies/${strategyId}#meta`),
  ]);
}
