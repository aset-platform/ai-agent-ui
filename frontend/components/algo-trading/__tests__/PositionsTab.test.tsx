/**
 * PositionsTab — intraday MIS positions w/ strategy attribution.
 * Slice 4 of three-page split.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/hooks/useLivePositions", () => ({
  useLivePositions: () => ({
    rows: [
      {
        tradingsymbol: "ITC",
        exchange: "NSE",
        quantity: 8,
        average_price: "307.33",
        last_price: "311.20",
        pnl_inr: "30.96",
        pnl_pct: "1.26",
        product: "MIS",
        strategy_id: "v3",
        strategy_name: "V3 Multi",
        entry_ts_utc: "2026-05-11T04:19:54Z",
        entry_reason: "BULL · momentum_z=1.4",
      },
      {
        tradingsymbol: "MANUAL",
        exchange: "NSE",
        quantity: 1,
        average_price: "100",
        last_price: "100",
        pnl_inr: "0",
        pnl_pct: "0",
        product: "MIS",
        strategy_id: null,
        strategy_name: null,
        entry_ts_utc: null,
        entry_reason: null,
      },
    ],
    ledger_drift: false,
    loading: false,
    error: null,
    refresh: () => {},
  }),
}));

import { PositionsTab } from "../live/PositionsTab";

afterEach(() => {
  cleanup();
});

describe("PositionsTab", () => {
  it("renders attributed + unattributed rows w/ dash fallback", () => {
    render(<PositionsTab />);
    const table = screen.getByTestId("positions-table");
    const txt = table.textContent ?? "";

    // Both ticker symbols appear
    expect(txt).toContain("ITC");
    expect(txt).toContain("MANUAL");

    // Attribution surfaces strategy name for ITC
    expect(txt).toContain("V3 Multi");

    // Unattributed row uses dash placeholders (strategy + entry +
    // reason all "—")
    const dashes = (txt.match(/—/g) ?? []).length;
    expect(dashes).toBeGreaterThanOrEqual(3);
  });
});
