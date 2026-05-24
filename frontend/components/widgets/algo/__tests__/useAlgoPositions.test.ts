import { describe, it, expect, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useAlgoPositions } from "@/hooks/useAlgoPositions";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      positions: [
        {
          tradingsymbol: "INFY",
          internal_ticker: "INFY.NS",
          product: "MIS",
          quantity: 50,
          avg_price: "1500.00",
          last_price: "1572.50",
          pnl_inr: "3625.00",
          pnl_pct: "4.83",
          strategy_id: "00000000-0000-0000-0000-000000000001",
          strategy_name: "RSI(2) v3",
          entry_ts: "2026-05-24T10:00:00Z",
          days_held: 0,
          t1_pending: false,
        },
      ],
      as_of: "2026-05-24T10:30:00Z",
      market_open: true,
    }),
  }),
}));

describe("useAlgoPositions", () => {
  it("returns positions array on success", async () => {
    const { result } = renderHook(() => useAlgoPositions());
    await waitFor(() => {
      expect(result.current.positions.length).toBe(1);
    });
    expect(result.current.positions[0].tradingsymbol).toBe(
      "INFY",
    );
    expect(result.current.marketOpen).toBe(true);
  });
});
