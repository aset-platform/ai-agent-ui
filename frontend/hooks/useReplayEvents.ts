"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface ReplayEvent {
  event_id: string;
  ts_ns: number;
  ts_date: string;
  mode: string;
  strategy_id: string | null;
  type: string;
  payload: Record<string, unknown>;
}

export interface ReplayFilters {
  mode?: string;
  type?: string;
  strategy_id?: string;
  ts_date?: string;
  limit?: number;
}

async function fetcher(url: string): Promise<ReplayEvent[]> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function buildKey(f: ReplayFilters): string {
  const sp = new URLSearchParams();
  if (f.mode) sp.set("mode", f.mode);
  if (f.type) sp.set("type", f.type);
  if (f.strategy_id) sp.set("strategy_id", f.strategy_id);
  if (f.ts_date) sp.set("ts_date", f.ts_date);
  sp.set("limit", String(f.limit ?? 200));
  return `${API_URL}/algo/replay/events?${sp.toString()}`;
}

export function useReplayEvents(filters: ReplayFilters = {}) {
  const { data, error, isLoading } = useSWR<ReplayEvent[]>(
    buildKey(filters),
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 10_000 },
  );
  return {
    events: data ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load events"
      : null,
  };
}
