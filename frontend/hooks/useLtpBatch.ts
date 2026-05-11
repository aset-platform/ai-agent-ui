"use client";
/**
 * SWR batch hook for /v1/algo/ltp/batch.
 *
 * Polls live LTPs every 5s during market hours (09:15-15:30 IST,
 * Mon-Fri); 60s otherwise to save bandwidth. Backend resolves
 * each ticker Redis-first → OHLCV-close fallback so off-hours
 * data never goes stale-empty, just stale-EOD-close with an
 * `ohlcv_close` source tag.
 *
 * De-dupes the input array, sorts for stable cache keys, and
 * caps at 200 tickers (server limit). Larger callers should
 * partition.
 */

import { useMemo } from "react";
import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export type LtpSource = "live_ltp" | "ohlcv_close" | "unknown";

export interface LtpEntry {
  price: number | null;
  source: LtpSource;
}

export type LtpMap = Record<string, LtpEntry>;

const MAX_PER_CALL = 200;

async function fetcher(url: string): Promise<LtpMap> {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

/** True if `now` falls within Indian market hours
 *  (09:15-15:30 IST, Mon-Fri). The 30s grace either side
 *  prevents a UTC-rounding edge from flipping the poll
 *  interval at the exact open/close minute. */
export function isMarketHoursIST(now: Date = new Date()): boolean {
  // Convert to IST
  const ist = new Date(
    now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }),
  );
  const day = ist.getDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const minutes = ist.getHours() * 60 + ist.getMinutes();
  // 09:14 → 15:31 (1-minute padding either side)
  return minutes >= 9 * 60 + 14 && minutes <= 15 * 60 + 31;
}

export function useLtpBatch(tickers: readonly string[]) {
  // Stable, deduped, sorted key — SWR uses string equality.
  const key = useMemo(() => {
    if (!tickers || tickers.length === 0) return null;
    const cleaned = Array.from(
      new Set(tickers.map((t) => t.trim()).filter(Boolean)),
    );
    if (cleaned.length === 0) return null;
    if (cleaned.length > MAX_PER_CALL) {
      // Cap on the client side too — the server returns 400 above.
      cleaned.length = MAX_PER_CALL;
    }
    cleaned.sort();
    return `${API_URL}/algo/ltp/batch?tickers=${cleaned.join(",")}`;
  }, [tickers]);

  const refreshInterval = isMarketHoursIST() ? 5_000 : 60_000;

  const { data, error, isLoading, mutate } = useSWR<LtpMap>(
    key,
    fetcher,
    {
      refreshInterval,
      revalidateOnFocus: true,
      // Tighter than refreshInterval so a refocus inside the
      // window still hits the network when ticks are flowing.
      dedupingInterval: 2_000,
    },
  );

  return {
    map: data ?? {},
    error: error instanceof Error ? error : undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}

/** Read-side helper: extract a single ticker's price + source
 *  from a batch result. Returns null/unknown if the ticker
 *  wasn't part of the batch or the call hasn't returned yet. */
export function pickLtp(
  map: LtpMap,
  ticker: string,
): LtpEntry {
  return map[ticker] ?? { price: null, source: "unknown" };
}
