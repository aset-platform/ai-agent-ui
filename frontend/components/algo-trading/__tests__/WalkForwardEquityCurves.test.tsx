import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

import { WalkForwardEquityCurves } from "../WalkForwardEquityCurves";
import type { WindowCurve } from "../WalkForwardEquityCurves";

function makeCurve(
  index: number,
  pointCount: number = 3,
): WindowCurve {
  return {
    windowIndex: index,
    testStart: `2024-0${index + 1}-01`,
    testEnd: `2024-0${index + 1}-30`,
    status: "completed",
    points: Array.from({ length: pointCount }, (_, i) => ({
      bar_date: `2024-0${index + 1}-${String(i + 1).padStart(2, "0")}`,
      equity_inr: String(100_000 + i * 1_000),
    })),
  };
}

describe("WalkForwardEquityCurves", () => {
  afterEach(() => cleanup());

  it("renders empty state when no curves provided", () => {
    render(
      <WalkForwardEquityCurves
        curves={[]}
        initialCapitalInr="100000"
      />,
    );
    expect(
      screen.queryByTestId("walkforward-curves-empty"),
    ).not.toBeNull();
  });

  it("renders chart container when curves are provided", () => {
    const curves = [makeCurve(0), makeCurve(1), makeCurve(2)];
    render(
      <WalkForwardEquityCurves
        curves={curves}
        initialCapitalInr="100000"
      />,
    );
    expect(
      screen.queryByTestId("walkforward-curves"),
    ).not.toBeNull();
    expect(
      screen.queryByTestId("walkforward-curves-empty"),
    ).toBeNull();
  });

  it("shows window count in subtitle for N=3", () => {
    const curves = [makeCurve(0), makeCurve(1), makeCurve(2)];
    render(
      <WalkForwardEquityCurves
        curves={curves}
        initialCapitalInr="100000"
      />,
    );
    const container = screen.getByTestId("walkforward-curves");
    expect(container.textContent).toContain("3 windows");
  });

  it("uses singular 'window' for N=1", () => {
    render(
      <WalkForwardEquityCurves
        curves={[makeCurve(0)]}
        initialCapitalInr="100000"
      />,
    );
    const container = screen.getByTestId("walkforward-curves");
    expect(container.textContent).toContain("1 window");
    expect(container.textContent).not.toContain("1 windows");
  });

  it("renders chart for N=23 curves without crashing", () => {
    const curves = Array.from({ length: 23 }, (_, i) =>
      makeCurve(i % 9),
    );
    render(
      <WalkForwardEquityCurves
        curves={curves}
        initialCapitalInr="100000"
      />,
    );
    expect(
      screen.queryByTestId("walkforward-curves"),
    ).not.toBeNull();
  });
});
