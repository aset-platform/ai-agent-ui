// ASETPLTFRM-400 slice 7 — cadence chip on the Backtest results
// panel surfaces the run's bar interval. Daily = slate "Daily";
// intraday (60/300/900) = amber-toned chip with the human label.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { BacktestSummary } from "@/hooks/useBacktestRuns";

import { BacktestSummaryCards } from "../BacktestSummaryCards";

function _summary(overrides: Partial<BacktestSummary> = {}): BacktestSummary {
  return {
    run_id: "00000000-0000-0000-0000-000000000000",
    strategy_id: "00000000-0000-0000-0000-000000000001",
    status: "completed",
    period_start: "2026-04-01",
    period_end: "2026-04-30",
    initial_capital_inr: "100000",
    final_equity_inr: "105000",
    total_pnl_inr: "5000",
    total_pnl_pct: "5",
    total_fees_inr: "100",
    total_trades: 3,
    winning_trades: 2,
    losing_trades: 1,
    win_rate_pct: "66.67",
    max_drawdown_pct: "2.5",
    started_at: "2026-04-30T10:00:00Z",
    completed_at: "2026-04-30T10:00:05Z",
    fee_rates_version: "2026-04-01",
    equity_curve: [],
    trade_list: [],
    error_text: null,
    ...overrides,
  };
}

describe("BacktestSummaryCards — cadence chip", () => {
  afterEach(() => cleanup());

  it("renders Daily chip when interval_sec is undefined (pre-slice-7 runs)", () => {
    render(<BacktestSummaryCards summary={_summary()} />);
    const chip = screen.getByTestId("backtest-cadence-chip");
    expect(chip.textContent).toContain("Daily");
  });

  it("renders Daily chip when interval_sec=86400", () => {
    render(<BacktestSummaryCards summary={_summary({ interval_sec: 86400 })} />);
    expect(screen.getByTestId("backtest-cadence-chip").textContent).toContain(
      "Daily",
    );
  });

  it("renders 15m chip when interval_sec=900", () => {
    render(<BacktestSummaryCards summary={_summary({ interval_sec: 900 })} />);
    expect(screen.getByTestId("backtest-cadence-chip").textContent).toContain(
      "15m",
    );
  });

  it("renders 5m chip when interval_sec=300", () => {
    render(<BacktestSummaryCards summary={_summary({ interval_sec: 300 })} />);
    expect(screen.getByTestId("backtest-cadence-chip").textContent).toContain(
      "5m",
    );
  });

  it("renders 1m chip when interval_sec=60", () => {
    render(<BacktestSummaryCards summary={_summary({ interval_sec: 60 })} />);
    expect(screen.getByTestId("backtest-cadence-chip").textContent).toContain(
      "1m",
    );
  });
});
