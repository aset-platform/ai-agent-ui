"use client";

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface InstrumentRow {
  instrument_token: number;
  tradingsymbol: string;
  exchange: string;
  segment: string;
  lot_size: number;
  tick_size: number;
  our_ticker: string | null;
  loaded_at: string | null;
}

export interface InstrumentsResponse {
  rows: InstrumentRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface InstrumentsParams {
  search: string;
  exchange: string;
  page: number;
  pageSize: number;
}

function buildKey(p: InstrumentsParams): string {
  const sp = new URLSearchParams({
    search: p.search,
    exchange: p.exchange,
    page: String(p.page),
    page_size: String(p.pageSize),
  });
  return `${API_URL}/algo/instruments?${sp.toString()}`;
}

async function fetcher(url: string): Promise<InstrumentsResponse> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useInstruments(params: InstrumentsParams) {
  const key = buildKey(params);
  const { data, error, isLoading } = useSWR<InstrumentsResponse>(
    key,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );
  return {
    value: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load instruments"
      : null,
    refreshKey: key,
  };
}

export async function refreshInstruments(): Promise<number> {
  const r = await apiFetch(`${API_URL}/algo/instruments/refresh`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = (await r.json()) as { instruments_loaded: number };
  // Trigger SWR re-fetch on every cached instruments key.
  await mutate(
    (k) => typeof k === "string" && k.startsWith(`${API_URL}/algo/instruments`),
  );
  return body.instruments_loaded;
}
