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
  /** 'paper' or 'live' — chosen by the start_run handler. */
  mode: "paper" | "live";
  /** Only meaningful when mode='live' — true if KiteAdapter
   *  was initialized in dry-run mode (synthetic responses). */
  dry_run: boolean;
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

export type PaperRunSource = "replay" | "live-ws";

/** Trading mode the user is starting a run in. Backend uses this
 *  to choose between PaperRuntime (mode=paper) and LiveRuntime
 *  (mode=live). When mode=live, ALGO_LIVE_DRY_RUN env decides
 *  whether KiteAdapter short-circuits to synthetic responses. */
export type RunMode = "paper" | "live";

export async function startPaperRun(
  strategyId: string,
  fixturePath: string,
  initialCapitalInr: string,
  source: PaperRunSource = "replay",
  mode: RunMode = "paper",
): Promise<void> {
  const body: Record<string, string> = {
    strategy_id: strategyId,
    initial_capital_inr: initialCapitalInr,
    source,
    mode,
  };
  if (source === "replay") {
    body.fixture_path = fixturePath;
  }
  const r = await apiFetch(KEY, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
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

export interface PaperFixture {
  path: string;
  n_ticks: number;
  distinct_tickers: number;
  sample_tickers: string[];
  size_bytes: number;
}

const FIXTURES_KEY = `${API_URL}/algo/paper/fixtures`;

async function fixturesFetcher(url: string): Promise<PaperFixture[]> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function usePaperFixtures() {
  const { data, error, isLoading } = useSWR<PaperFixture[]>(
    FIXTURES_KEY,
    fixturesFetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );
  return {
    fixtures: data ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load fixtures"
      : null,
  };
}
