"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export type PaperEventType =
  | "signal_generated"
  | "signal_rejected"
  | "order_submitted"
  | "order_filled"
  | "order_cancelled"
  | "position_opened"
  | "position_closed"
  | "risk_breach"
  | "broker_connected"
  | "broker_disconnected";

export interface PaperEvent {
  event_id: string;
  ts_ns: number;
  ts_date: string;
  strategy_id: string | null;
  type: string;
  payload: Record<string, unknown>;
}

async function fetcher(url: string): Promise<PaperEvent[]> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function usePaperEvents(limit = 100, offset = 0) {
  const key =
    `${API_URL}/algo/paper/events?limit=${limit}&offset=${offset}`;
  const { data, error, isLoading } = useSWR<PaperEvent[]>(
    key,
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 5_000 },
  );
  const events = data ?? [];
  return {
    events,
    // ``hasMore`` is a heuristic: if we got back exactly ``limit``
    // rows we don't know whether there are more — assume yes. If
    // we got back fewer, the result set is exhausted at this offset.
    hasMore: events.length >= limit,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load events"
      : null,
  };
}
