/**
 * Live overlay for the Sector Allocation donut.
 *
 * Recomputes each sector's value from the live LTP map
 * (`liveByTicker`, produced by `useLivePortfolioTotals` —
 * Kite live LTP → OHLCV-close → holding.current_price) × the
 * holding quantity, then re-derives weights + total. This keeps
 * the pie consistent with the live Hero / Asset Performance
 * widgets instead of showing a static EOD snapshot.
 *
 * Pure + deterministic so it can be unit-tested without React.
 * The caller is responsible for guarding on load state (see
 * `shouldOverlayAllocation`) — when live data isn't ready yet the
 * server-computed allocation should be shown unchanged.
 */
import type { AllocationResponse } from "@/lib/types";

export interface LivePrice {
  price: number;
  source: string;
}

/**
 * True when a live overlay is worth applying — we have at least
 * one resolved live/eod price. Otherwise the caller shows the
 * server allocation unchanged (avoids blanking the pie on first
 * paint / before the LTP batch returns).
 */
export function shouldOverlayAllocation(
  loading: boolean,
  liveByTicker: Record<string, LivePrice>,
): boolean {
  return !loading && Object.keys(liveByTicker).length > 0;
}

/**
 * Return a new AllocationResponse with sector values recomputed
 * from live prices × quantity. A sector whose tickers don't
 * resolve to any live price keeps its original server value, so a
 * sector never silently vanishes from the donut. If the overlay
 * resolves nothing at all, the original is returned untouched.
 */
export function overlayLiveAllocation(
  base: AllocationResponse,
  liveByTicker: Record<string, LivePrice>,
  qtyByTicker: Record<string, number>,
): AllocationResponse {
  if (!base.sectors.length) return base;

  let resolvedAny = false;
  let total = 0;
  const sectors = base.sectors.map((s) => {
    let value = 0;
    let resolved = false;
    for (const ticker of s.tickers) {
      const live = liveByTicker[ticker];
      const qty = qtyByTicker[ticker];
      if (live != null && qty != null) {
        value += live.price * qty;
        resolved = true;
      }
    }
    // Keep the server value for a sector we couldn't price live,
    // so weights stay sensible rather than collapsing to 0.
    const finalValue = resolved ? value : s.value;
    if (resolved) resolvedAny = true;
    total += finalValue;
    return { ...s, value: finalValue };
  });

  if (!resolvedAny || total <= 0) return base;

  return {
    ...base,
    sectors: sectors.map((s) => ({
      ...s,
      weight_pct: (s.value / total) * 100,
    })),
    total_value: total,
  };
}
