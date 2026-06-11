import { describe, expect, it } from "vitest";

import {
  computeLiveMeta,
  overlayLiveAllocation,
  shouldOverlayAllocation,
} from "../liveAllocation";
import type { AllocationResponse } from "@/lib/types";

const base: AllocationResponse = {
  sectors: [
    {
      sector: "IT",
      value: 1000,
      weight_pct: 50,
      stock_count: 1,
      tickers: ["TCS.NS"],
    },
    {
      sector: "Energy",
      value: 1000,
      weight_pct: 50,
      stock_count: 1,
      tickers: ["RELIANCE.NS"],
    },
  ],
  total_value: 2000,
  currency: "INR",
};

describe("shouldOverlayAllocation", () => {
  it("is false while loading", () => {
    expect(
      shouldOverlayAllocation(true, {
        "TCS.NS": { price: 1, source: "x" },
      }),
    ).toBe(false);
  });
  it("is false with no live prices", () => {
    expect(shouldOverlayAllocation(false, {})).toBe(false);
  });
  it("is true once a price resolves and not loading", () => {
    expect(
      shouldOverlayAllocation(false, {
        "TCS.NS": { price: 1, source: "live_ltp" },
      }),
    ).toBe(true);
  });
});

describe("overlayLiveAllocation", () => {
  it("recomputes sector values + weights from live price x qty", () => {
    const live = {
      "TCS.NS": { price: 200, source: "live_ltp" }, // 200 * 10 = 2000
      "RELIANCE.NS": { price: 100, source: "live_ltp" }, // 100 * 10 = 1000
    };
    const qty = { "TCS.NS": 10, "RELIANCE.NS": 10 };
    const out = overlayLiveAllocation(base, live, qty);

    expect(out.total_value).toBe(3000);
    const itSec = out.sectors.find((s) => s.sector === "IT")!;
    const enSec = out.sectors.find((s) => s.sector === "Energy")!;
    expect(itSec.value).toBe(2000);
    expect(enSec.value).toBe(1000);
    expect(itSec.weight_pct).toBeCloseTo(66.6667, 3);
    expect(enSec.weight_pct).toBeCloseTo(33.3333, 3);
  });

  it("keeps the server value for a sector with no resolved price", () => {
    const live = { "TCS.NS": { price: 200, source: "live_ltp" } };
    const qty = { "TCS.NS": 10 }; // RELIANCE not priced
    const out = overlayLiveAllocation(base, live, qty);

    const enSec = out.sectors.find((s) => s.sector === "Energy")!;
    expect(enSec.value).toBe(1000); // fell back to server value
    const itSec = out.sectors.find((s) => s.sector === "IT")!;
    expect(itSec.value).toBe(2000); // live
    expect(out.total_value).toBe(3000);
  });

  it("returns the base unchanged when nothing resolves", () => {
    const out = overlayLiveAllocation(base, {}, {});
    expect(out).toBe(base);
  });

  it("returns the base unchanged when there are no sectors", () => {
    const empty: AllocationResponse = {
      sectors: [],
      total_value: 0,
      currency: "INR",
    };
    expect(overlayLiveAllocation(empty, {}, {})).toBe(empty);
  });
});

describe("computeLiveMeta", () => {
  it("classifies live / eod / unknown over the given tickers", () => {
    const live = {
      "TCS.NS": { price: 1, source: "live_ltp" },
      "INFY.NS": { price: 1, source: "live_ltp" },
      "AAPL": { price: 1, source: "ohlcv_close" },
      // "TSLA" intentionally absent -> unknown
    };
    const meta = computeLiveMeta(
      ["TCS.NS", "INFY.NS", "AAPL", "TSLA"],
      live,
    );
    expect(meta).toEqual({
      live_count: 2,
      eod_count: 1,
      unknown_count: 1,
      total_count: 4,
    });
  });

  it("denominator is the scoped ticker count, not the map size", () => {
    // 13 visible india tickers all live; 5 US holdings in the
    // map but NOT in the scoped list -> total stays 13.
    const live: Record<string, { price: number; source: string }> = {};
    const india = Array.from({ length: 13 }, (_, i) => `IN${i}.NS`);
    for (const t of india) live[t] = { price: 1, source: "live_ltp" };
    for (const t of ["AAPL", "MSFT", "NFLX", "PLTR", "TSLA"]) {
      live[t] = { price: 1, source: "ohlcv_close" };
    }
    const meta = computeLiveMeta(india, live);
    expect(meta.total_count).toBe(13);
    expect(meta.live_count).toBe(13);
    expect(meta.eod_count).toBe(0);
  });

  it("handles an empty ticker list", () => {
    expect(computeLiveMeta([], {})).toEqual({
      live_count: 0,
      eod_count: 0,
      unknown_count: 0,
      total_count: 0,
    });
  });
});
