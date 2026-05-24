import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { AlgoPositionsTab } from "../AlgoPositionsTab";

const ROW = {
  tradingsymbol: "INFY",
  internal_ticker: "INFY.NS",
  product: "MIS" as const,
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
};

vi.mock("@/hooks/useAlgoPositions", () => ({
  useAlgoPositions: vi.fn(),
}));

import { useAlgoPositions } from "@/hooks/useAlgoPositions";

afterEach(() => cleanup());

describe("AlgoPositionsTab", () => {
  it("renders rows when positions present", () => {
    (useAlgoPositions as ReturnType<typeof vi.fn>).mockReturnValue({
      positions: [ROW],
      asOf: "2026-05-24T10:30:00Z",
      marketOpen: true,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });
    render(<AlgoPositionsTab />);
    expect(
      screen.getByTestId("dashboard-algo-positions-table"),
    ).toBeDefined();
    expect(screen.getByTestId("dashboard-algo-row-INFY")).toBeDefined();
  });

  it("renders empty state with deep link", () => {
    (useAlgoPositions as ReturnType<typeof vi.fn>).mockReturnValue({
      positions: [],
      asOf: null,
      marketOpen: false,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });
    render(<AlgoPositionsTab />);
    const cta = screen.getByTestId("dashboard-algo-positions-cta");
    expect(cta).toBeDefined();
    expect(cta.getAttribute("href")).toBe(
      "/algo-trading/strategies?tab=live",
    );
  });

  it("row click calls onSelectTicker with internal_ticker", () => {
    (useAlgoPositions as ReturnType<typeof vi.fn>).mockReturnValue({
      positions: [ROW],
      asOf: "2026-05-24T10:30:00Z",
      marketOpen: true,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    });
    const onSelectTicker = vi.fn();
    render(<AlgoPositionsTab onSelectTicker={onSelectTicker} />);
    fireEvent.click(screen.getByTestId("dashboard-algo-row-INFY"));
    expect(onSelectTicker).toHaveBeenCalledWith("INFY.NS");
  });
});
