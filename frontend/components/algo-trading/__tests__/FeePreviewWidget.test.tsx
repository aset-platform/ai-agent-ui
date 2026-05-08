// frontend/components/algo-trading/__tests__/FeePreviewWidget.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

afterEach(() => cleanup());

vi.mock("swr", () => ({
  default: (key: string | null) => {
    if (!key) return { data: null, error: null, isLoading: false };
    return {
      data: {
        brokerage_inr: "0.00",
        stt_inr: "29.45",
        exchange_txn_inr: "0.87",
        sebi_inr: "0.03",
        stamp_duty_inr: "4.42",
        gst_inr: "0.16",
        dp_charges_inr: "0.00",
        total_inr: "34.93",
        rates_version: "2026-04-01",
      },
      error: null,
      isLoading: false,
    };
  },
}));

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/lib/config", () => ({
  API_URL: "http://test/api",
}));

import { FeePreviewWidget } from "../FeePreviewWidget";

describe("FeePreviewWidget", () => {
  it("renders the widget with default values", () => {
    render(<FeePreviewWidget />);
    expect(screen.getByTestId("algo-fee-preview")).toBeTruthy();
    expect(screen.getByTestId("algo-fee-symbol")).toBeTruthy();
  });

  it("renders the breakdown grid when value is present", async () => {
    render(<FeePreviewWidget />);
    await waitFor(() => {
      const breakdown = screen.getByTestId("algo-fee-breakdown");
      expect(breakdown.textContent).toContain("Brokerage");
      expect(breakdown.textContent).toContain("Total");
      expect(breakdown.textContent).toContain("₹34.93");
    });
  });

  it("respects user-changed qty input", () => {
    render(<FeePreviewWidget />);
    const qty = screen.getByTestId("algo-fee-qty") as HTMLInputElement;
    fireEvent.change(qty, { target: { value: "100" } });
    expect(qty.value).toBe("100");
  });

  it("changes side when select is changed", () => {
    render(<FeePreviewWidget />);
    const side = screen.getByTestId("algo-fee-side") as HTMLSelectElement;
    fireEvent.change(side, { target: { value: "SELL" } });
    expect(side.value).toBe("SELL");
  });
});
