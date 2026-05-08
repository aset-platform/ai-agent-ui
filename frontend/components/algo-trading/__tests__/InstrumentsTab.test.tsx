import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const swr = { current: null as unknown };
vi.mock("swr", () => ({
  default: () => ({ data: swr.current, error: null, isLoading: false }),
  mutate: vi.fn(),
}));
vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { InstrumentsTab } from "../InstrumentsTab";

afterEach(() => {
  cleanup();
  swr.current = null;
});

describe("InstrumentsTab", () => {
  it("renders empty state when no instruments", () => {
    swr.current = { rows: [], total: 0, page: 1, page_size: 50 };
    render(<InstrumentsTab />);
    expect(
      screen.getByTestId("algo-instruments-tbody").textContent,
    ).toContain("No instruments");
  });

  it("renders rows when data is present", () => {
    swr.current = {
      rows: [
        {
          instrument_token: 1,
          tradingsymbol: "RELIANCE",
          exchange: "NSE",
          segment: "NSE-EQ",
          lot_size: 1,
          tick_size: 0.05,
          our_ticker: "RELIANCE.NS",
          loaded_at: null,
        },
      ],
      total: 1,
      page: 1,
      page_size: 50,
    };
    render(<InstrumentsTab />);
    expect(
      screen.getByTestId("algo-instruments-tbody").textContent,
    ).toContain("RELIANCE");
  });

  it("Refresh button is enabled by default", () => {
    swr.current = { rows: [], total: 0, page: 1, page_size: 50 };
    render(<InstrumentsTab />);
    const btn = screen.getByTestId(
      "algo-instruments-refresh",
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });
});
