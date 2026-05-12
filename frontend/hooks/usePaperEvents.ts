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

export type EventsMode = "paper" | "live" | "backtest" | null;
export type EventsView = "paper" | "dryrun" | "live" | "all";

export function usePaperEvents(
  limit = 100,
  offset = 0,
  mode: EventsMode = null,
  dryRun: boolean | null = null,
  /** Optional server-side filter on event type
   *  (e.g. "order_filled_live"). When set, only events of that
   *  type count toward `limit` — useful for narrow widgets like
   *  RecentFillsTape that would otherwise be drowned out by
   *  high-volume types (signal_generated, signal_rejected). */
  type: string | null = null,
  /** Optional IST-date floor (YYYY-MM-DD). Server filters
   *  ``ts_date >= since_date`` so today-only widgets don't bleed
   *  prior sessions into the visible window. */
  sinceDate: string | null = null,
) {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (mode != null) params.set("mode", mode);
  if (dryRun != null) params.set("dry_run", String(dryRun));
  if (type != null) params.set("type", type);
  if (sinceDate != null) params.set("since_date", sinceDate);
  const key = `${API_URL}/algo/paper/events?${params.toString()}`;
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
