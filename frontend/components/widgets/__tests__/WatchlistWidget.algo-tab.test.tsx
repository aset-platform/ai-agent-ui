import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { WatchlistWidget } from "../WatchlistWidget";

vi.mock("@/hooks/useAlgoPositions", () => ({
  useAlgoPositions: () => ({
    positions: [],
    asOf: null,
    marketOpen: false,
    isLoading: false,
    error: null,
    mutate: vi.fn(),
  }),
}));

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/hooks/useLtpBatch", () => ({
  useLtpBatch: () => ({ map: {} }),
}));

const BASE_PROPS = {
  data: {
    loading: false,
    error: null,
    value: { tickers: [] },
  },
  selectedTicker: null,
  onSelectTicker: vi.fn(),
  onRefresh: vi.fn(),
  portfolio: [],
  portfolioLoading: false,
} as const;

afterEach(() => cleanup());

describe("WatchlistWidget — algo tab gating", () => {
  it("hides the Algo tab when algoTabEnabled=false", () => {
    render(
      <WatchlistWidget
        {...BASE_PROPS}
        algoTabEnabled={false}
      />,
    );
    expect(
      screen.queryByTestId("dashboard-watchlist-tab-algo"),
    ).toBeNull();
  });

  it("shows the Algo tab when algoTabEnabled=true", () => {
    render(
      <WatchlistWidget
        {...BASE_PROPS}
        algoTabEnabled={true}
      />,
    );
    expect(
      screen.getByTestId("dashboard-watchlist-tab-algo"),
    ).toBeDefined();
  });
});
