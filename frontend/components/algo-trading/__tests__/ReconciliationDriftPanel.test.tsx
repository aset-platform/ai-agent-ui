// frontend/components/algo-trading/__tests__/ReconciliationDriftPanel.test.tsx
/**
 * Vitest unit tests for ReconciliationDriftPanel (V2-3).
 *
 * Tests:
 *  1. Renders nothing when drift list is empty (auto-clear).
 *  2. Renders chip with count when N drifts exist.
 *  3. Shows expanded table on "Show" toggle.
 *  4. Applies red chip for critical (> 3 runs) drifts.
 *  5. Applies amber chip for warning (≤ 3 runs) drifts.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

afterEach(() => cleanup());

// Mock the hook so tests don't make real HTTP calls.
vi.mock("@/hooks/useReconciliation", () => ({
  useReconciliation: vi.fn(),
}));

import { useReconciliation } from "@/hooks/useReconciliation";
import { ReconciliationDriftPanel } from "../ReconciliationDriftPanel";

const mockUseReconciliation = useReconciliation as ReturnType<
  typeof vi.fn
>;

describe("ReconciliationDriftPanel", () => {
  it("renders nothing when drift list is empty", () => {
    mockUseReconciliation.mockReturnValue({
      drifts: [],
      loading: false,
      error: null,
    });

    const { container } = render(<ReconciliationDriftPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders chip with correct drift count", () => {
    mockUseReconciliation.mockReturnValue({
      drifts: [
        {
          symbol: "RELIANCE.NS",
          consecutive_runs: 1,
          last_diff: { our_qty: 50, broker_qty: 100, diff: 50 },
          first_seen_at: null,
          resolved_at: null,
        },
        {
          symbol: "INFY.NS",
          consecutive_runs: 2,
          last_diff: { our_qty: 20, broker_qty: 30, diff: 10 },
          first_seen_at: null,
          resolved_at: null,
        },
      ],
      loading: false,
      error: null,
    });

    render(<ReconciliationDriftPanel />);
    const chip = screen.getByTestId(
      "reconciliation-drift-chip",
    );
    expect(chip.textContent).toContain("2 position drifts");
  });

  it("expands table on Show click", () => {
    mockUseReconciliation.mockReturnValue({
      drifts: [
        {
          symbol: "WIPRO.NS",
          consecutive_runs: 1,
          last_diff: { our_qty: 10, broker_qty: 20, diff: 10 },
          first_seen_at: null,
          resolved_at: null,
        },
      ],
      loading: false,
      error: null,
    });

    render(<ReconciliationDriftPanel />);
    // Table not visible initially
    expect(
      screen.queryByTestId("reconciliation-drift-table"),
    ).toBeNull();

    // Click Show
    fireEvent.click(
      screen.getByTestId("reconciliation-drift-toggle"),
    );
    expect(
      screen.getByTestId("reconciliation-drift-table"),
    ).toBeTruthy();
    // Symbol appears in the table
    expect(screen.getByText("WIPRO.NS")).toBeTruthy();
  });

  it("applies red chip when any drift has > 3 runs", () => {
    mockUseReconciliation.mockReturnValue({
      drifts: [
        {
          symbol: "RELIANCE.NS",
          consecutive_runs: 4,
          last_diff: { our_qty: 50, broker_qty: 100, diff: 50 },
          first_seen_at: null,
          resolved_at: null,
        },
      ],
      loading: false,
      error: null,
    });

    render(<ReconciliationDriftPanel />);
    const chip = screen.getByTestId("reconciliation-drift-chip");
    // Red chip has bg-rose-600 class
    expect(chip.className).toContain("bg-rose-600");
  });

  it("applies amber chip when all drifts have ≤ 3 runs", () => {
    mockUseReconciliation.mockReturnValue({
      drifts: [
        {
          symbol: "WIPRO.NS",
          consecutive_runs: 3,
          last_diff: { our_qty: 10, broker_qty: 20, diff: 10 },
          first_seen_at: null,
          resolved_at: null,
        },
      ],
      loading: false,
      error: null,
    });

    render(<ReconciliationDriftPanel />);
    const chip = screen.getByTestId("reconciliation-drift-chip");
    // Amber chip has bg-amber-500 class
    expect(chip.className).toContain("bg-amber-500");
  });

  it("auto-clears when loading", () => {
    mockUseReconciliation.mockReturnValue({
      drifts: [],
      loading: true,
      error: null,
    });

    const { container } = render(<ReconciliationDriftPanel />);
    expect(container.firstChild).toBeNull();
  });
});
