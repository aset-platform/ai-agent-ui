"use client";
/**
 * Recompute portfolio totals + per-ticker live values using
 * /v1/algo/ltp/batch — falls back to the holding's
 * current_price (already populated by the backend from yfinance
 * / OHLCV close) when the live cache hasn't seen the ticker.
 *
 * Returns:
 *  - totals: Record<currency, sum of qty*livePrice>
 *  - liveByTicker: Record<ticker, {price, source}>
 *  - meta: counts of live vs eod vs unknown for the chip
 *
 * Use alongside `usePortfolio()`:
 *   const { holdings, totals: baseTotals } = usePortfolio();
 *   const live = useLivePortfolioTotals(holdings, baseTotals);
 *   <Hero portfolioTotals={live.totals} />
 */

import { useMemo } from "react";

import type { PortfolioHolding } from "@/hooks/usePortfolio";
import { useLtpBatch, type LtpSource } from "@/hooks/useLtpBatch";

export interface LivePortfolioMeta {
  /** How many holdings have a fresh live LTP (≤60s old). */
  live_count: number;
  /** How many fell back to OHLCV close (off-hours / no WS). */
  eod_count: number;
  /** How many couldn't be resolved at all. */
  unknown_count: number;
  /** Total holdings considered. */
  total_count: number;
}

export interface LivePortfolioTotals {
  /** Per-currency portfolio current value, computed from live
   *  LTP × qty (with EOD/static fallback per ticker). */
  totals: Record<string, number>;
  /** Per-ticker resolved live entry — handy for child widgets
   *  (AssetPerformanceWidget, holdings table) that need to show
   *  their own live values without re-fetching. */
  liveByTicker: Record<string, { price: number; source: LtpSource }>;
  meta: LivePortfolioMeta;
  loading: boolean;
}

export function useLivePortfolioTotals(
  holdings: readonly PortfolioHolding[],
  baseTotals: Record<string, number>,
): LivePortfolioTotals {
  const tickers = useMemo(
    () => holdings.map((h) => h.ticker),
    [holdings],
  );
  const { map: ltpMap, loading } = useLtpBatch(tickers);

  return useMemo(() => {
    const totals: Record<string, number> = {};
    const liveByTicker: Record<
      string,
      { price: number; source: LtpSource }
    > = {};
    let live_count = 0;
    let eod_count = 0;
    let unknown_count = 0;

    for (const h of holdings) {
      const entry = ltpMap[h.ticker];
      // Resolution: prefer live, then EOD from batch, then the
      // holding's own current_price (populated by backend from
      // its own pricing pipeline — last resort but always present
      // for holdings that completed the daily refresh).
      let price: number | null = null;
      let source: LtpSource = "unknown";
      if (entry?.price != null && entry.source === "live_ltp") {
        price = entry.price;
        source = "live_ltp";
        live_count += 1;
      } else if (
        entry?.price != null && entry.source === "ohlcv_close"
      ) {
        price = entry.price;
        source = "ohlcv_close";
        eod_count += 1;
      } else if (h.current_price != null) {
        price = h.current_price;
        source = "ohlcv_close";
        eod_count += 1;
      } else {
        unknown_count += 1;
      }

      if (price != null) {
        liveByTicker[h.ticker] = { price, source };
        totals[h.currency] =
          (totals[h.currency] ?? 0) + price * h.quantity;
      }
    }

    // If the batch hasn't returned yet, fall back to the
    // server-computed totals so the UI doesn't blink to zero
    // on first paint. This keeps SSR + first-render stable.
    if (loading || Object.keys(totals).length === 0) {
      return {
        totals: baseTotals,
        liveByTicker,
        meta: {
          live_count,
          eod_count,
          unknown_count,
          total_count: holdings.length,
        },
        loading,
      };
    }

    return {
      totals,
      liveByTicker,
      meta: {
        live_count,
        eod_count,
        unknown_count,
        total_count: holdings.length,
      },
      loading,
    };
  }, [holdings, ltpMap, baseTotals, loading]);
}
