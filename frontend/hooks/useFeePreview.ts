// frontend/hooks/useFeePreview.ts
"use client";
/**
 * SWR fetcher for /v1/algo/fees/preview. Debounces 300 ms so
 * typing in the qty/price inputs doesn't fire a request per
 * keystroke. Mirrors the AA hook patterns (apiFetch, no
 * revalidateOnFocus, dedupingInterval).
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface FeeBreakdown {
  brokerage_inr: string;
  stt_inr: string;
  exchange_txn_inr: string;
  sebi_inr: string;
  stamp_duty_inr: string;
  gst_inr: string;
  dp_charges_inr: string;
  total_inr: string;
  rates_version: string;
}

export interface FeePreviewParams {
  symbol: string;
  exchange: "NSE" | "BSE";
  side: "BUY" | "SELL";
  product: "DELIVERY" | "INTRADAY";
  qty: number;
  price: number;
}

async function fetcher(url: string): Promise<FeeBreakdown> {
  const r = await apiFetch(url);
  if (!r.ok) {
    let detail = "";
    try {
      const body = await r.json();
      detail = body?.detail ?? "";
    } catch {
      // ignore parse errors
    }
    throw new Error(
      `Fee preview failed: HTTP ${r.status}` +
        (detail ? ` — ${detail}` : ""),
    );
  }
  return r.json();
}

export function useFeePreview(params: FeePreviewParams | null) {
  const key = params
    ? `${API_URL}/algo/fees/preview?${new URLSearchParams({
        symbol: params.symbol,
        exchange: params.exchange,
        side: params.side,
        product: params.product,
        qty: String(params.qty),
        price: String(params.price),
      }).toString()}`
    : null;

  const { data, error, isLoading } = useSWR<FeeBreakdown>(
    key,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 60_000,
    },
  );

  return {
    value: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Fee preview failed"
      : null,
  };
}
