import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { BudgetPanel } from "../BudgetPanel";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/hooks/useBudget", () => ({
  useUserBudget: () => ({
    budget: {
      user_id: "u",
      allocated_inr: "100000",
      enabled: true,
      open_pos_cost: "35000",
      active_reserved: "8500",
      internal_headroom: "56500",
      kite_available: "78200",
      available: "56500",
    },
    isLoading: false,
    error: null,
    mutate: vi.fn(),
  }),
  useActiveReservations: () => ({
    reservations: [
      {
        reservation_id: "r1",
        strategy_id: "s1",
        state: "SUBMITTED",
        ticker: "INFY.NS",
        side: "BUY",
        qty: 50,
        reserved_inr: "7500",
        filled_qty: 0,
        filled_inr: "0",
        kite_order_id: "kite-1",
        transitioned_at: "2026-05-24T10:00:00Z",
      },
    ],
    mutate: vi.fn(),
  }),
  forceReleaseReservation: vi.fn(),
}));

afterEach(() => cleanup());

describe("BudgetPanel", () => {
  it("renders four tiles with values", () => {
    render(<BudgetPanel />);
    expect(
      screen.getByTestId("budget-tile-allocated"),
    ).toBeDefined();
    expect(
      screen.getByTestId("budget-tile-open-positions"),
    ).toBeDefined();
    expect(
      screen.getByTestId("budget-tile-pending"),
    ).toBeDefined();
    expect(
      screen.getByTestId("budget-tile-available"),
    ).toBeDefined();
  });

  it("renders Kite wallet row", () => {
    render(<BudgetPanel />);
    expect(
      screen.getByTestId("budget-kite-wallet-row"),
    ).toBeDefined();
  });

  it("renders active reservations table", () => {
    render(<BudgetPanel />);
    expect(
      screen.getByTestId("budget-active-reservations-table"),
    ).toBeDefined();
    expect(
      screen.getByTestId("budget-reservation-row-r1"),
    ).toBeDefined();
  });
});
