"use client";

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface PaperRunRow {
  user_id: string;
  strategy_id: string;
  strategy_name: string;
  started_at: string;
  status: "running" | "completed";
}

const KEY = `${API_URL}/algo/paper/runs`;

async function fetcher(url: string): Promise<PaperRunRow[]> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function usePaperRuns() {
  const { data, error, isLoading } = useSWR<PaperRunRow[]>(
    KEY,
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 5_000 },
  );
  return {
    runs: data ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load runs"
      : null,
  };
}

export async function startPaperRun(
  strategyId: string,
  fixturePath: string,
  initialCapitalInr: string,
): Promise<void> {
  const r = await apiFetch(KEY, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      strategy_id: strategyId,
      fixture_path: fixturePath,
      initial_capital_inr: initialCapitalInr,
    }),
  });
  if (!r.ok) {
    let detail = "";
    try {
      const body = await r.json();
      detail = body?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(
      `HTTP ${r.status}${detail ? ` — ${detail}` : ""}`,
    );
  }
  await mutate(KEY);
}

export async function stopPaperRun(strategyId: string): Promise<void> {
  const r = await apiFetch(`${KEY}/${strategyId}`, {
    method: "DELETE",
  });
  if (!r.ok && r.status !== 404) {
    throw new Error(`HTTP ${r.status}`);
  }
  await mutate(KEY);
}
