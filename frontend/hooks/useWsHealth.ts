"use client";
/**
 * useWsHealth — OBS-1 SWR hook for the Kite WS health dot.
 *
 * Polls GET /v1/algo/live/ws-health every 10 seconds while the
 * Live (or Dry-run) segment is mounted. The endpoint is read-only
 * and never spins up a multiplexer for the user, so a poll for an
 * idle user is cheap.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface WsHealth {
  connected: boolean;
  subscriber_count: number;
  subscribed_tokens: number;
  last_tick_at: string | null;
  tick_age_seconds: number | null;
  tick_count_today: number;
}

export const WS_HEALTH_KEY = `${API_URL}/algo/live/ws-health`;

async function fetcher(url: string): Promise<WsHealth> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useWsHealth() {
  const { data, error, isLoading } = useSWR<WsHealth>(
    WS_HEALTH_KEY,
    fetcher,
    {
      refreshInterval: 10_000,
      revalidateOnFocus: false,
      dedupingInterval: 5_000,
    },
  );
  return {
    health: data ?? null,
    loading: isLoading,
    error: error instanceof Error ? error.message : null,
  };
}
