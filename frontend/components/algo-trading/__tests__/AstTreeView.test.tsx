// frontend/components/algo-trading/__tests__/AstTreeView.test.tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { AstTreeView } from "../builder/AstTreeView";

afterEach(() => cleanup());

describe("AstTreeView", () => {
  it("renders a hold leaf", () => {
    render(<AstTreeView node={{ type: "hold" }} />);
    expect(screen.getByTestId("algo-builder-node-hold").textContent)
      .toContain("hold");
  });

  it("renders a compare node with feature + op + literal", () => {
    render(
      <AstTreeView
        node={{
          type: "compare",
          left: { feature: "rsi" },
          op: "<",
          right: { literal: 30 },
        }}
      />,
    );
    const t = screen.getByTestId("algo-builder-node-compare").textContent ?? "";
    expect(t).toContain("RSI");
    expect(t).toContain("<");
    expect(t).toContain("30");
  });

  it("renders an if/then/else with three branches", () => {
    render(
      <AstTreeView
        node={{
          type: "if",
          cond: {
            type: "compare",
            left: { feature: "rsi" },
            op: "<",
            right: { literal: 30 },
          },
          then: { type: "set_target_weight", weight: 0.10 },
          else: { type: "hold" },
        }}
      />,
    );
    expect(screen.getByTestId("algo-builder-node-if")).toBeTruthy();
    expect(screen.getByTestId("algo-builder-node-compare")).toBeTruthy();
    expect(screen.getByTestId("algo-builder-node-set_target_weight"))
      .toBeTruthy();
    expect(screen.getByTestId("algo-builder-node-hold")).toBeTruthy();
  });

  it("returns null for non-object node", () => {
    const { container } = render(<AstTreeView node={"string" as unknown} />);
    expect(container.firstChild).toBeNull();
  });
});
