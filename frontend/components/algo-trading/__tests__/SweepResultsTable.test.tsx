import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect } from "vitest";
import { SweepResultsTable } from "../SweepResultsTable";
import type { SweepResult } from "@/lib/types/algoSweep";

const fixture: SweepResult = {
  run_id: "sweep-1",
  base_strategy_id: "strat-1",
  swept_field: "cooldown_days",
  swept_values: [3, 7, 14],
  variants: [
    {
      variant_index: 0, swept_value: 3,
      walkforward_run_id: "wf-1",
      avg_pnl_pct: "1.47",
      avg_win_rate_pct: "62.2",
      avg_max_drawdown_pct: "7.91",
      sharpe: "0.311", dsr: "0.41",
      n_trades: 82,
      status: "completed", error_text: null,
    },
    {
      variant_index: 1, swept_value: 7,
      walkforward_run_id: "wf-2",
      avg_pnl_pct: "3.74",
      avg_win_rate_pct: "63.9",
      avg_max_drawdown_pct: "7.63",
      sharpe: "0.648", dsr: "0.62",
      n_trades: 83,
      status: "completed", error_text: null,
    },
    {
      variant_index: 2, swept_value: 14,
      walkforward_run_id: "wf-3",
      avg_pnl_pct: "3.74",
      avg_win_rate_pct: "63.9",
      avg_max_drawdown_pct: "7.63",
      sharpe: "0.648", dsr: "0.62",
      n_trades: 83,
      status: "completed", error_text: null,
    },
  ],
  cross_variant_pbo: "0.328",
  returns_matrix_shape: [122, 3],
  winner_variant_index: 1,
  started_at: "2026-05-24T10:00:00Z",
  completed_at: "2026-05-24T10:15:00Z",
  status: "completed",
};

afterEach(() => cleanup());

describe("SweepResultsTable", () => {
  it("renders rows sorted by Sharpe descending", () => {
    render(<SweepResultsTable run={fixture} />);
    const rows = screen.getAllByTestId(
      /^sweep-results-row-/,
    );
    expect(rows[0].getAttribute("data-testid"))
      .toBe("sweep-results-row-1");
    expect(rows[2].getAttribute("data-testid"))
      .toBe("sweep-results-row-0");
  });

  it("renders promote button when winner exists", () => {
    render(<SweepResultsTable run={fixture} />);
    expect(
      screen.getByTestId("sweep-promote-winner-button"),
    ).toBeDefined();
  });
});
