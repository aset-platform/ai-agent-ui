import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ConditionBuilder } from "../builder/ConditionBuilder";

afterEach(() => cleanup());

/** Minimal "if (feature op literal) then entry" root — the shape
 * ConditionBuilder's visual editor understands (see
 * astRootToVisualSpec in builder/visualStrategy.ts). */
function rootWithCondition(feature: string, op: string, literal: number) {
  return {
    type: "if",
    cond: {
      type: "compare",
      op,
      left: { feature },
      right: { literal },
    },
    then: { type: "set_target_weight", weight: 0.1 },
  };
}

describe("ConditionBuilder — feature scale caption", () => {
  it("shows the fraction caption for a fraction-scale feature", () => {
    render(
      <ConditionBuilder
        root={rootWithCondition("distance_from_sma50", ">", -3)}
        onChangeRoot={vi.fn()}
      />,
    );
    const captions = screen.getAllByTestId("algo-cond-scale-caption");
    expect(captions.length).toBeGreaterThan(0);
    expect(captions[0].textContent).toContain("0.05 for 5%");
  });

  it("shows the percent caption for a percent-scale feature", () => {
    render(
      <ConditionBuilder
        root={rootWithCondition("nifty_30d_return_pct", ">", -5)}
        onChangeRoot={vi.fn()}
      />,
    );
    const captions = screen.getAllByTestId("algo-cond-scale-caption");
    expect(captions[0].textContent).toContain("enter 5 for 5%");
  });

  it("shows the ratio caption for a ratio-scale feature", () => {
    render(
      <ConditionBuilder
        root={rootWithCondition("beta_to_nifty", "<", 1.2)}
        onChangeRoot={vi.fn()}
      />,
    );
    const captions = screen.getAllByTestId("algo-cond-scale-caption");
    expect(captions[0].textContent).toContain("1.0");
  });

  it("shows no caption for a feature with no scale ambiguity", () => {
    render(
      <ConditionBuilder
        root={rootWithCondition("rsi_2", "<=", 5)}
        onChangeRoot={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId("algo-cond-scale-caption"),
    ).toBeNull();
  });
});
