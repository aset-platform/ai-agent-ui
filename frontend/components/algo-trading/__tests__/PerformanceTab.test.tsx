import {
  afterEach, describe, expect, it, vi,
} from "vitest";
import {
  cleanup, fireEvent, render, screen, within,
} from "@testing-library/react";

const swrData: Record<string, unknown> = {
  strategies: [
    { id: "s-live", name: "RSI(2) v5", mode: "live", archived_at: null },
    { id: "s-paper", name: "MACD Cross", mode: "paper", archived_at: null },
    { id: "s-draft", name: "Draft Idea", mode: "draft", archived_at: null },
  ],
  summary: {
    mode: "live",
    window: { start: "2026-06-01", end: "2026-07-01" },
    strategies: [
      {
        strategy_id: "s-live", strategy_name: "RSI(2) v5",
        total_trades: 6, wins: 4, losses: 2, win_rate_pct: 66.7,
        total_pnl_inr: 4210.5,
        total_invested_inr: 45000, total_gain_inr: 49210.5,
        biggest_win: { ticker: "ITC", pnl_inr: 2100, closed_at: "2026-06-30" },
        biggest_loss: { ticker: "SHAILY", pnl_inr: -890, closed_at: "2026-06-25" },
        avg_win_inr: 1350.2, avg_loss_inr: -610.4, profit_pct: 9.36,
        max_drawdown_pct: null,
      },
      // 20 filler rows so pagination (default page size 15) has
      // something to paginate over in the dedicated pagination
      // test below. Kept out of the way of "s-live"-scoped
      // assertions in the other tests (s-live stays first, so it
      // always lands on page 1).
      ...Array.from({ length: 20 }, (_, i) => ({
        strategy_id: `s-filler-${i}`,
        strategy_name: `Filler Strategy ${i}`,
        total_trades: 1, wins: 1, losses: 0, win_rate_pct: 100,
        total_pnl_inr: 10, total_invested_inr: 100,
        total_gain_inr: 110,
        biggest_win: { ticker: "ABC", pnl_inr: 10, closed_at: "2026-06-30" },
        biggest_loss: null,
        avg_win_inr: 10, avg_loss_inr: null, profit_pct: 10,
        max_drawdown_pct: null,
      })),
    ],
    trades: [],
  },
};

vi.mock("swr", () => ({
  default: (key: string) => {
    if (key?.includes("/algo/performance/summary")) {
      return { data: swrData["summary"], error: null, isLoading: false };
    }
    return { data: null, error: null, isLoading: false };
  },
  mutate: vi.fn(),
}));

vi.mock("@/hooks/useStrategies", async () => {
  const actual = await vi.importActual<
    typeof import("@/hooks/useStrategies")
  >("@/hooks/useStrategies");
  return {
    ...actual,
    useStrategies: () => ({
      strategies: swrData["strategies"],
      loading: false,
      error: null,
    }),
  };
});

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }),
}));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { PerformanceTab } from "../PerformanceTab";

afterEach(() => cleanup());

describe("PerformanceTab", () => {
  it("defaults to Live mode and All strategies", () => {
    render(<PerformanceTab />);
    expect(
      screen.getByTestId("performance-mode-live"),
    ).toHaveClass("bg-slate-900");
    expect(
      (screen.getByTestId("performance-strategy-select") as HTMLSelectElement)
        .value,
    ).toBe("all");
  });

  it("scopes the strategy dropdown to live-mode strategies only when Live is selected", () => {
    render(<PerformanceTab />);
    const select = screen.getByTestId(
      "performance-strategy-select",
    ) as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toContain("s-live");
    expect(optionValues).not.toContain("s-paper");
    expect(optionValues).not.toContain("s-draft");
  });

  it("scopes the strategy dropdown to paper+live strategies when Paper is selected", () => {
    render(<PerformanceTab />);
    fireEvent.click(screen.getByTestId("performance-mode-paper"));
    const select = screen.getByTestId(
      "performance-strategy-select",
    ) as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toContain("s-live");
    expect(optionValues).toContain("s-paper");
    expect(optionValues).not.toContain("s-draft");
  });

  it("renders the strategy comparison table from the summary response", () => {
    render(<PerformanceTab />);
    const row = screen.getByTestId("performance-strategy-row-s-live");
    expect(row).toBeDefined();
    // "RSI(2) v5" also appears as a <select> option label — scope
    // the text query to the comparison-table row to avoid a
    // multiple-matches error.
    expect(within(row).getByText("RSI(2) v5")).toBeDefined();
  });

  it("renders Total invested, Total gain, and Profit % (not Profit factor)", () => {
    render(<PerformanceTab />);
    const row = screen.getByTestId("performance-strategy-row-s-live");
    expect(screen.getByText("Total invested")).toBeDefined();
    expect(screen.getByText("Total gain")).toBeDefined();
    expect(screen.getByText("Profit %")).toBeDefined();
    expect(screen.queryByText("Profit factor")).toBeNull();
    expect(within(row).getByText("₹45,000")).toBeDefined();
    expect(within(row).getByText("₹49,211")).toBeDefined();
    expect(within(row).getByText("9.36%")).toBeDefined();
  });

  it("paginates the strategy comparison table at 15 rows/page by default", () => {
    render(<PerformanceTab />);
    // 21 total rows (1 s-live + 20 filler) -> 2 pages at 15/page.
    expect(
      screen.getByTestId("performance-strategy-comparison-page-indicator"),
    ).toHaveTextContent("1 / 2");
    expect(screen.getAllByTestId(/^performance-strategy-row-/)).toHaveLength(
      15,
    );

    fireEvent.click(
      screen.getByTestId("performance-strategy-comparison-next"),
    );
    expect(
      screen.getByTestId("performance-strategy-comparison-page-indicator"),
    ).toHaveTextContent("2 / 2");
    expect(screen.getAllByTestId(/^performance-strategy-row-/)).toHaveLength(
      6,
    );

    fireEvent.change(
      screen.getByTestId("performance-strategy-comparison-page-size"),
      { target: { value: "25" } },
    );
    // Changing page size resets to page 1, and 21 rows fit on one
    // 25-row page.
    expect(
      screen.getByTestId("performance-strategy-comparison-page-indicator"),
    ).toHaveTextContent("1 / 1");
    expect(screen.getAllByTestId(/^performance-strategy-row-/)).toHaveLength(
      21,
    );
  });

  it("hides the Max DD% column for Paper/Live but shows it for Backtest/Walk-forward", () => {
    render(<PerformanceTab />);
    // Default mode is Live — no capital baseline, column hidden.
    expect(screen.queryByText("Max DD%")).toBeNull();

    fireEvent.click(screen.getByTestId("performance-mode-backtest"));
    expect(screen.getByText("Max DD%")).toBeDefined();

    fireEvent.click(screen.getByTestId("performance-mode-walkforward"));
    expect(screen.getByText("Max DD%")).toBeDefined();

    fireEvent.click(screen.getByTestId("performance-mode-paper"));
    expect(screen.queryByText("Max DD%")).toBeNull();
  });

  it("reveals custom date inputs when Custom is selected", () => {
    render(<PerformanceTab />);
    expect(
      screen.queryByTestId("performance-range-start"),
    ).toBeNull();
    fireEvent.click(screen.getByTestId("performance-lookback-custom"));
    expect(
      screen.getByTestId("performance-range-start"),
    ).toBeDefined();
    expect(
      screen.getByTestId("performance-range-end"),
    ).toBeDefined();
  });
});
