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

/** Feature-vs-feature comparison — the right operand is itself a
 * feature reference, not a literal. */
function rootWithFeatureComparison(
  leftFeature: string,
  op: string,
  rightFeature: string,
) {
  return {
    type: "if",
    cond: {
      type: "compare",
      op,
      left: { feature: leftFeature },
      right: { feature: rightFeature },
    },
    then: { type: "set_target_weight", weight: 0.1 },
  };
}

describe("ConditionBuilder — unwired feature warning (ASETPLTFRM-469)", () => {
  it("shows a warning when an unwired feature is selected", () => {
    render(
      <ConditionBuilder
        root={rootWithCondition("roce", ">", 15)}
        onChangeRoot={vi.fn()}
      />,
    );
    const warnings = screen.getAllByTestId("algo-cond-unwired-warning");
    expect(warnings.length).toBeGreaterThan(0);
    expect(warnings[0].textContent).toContain("never evaluate true");
  });

  it("shows no warning for a wired feature", () => {
    render(
      <ConditionBuilder
        root={rootWithCondition("rsi_2", "<=", 5)}
        onChangeRoot={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId("algo-cond-unwired-warning"),
    ).toBeNull();
  });

  it("shows a warning when the RIGHT operand (feature-vs-feature) is unwired", () => {
    render(
      <ConditionBuilder
        root={rootWithFeatureComparison("rsi_2", "<", "roce")}
        onChangeRoot={vi.fn()}
      />,
    );
    const warnings = screen.getAllByTestId("algo-cond-unwired-warning");
    expect(warnings.length).toBeGreaterThan(0);
    expect(warnings[0].textContent).toContain("ROCE");
  });

  it("shows no warning when both sides of a feature-vs-feature comparison are wired", () => {
    render(
      <ConditionBuilder
        root={rootWithFeatureComparison("rsi_2", "<", "sma_50")}
        onChangeRoot={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId("algo-cond-unwired-warning"),
    ).toBeNull();
  });
});
