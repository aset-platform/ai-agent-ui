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

interface EventsPage {
  events: PaperEvent[];
  total: number;
}

async function fetcher(url: string): Promise<EventsPage> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const events: PaperEvent[] = await r.json();
  const totalHeader = r.headers.get("X-Total-Count");
  const total = totalHeader != null ? Number(totalHeader) : 0;
  return { events, total };
}

export function usePaperEvents(limit = 100, offset = 0) {
  const key =
    `${API_URL}/algo/paper/events?limit=${limit}&offset=${offset}`;
  const { data, error, isLoading } = useSWR<EventsPage>(
    key,
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 5_000 },
  );
  return {
    events: data?.events ?? [],
    total: data?.total ?? 0,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load events"
      : null,
  };
}
