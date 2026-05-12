"use client";
/**
 * SWR hook for order_submitted_live events (Order-Safety PR #1).
 *
 * Polls GET /v1/algo/live/order-submissions?limit=50 every 30 s.
 * Mirrors useKitePostbacks shape so the Submissions tab on
 * KitePostbackPanel can reuse the same row + toggle layout.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

/** One row from GET /v1/algo/live/order-submissions.
 *  Top-level fields are flattened from the event payload so the
 *  table doesn't have to drill into `raw` for common columns. */
export interface OrderSubmission {
  /** ISO 8601 UTC timestamp of when the event was emitted. */
  event_ts: string;
  session_id: string;
  strategy_id: string | null;
  internal_order_id: string;
  kite_order_id: string;
  symbol: string;
  /** "BUY" | "SELL" */
  side: string;
  qty: number;
  dry_run: boolean;
  /** Full payload (request / context / response) for the
   *  expand-row JSON toggle. */
  raw: Record<string, unknown>;
}

interface SubmissionsResponse {
  submissions: OrderSubmission[];
}

const KEY = `${API_URL}/algo/live/order-submissions?limit=50`;

async function fetcher(url: string): Promise<OrderSubmission[]> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = (await r.json()) as SubmissionsResponse;
  return body.submissions ?? [];
}

export function useOrderSubmissions() {
  const { data, error, isLoading, mutate } = useSWR<OrderSubmission[]>(
    KEY,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 30_000,
      dedupingInterval: 15_000,
    },
  );

  return {
    submissions: data ?? [],
    isLoading,
    error: error instanceof Error ? error.message : null,
    mutate,
  };
}
