import { describe, it, expect, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useAlgoPositions } from "@/hooks/useAlgoPositions";

// The watchlist widget shows LIVE algo positions only — paper-mode
// rows must be filtered out. live + source-absent (defaults live) stay.
vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      positions: [
        {
          tradingsymbol: "PAPERX",
          internal_ticker: "PAPERX.NS",
          product: "CNC",
          quantity: 10,
          avg_price: "100.00",
          last_price: "110.00",
          pnl_inr: "100.00",
          pnl_pct: "10.00",
          strategy_id: "00000000-0000-0000-0000-000000000001",
          strategy_name: "Paper",
          entry_ts: "2026-05-24T10:00:00Z",
          days_held: 0,
          t1_pending: false,
          source: "paper",
        },
        {
          tradingsymbol: "LIVEX",
          internal_ticker: "LIVEX.NS",
          product: "MIS",
          quantity: 5,
          avg_price: "200.00",
          last_price: "210.00",
          pnl_inr: "50.00",
          pnl_pct: "5.00",
          strategy_id: "00000000-0000-0000-0000-000000000002",
          strategy_name: "Live",
          entry_ts: "2026-05-24T10:00:00Z",
          days_held: 0,
          t1_pending: false,
          source: "live",
        },
        {
          tradingsymbol: "DEFX",
          internal_ticker: "DEFX.NS",
          product: "MIS",
          quantity: 3,
          avg_price: "300.00",
          last_price: "300.00",
          pnl_inr: "0.00",
          pnl_pct: "0.00",
          strategy_id: "00000000-0000-0000-0000-000000000003",
          strategy_name: "Default",
          entry_ts: "2026-05-24T10:00:00Z",
          days_held: 0,
          t1_pending: false,
          // source absent → defaults to "live"
        },
      ],
      as_of: "2026-05-24T10:30:00Z",
      market_open: true,
    }),
  }),
}));

describe("useAlgoPositions — live-only filter", () => {
  it("excludes paper-source rows, keeps live + source-absent", async () => {
    const { result } = renderHook(() => useAlgoPositions());
    await waitFor(() => {
      expect(result.current.positions.length).toBeGreaterThan(0);
    });
    const syms = result.current.positions.map((p) => p.tradingsymbol);
    expect(syms).toContain("LIVEX");
    expect(syms).toContain("DEFX");
    expect(syms).not.toContain("PAPERX");
  });
});
