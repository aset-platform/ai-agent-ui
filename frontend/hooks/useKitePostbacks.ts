"use client";
/**
 * SWR hook for Kite postback events (OBS-4).
 *
 * Polls GET /v1/algo/live/postbacks?limit=50 every 30 s.
 * The endpoint is implemented in OBS-2 and filters by
 * the authenticated user automatically.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { todayIstIso } from "@/lib/datetime";

/** Subset of the kite_postback_received event payload
 *  returned by GET /v1/algo/live/postbacks. */
export interface KitePostback {
  /** ISO 8601 UTC timestamp of when the postback was received. */
  event_ts: string;
  tradingsymbol: string;
  /** COMPLETE | REJECTED | CANCELLED | UPDATE */
  status: string;
  filled_quantity: number;
  average_price: number;
  /** Full raw Kite postback payload for the JSON expand row. */
  raw: Record<string, unknown>;
}

// ASETPLTFRM-382 — restrict the Live → Postbacks panel to today's
// real-money rows. ``dry_run=false`` treats NULL-payload-dry_run as
// real money (post-ASETPLTFRM-374 the runtime omits the field for
// real-money events); ``since_date`` is today in IST so the panel
// never bleeds prior sessions.
function buildPostbacksKey(): string {
  const qs = new URLSearchParams({
    limit: "50",
    dry_run: "false",
    since_date: todayIstIso(),
  });
  return `${API_URL}/algo/live/postbacks?${qs.toString()}`;
}

async function fetcher(url: string): Promise<KitePostback[]> {
  const r = await apiFetch(url);
  if (!r.ok) {
    let detail = "";
    try {
      const body = await r.json();
      detail = body?.detail ?? "";
    } catch {
      // body wasn't JSON — fall back to plain status.
    }
    throw new Error(
      `HTTP ${r.status}${detail ? ` — ${detail}` : ""}`,
    );
  }
  return r.json();
}

export function useKitePostbacks() {
  const { data, error, isLoading, mutate } = useSWR<KitePostback[]>(
    buildPostbacksKey(),
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 30_000,
      dedupingInterval: 15_000,
    },
  );

  return {
    postbacks: data ?? [],
    isLoading,
    error: error instanceof Error ? error.message : null,
    mutate,
  };
}
