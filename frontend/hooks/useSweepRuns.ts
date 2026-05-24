"use client";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  SweepConfig, SweepResult, SweepRow,
} from "@/lib/types/algoSweep";

const fetcher = async (url: string) => {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export function useSweepRuns() {
  const { data, error, isLoading, mutate } = useSWR<
    { sweeps: SweepRow[] }
  >(
    `${API_URL}/algo/sweep/runs`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 120_000 },
  );
  return {
    runs: data?.sweeps ?? [],
    isLoading,
    error,
    mutate,
  };
}

export function useSweepRun(runId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<
    SweepResult
  >(
    runId
      ? `${API_URL}/algo/sweep/runs/${runId}`
      : null,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) => {
        if (!latest) return 3_000;
        if (
          latest.status === "completed"
          || latest.status === "failed"
        ) return 0;
        return 3_000;
      },
    },
  );
  return { run: data, isLoading, error, mutate };
}

export async function startSweepRun(
  config: SweepConfig,
): Promise<{ sweep_run_id: string }> {
  const r = await apiFetch(
    `${API_URL}/algo/sweep/run`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    },
  );
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`Sweep start failed: ${body}`);
  }
  return r.json();
}
