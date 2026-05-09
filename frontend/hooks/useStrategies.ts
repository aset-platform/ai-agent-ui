"use client";
/**
 * SWR hook for /v1/algo/strategies/*.
 *
 * - useStrategies(): list view; SWR-keyed on user implicitly
 *   via the cookie-bearing apiFetch.
 * - useStrategy(id): full AST fetch; lazy.
 * - createStrategy / updateStrategy / archiveStrategy:
 *   imperative wrappers that mutate the list cache.
 */

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface StrategySummary {
  id: string;
  name: string;
  mode: string;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  archived_at: string | null;
}

export interface StrategyAst {
  id: string;
  name: string;
  universe: unknown;
  schedule: unknown;
  rebalance: unknown;
  root: unknown;
  risk: unknown;
}

const LIST_KEY = `${API_URL}/algo/strategies`;

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) {
    let detail = "";
    try {
      const body = await r.json();
      detail = body?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(
      `${url}: HTTP ${r.status}${detail ? ` — ${detail}` : ""}`,
    );
  }
  return r.json();
}

export function useStrategies() {
  const { data, error, isLoading } = useSWR<{ strategies: StrategySummary[] }>(
    LIST_KEY,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );
  return {
    strategies: data?.strategies ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load strategies"
      : null,
  };
}

export async function createStrategy(payload: StrategyAst): Promise<string> {
  const r = await apiFetch(`${API_URL}/algo/strategies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload }),
  });
  if (!r.ok) {
    throw new Error(`createStrategy: HTTP ${r.status}`);
  }
  const body = (await r.json()) as { id: string };
  await mutate(LIST_KEY);
  return body.id;
}

export async function updateStrategy(
  id: string,
  payload: StrategyAst,
): Promise<void> {
  const r = await apiFetch(`${API_URL}/algo/strategies/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload }),
  });
  if (!r.ok) {
    throw new Error(`updateStrategy: HTTP ${r.status}`);
  }
  await mutate(LIST_KEY);
}

export async function archiveStrategy(id: string): Promise<void> {
  const r = await apiFetch(`${API_URL}/algo/strategies/${id}`, {
    method: "DELETE",
  });
  if (!r.ok) {
    throw new Error(`archiveStrategy: HTTP ${r.status}`);
  }
  await mutate(LIST_KEY);
}
