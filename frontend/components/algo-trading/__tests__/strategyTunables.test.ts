import { describe, expect, it } from "vitest";

import {
  setByPath,
  walkTunables,
} from "../builder/strategyTunables";

describe("walkTunables", () => {
  it("finds set_target_weight in a then-branch", () => {
    const root = {
      type: "if",
      cond: { type: "hold" },
      then: { type: "set_target_weight", weight: 0.2 },
      else: { type: "hold" },
    };
    const out = walkTunables(root);
    const targets = out.filter((t) => t.kind === "weight");
    expect(targets).toHaveLength(1);
    expect(targets[0].path).toBe("then.weight");
    expect(targets[0].value).toBe(0.2);
  });

  it("finds compare literals with operand-aware labels", () => {
    const root = {
      type: "and",
      operands: [
        {
          type: "compare",
          left: { feature: "rsi" },
          op: "<",
          right: { literal: 30 },
        },
        {
          type: "compare",
          left: { feature: "today_ltp" },
          op: ">",
          right: { feature: "sma_50" },
        },
      ],
    };
    const out = walkTunables(root);
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe("literal");
    expect(out[0].label).toBe("rsi < ?");
    expect(out[0].path).toBe("operands[0].right.literal");
    expect(out[0].value).toBe(30);
  });

  it("finds qty.shares on buy and sell", () => {
    const root = {
      type: "if",
      cond: { type: "hold" },
      then: { type: "buy", qty: { shares: 5 } },
      else: { type: "sell", qty: { shares: 3 } },
    };
    const out = walkTunables(root);
    const buy = out.find((t) => t.label.startsWith("Buy"));
    const sell = out.find((t) => t.label.startsWith("Sell"));
    expect(buy?.path).toBe("then.qty.shares");
    expect(buy?.value).toBe(5);
    expect(sell?.path).toBe("else.qty.shares");
    expect(sell?.value).toBe(3);
  });

  it("walks the full Golden Cross-style tree", () => {
    const root = {
      type: "if",
      cond: {
        type: "and",
        operands: [
          {
            type: "compare",
            left: { feature: "today_ltp" },
            op: ">",
            right: { feature: "sma_50" },
          },
          {
            type: "compare",
            left: { feature: "today_ltp" },
            op: ">",
            right: { feature: "sma_200" },
          },
          {
            type: "compare",
            left: { feature: "golden_cross_days_ago" },
            op: "<=",
            right: { literal: 10 },
          },
        ],
      },
      then: { type: "set_target_weight", weight: 0.2 },
      else: { type: "exit", qty: { all: true } },
    };
    const out = walkTunables(root);
    // Expect: 1 compare-literal (days_ago) + 1 target weight.
    // Exit with qty.all has no shares, so no exit qty tunable.
    expect(out).toHaveLength(2);
    const days = out.find((t) => t.label.includes("days_ago"));
    expect(days?.value).toBe(10);
    expect(days?.path).toBe(
      "cond.operands[2].right.literal",
    );
    const weight = out.find((t) => t.kind === "weight");
    expect(weight?.value).toBe(0.2);
    expect(weight?.path).toBe("then.weight");
  });

  it("skips non-numeric literals (strings, booleans)", () => {
    const root = {
      type: "compare",
      left: { feature: "x" },
      op: "==",
      right: { literal: "value" },
    };
    expect(walkTunables(root)).toHaveLength(0);
  });
});

describe("setByPath", () => {
  it("deep-sets a top-level key", () => {
    const root = { type: "set_target_weight", weight: 0.2 };
    const next = setByPath(root, "weight", 0.15);
    expect(next.weight).toBe(0.15);
    // Original untouched (immutability).
    expect(root.weight).toBe(0.2);
  });

  it("deep-sets through nested objects", () => {
    const root = {
      then: { type: "set_target_weight", weight: 0.2 },
    };
    const next = setByPath(root, "then.weight", 0.15);
    expect((next.then as { weight: number }).weight).toBe(0.15);
    expect((root.then as { weight: number }).weight).toBe(0.2);
  });

  it("deep-sets through array indices", () => {
    const root = {
      operands: [
        {
          type: "compare",
          right: { literal: 10 },
        },
      ],
    };
    const next = setByPath(
      root,
      "operands[0].right.literal",
      5,
    );
    expect(
      (next.operands as Array<{ right: { literal: number } }>)[0]
        .right.literal,
    ).toBe(5);
  });
});
