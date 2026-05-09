import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

import { BacktestEquityCurve } from "../BacktestEquityCurve";

describe("BacktestEquityCurve", () => {
  it("renders empty state when no points", () => {
    render(
      <BacktestEquityCurve
        points={[]}
        initialCapitalInr="100000"
      />,
    );
    expect(
      screen.queryByTestId("backtest-equity-curve-empty"),
    ).not.toBeNull();
  });

  it("renders chart container when points present", () => {
    render(
      <BacktestEquityCurve
        points={[
          { bar_date: "2026-04-01", equity_inr: "100000" },
          { bar_date: "2026-04-02", equity_inr: "101000" },
        ]}
        initialCapitalInr="100000"
      />,
    );
    expect(
      screen.queryByTestId("backtest-equity-curve"),
    ).not.toBeNull();
  });
});
