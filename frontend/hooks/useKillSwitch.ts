"use client";

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface KillSwitchState {
  user_id: string;
  active: boolean;
  set_by: string | null;
  set_at: string | null;
  reason: string | null;
}

const KEY = `${API_URL}/algo/kill-switch`;

async function fetcher(url: string): Promise<KillSwitchState> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useKillSwitch() {
  const { data, error, isLoading } = useSWR<KillSwitchState>(
    KEY,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );
  return {
    state: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load kill switch"
      : null,
  };
}

export async function armKillSwitch(reason?: string): Promise<void> {
  const r = await apiFetch(`${KEY}/arm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? null }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  await mutate(KEY);
}

export async function disarmKillSwitch(): Promise<void> {
  const r = await apiFetch(`${KEY}/disarm`, { method: "POST" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  await mutate(KEY);
}

export interface PanicCloseResult {
  tickers_closed: string[];
  orders_submitted: number;
  errors: string[];
  note?: string;
}

/** Submits SELL orders for every algo-opened position via Kite,
 *  then arms the kill switch. Destructive — caller MUST gate
 *  behind a confirm dialog. Resolves with a summary the caller
 *  surfaces in a toast. */
export async function panicCloseAll(): Promise<PanicCloseResult> {
  const r = await apiFetch(`${KEY}/panic-close-all`, {
    method: "POST",
  });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const body = await r.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* swallow */
    }
    throw new Error(detail);
  }
  await mutate(KEY);
  return r.json();
}
